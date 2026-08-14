"""폴더를 스캔하여 엑셀/PDF 내용을 색인하고 검색을 제공하는 모듈.

내용 검색 대상 파일은 mtime+size 를 캐시에 저장해 두어, 변경되지 않은 파일은
재파싱하지 않는다. 파일명만 색인하는(filename_only) 폴더는 재파싱할 내용 자체가
없어 stat 비교가 아무 이득이 없으므로 mtime/size 를 저장하지도, os.stat() 을
호출하지도 않는다 (os.scandir 순회만으로 충분 — DirEntry 는 Windows에서
FindFirstFile/FindNextFile 결과를 이미 들고 있어 is_dir()/is_symlink() 가
추가 시스템 콜 없이 끝난다).
"""
import json
import os
import threading
from typing import Callable, Dict, List, Optional

from config import CACHE_PATH
from parsers import EXTRACTORS

MAX_RESULTS = 5000  # 결과 목록이 가상화되어 있어(보이는 행만 그림) 이 정도는 가볍게 처리된다
SNIPPET_RADIUS = 220
MAX_SNIPPET_LEN = 480


def _is_under(path_norm: str, root_norm: str) -> bool:
    """path_norm 이 root_norm 자신이거나 그 아래에 있는지 (둘 다 normcase+normpath 된 값).
    드라이브 루트("C:\\")는 os.path.normpath 가 이미 끝에 구분자를 붙여서 돌려주는데
    반해 일반 폴더("C:\\Users\\sol")는 안 붙인다 — 여기서 무조건 os.sep 를 한 번 더
    붙이면 드라이브 루트일 때 "C:\\\\" 처럼 구분자가 두 번 겹쳐서 그 밑의 어떤 경로와도
    매칭이 안 되는 버그가 생긴다(이미 구분자로 끝나 있으면 추가하지 않아야 함)."""
    if path_norm == root_norm:
        return True
    prefix = root_norm if root_norm.endswith(os.sep) else root_norm + os.sep
    return path_norm.startswith(prefix)


class Indexer:
    def __init__(self):
        self._lock = threading.Lock()
        # file_path -> {"entries": [...], "content_indexed": bool} (내용 색인 시 "mtime"/"size" 도 포함)
        self._files: Dict[str, dict] = {}
        # dir_path -> {} (filename_only 폴더에서만 채워짐, 존재 여부만 의미가 있음)
        self._dirs: Dict[str, dict] = {}
        self._load_cache()

    # ---------- 캐시 ----------
    def _load_cache(self):
        if not os.path.exists(CACHE_PATH):
            return
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return
        if "files" in data or "dirs" in data:
            self._files = data.get("files", {})
            self._dirs = data.get("dirs", {})
        else:
            self._files = data  # 예전 캐시 형식(파일만 저장) 호환

    def _save_cache(self):
        try:
            with open(CACHE_PATH, "w", encoding="utf-8") as f:
                json.dump({"files": self._files, "dirs": self._dirs}, f, ensure_ascii=False)
        except OSError:
            pass

    # ---------- 색인 ----------
    def rebuild(self, folder_modes: List[tuple], progress: Optional[Callable[[str], None]] = None,
                cancel_check: Optional[Callable[[], bool]] = None):
        """folder_modes: (폴더 경로, filename_only) 목록.
        filename_only 폴더는 확장자 제한 없이 모든 파일명을 대상으로 하고, 내용은 파싱하지
        않는다(엑셀/PDF를 열어 읽는 과정을 통째로 건너뛰므로 색인이 훨씬 빠르다 — 파일명
        매핑만 미리 만들어 두는 셈). 하위 폴더 이름도 함께 기록해 폴더명 검색에 쓴다.
        내용 검색 폴더는 기존처럼 xlsx/pdf만 파싱한다.

        os.walk 대신 os.scandir 를 명시적 스택으로 직접 순회한다: os.walk 는 이미 읽어온
        디렉터리 엔트리(FindFirstFile/FindNextFile 결과)를 파일명 문자열로만 넘겨줘서,
        내용 검색이 필요 없는 filename_only 폴더에서도 mtime/size 비교를 위해 os.stat()을
        다시 호출하게 되는데, 이게 전체 색인 시간의 대부분(프로파일링 기준 65%)을 차지했다.
        DirEntry 를 직접 쓰면 그 정보가 이미 있으므로 재요청이 필요 없다. 재귀 대신 스택을
        쓰는 이유는 아주 깊은 폴더 트리(예: node_modules류)에서 재귀 깊이 제한에 걸리지
        않기 위해서다."""
        found_paths = set()
        found_dirs = set()
        scanned_roots = []  # 이번 호출에서 실제로 스캔을 시작한 폴더 (stale 정리 범위 제한용)
        denied_paths = []   # 접근거부 등으로 열거 자체를 못한 하위 경로 (stale 정리에서 제외)

        # 같은 폴더를 "파일명만"과 "내용까지" 두 가지 방식으로 각각 등록할 수 있는데
        # (설정 화면에서 같은 경로를 두 번 추가), self._files 는 경로 하나당 항목 하나뿐이라
        # 처리 순서에 따라 filename_only 쪽이 나중에 돌면서 방금 파싱한 내용을 빈 값으로
        # 덮어써버릴 수 있다. 내용 모드로도 덮이는 경로 범위를 미리 알아두고, filename_only
        # 처리에서는 그 범위를 건드리지 않는다(내용 모드 쪽이 알아서 채워 넣는다).
        content_mode_roots = [
            os.path.normcase(os.path.normpath(f)) for f, fo in folder_modes
            if not fo and os.path.isdir(f)
        ]

        for folder, filename_only in folder_modes:
            if not os.path.isdir(folder):
                continue
            scanned_roots.append(os.path.normcase(os.path.normpath(folder)))

            stack = [folder]
            while stack:
                if cancel_check and cancel_check():
                    return
                current = stack.pop()
                try:
                    entries_iter = list(os.scandir(current))
                except OSError:
                    # 권한 없음 등으로 이 디렉터리 안을 못 봤으니, 이 하위 트리는 stale
                    # 정리 대상에서 빼야 한다 — 안 그러면 실제론 그대로 있는 파일이
                    # "이번에 못 찾았다"는 이유만으로 캐시에서 삭제돼버린다.
                    denied_paths.append(os.path.normcase(os.path.normpath(current)))
                    continue

                for entry in entries_iter:
                    if cancel_check and cancel_check():
                        return
                    try:
                        is_dir = entry.is_dir()  # follow_symlinks=True(기본): 링크로 연결된 폴더도 목록엔 포함
                    except OSError:
                        continue

                    if is_dir:
                        dpath = entry.path
                        if filename_only:
                            found_dirs.add(dpath)
                            if dpath not in self._dirs:
                                with self._lock:
                                    self._dirs[dpath] = {}
                        try:
                            is_link = entry.is_symlink()
                        except OSError:
                            is_link = False
                        if not is_link:  # os.walk 의 기본 followlinks=False 와 동일하게, 링크 안으로는 재귀 안 함
                            stack.append(dpath)
                        continue

                    name = entry.name
                    ext = os.path.splitext(name)[1].lower()
                    if not filename_only and ext not in EXTRACTORS:
                        continue
                    path = entry.path
                    found_paths.add(path)

                    cached = self._files.get(path)
                    has_content = cached is not None and cached.get("content_indexed", True)  # 예전 캐시 호환: 키 없으면 내용 있다고 간주

                    if filename_only:
                        if content_mode_roots:
                            path_norm = os.path.normcase(os.path.normpath(path))
                            if any(_is_under(path_norm, r) for r in content_mode_roots):
                                continue  # 이 파일은 다른 등록이 내용까지 색인하므로 여기선 손대지 않는다
                        if cached is not None and not has_content:
                            continue  # 이미 파일명만으로 색인됨 - stat 자체가 필요 없다
                        with self._lock:
                            self._files[path] = {"entries": [], "content_indexed": False}
                        continue

                    # 내용 검색 폴더: entry.stat() 은 scandir 결과에 이미 있는 걸 재사용하는
                    # 것이라 os.stat(path) 와 달리 추가 시스템 콜이 들지 않는다.
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue
                    old_entries = cached.get("entries") if cached else None
                    # PDF 페이지/엑셀 시트 이동 기능이 추가되기 전에 색인된 캐시는
                    # entry 안에 "page"/"sheet" 키가 없다. mtime/size 만 보고 "안 바뀌었으니
                    # 재파싱 안 해도 됨"이라 판단하면, 파일이 실제로 안 바뀐 한 그 구버전
                    # 데이터가 영원히 재사용돼서 페이지 이동이 계속 안 되는 채로 남는다 —
                    # 새 entry에 있어야 할 키가 없으면 최신 스키마가 아니라고 보고 다시 파싱한다.
                    schema_key = "page" if ext == ".pdf" else "sheet"
                    has_current_schema = not old_entries or schema_key in old_entries[0]
                    if (has_content and has_current_schema
                            and cached.get("mtime") == stat.st_mtime
                            and cached.get("size") == stat.st_size):
                        continue  # 변경 없고 이미 최신 스키마로 내용까지 색인됨

                    if progress:
                        progress(name)
                    entries = EXTRACTORS[ext](path) if ext in EXTRACTORS else []
                    with self._lock:
                        self._files[path] = {
                            "mtime": stat.st_mtime,
                            "size": stat.st_size,
                            "entries": entries,
                            "content_indexed": True,
                        }

        # 삭제된 파일/폴더 정리: 이번에 실제로 스캔에 성공한 루트 범위 안에서만 지운다
        # (접근거부로 못 본 하위 트리는 범위에서 제외 — 못 찾은 걸 삭제된 걸로 오인하지 않게).
        def _under_any(p: str, roots: list) -> bool:
            pn = os.path.normcase(os.path.normpath(p))
            return any(_is_under(pn, r) for r in roots)

        with self._lock:
            stale = [
                p for p in self._files
                if p not in found_paths and _under_any(p, scanned_roots) and not _under_any(p, denied_paths)
            ]
            for p in stale:
                del self._files[p]
            stale_dirs = [
                d for d in self._dirs
                if d not in found_dirs and _under_any(d, scanned_roots) and not _under_any(d, denied_paths)
            ]
            for d in stale_dirs:
                del self._dirs[d]

        self._save_cache()

    # ---------- 검색 ----------
    def search(self, query: str, limit: int = MAX_RESULTS,
               folder_modes: Optional[List[tuple]] = None) -> List[dict]:
        """folder_modes 가 주어지면(None 이 아니면) 그 폴더들 아래 파일에서만 검색한다
        (검색창 하단의 폴더 포함/제외 토글용). 각 항목은 (폴더 경로, filename_only) 튜플이며,
        폴더별로 내용 검색/파일명만 검색을 다르게 지정할 수 있다.
        None 이면 폴더 제한 없이 전체를 내용 검색한다.
        검색어에 공백으로 여러 단어를 넣으면 전부 포함하는 것만 찾는다(순서/인접 무관) —
        이미 검색한 결과에 단어를 더 입력해서 계속 좁혀나갈 수 있다(결과 안에서 추가 검색)."""
        query = query.strip()
        if not query:
            return []
        terms = [t for t in query.lower().split() if t]
        if not terms:
            return []
        q_lower = terms[0]  # 스니펫 위치는 첫 검색어 기준으로 잡는다
        results = []
        with self._lock:
            items = list(self._files.items())
            dir_items = list(self._dirs.keys())

        allowed_norm = None
        if folder_modes is not None:
            allowed_norm = [(os.path.normcase(os.path.normpath(f)), bool(fo)) for f, fo in folder_modes]

        def _matched_folders(p_norm: str):
            """p_norm 이 속하는 모든 (폴더_norm, filename_only) 를 반환한다(여러 개 가능 —
            같은 경로를 파일명만/내용까지 두 가지로 각각 등록했을 수 있다). 예전엔 첫
            매치 하나만 채택했는데, 그러면 둘 다 등록된 파일은 먼저 나온 등록의 모드로만
            취급돼서 다른 쪽 모드의 검색 결과가 아예 안 나오는 문제가 있었다."""
            if allowed_norm is None:
                return [("", False)]  # 폴더 제한 없음 = 내용 검색으로 취급
            return [(folder_norm, fo) for folder_norm, fo in allowed_norm if _is_under(p_norm, folder_norm)]

        # ---- 폴더명 매칭: filename_only 폴더에서만, 폴더 자체를 결과로 보여준다 ----
        for dpath in dir_items:
            dpath_norm = os.path.normcase(os.path.normpath(dpath))
            matches = _matched_folders(dpath_norm)
            if not any(fo for _, fo in matches):
                continue
            name = os.path.basename(dpath)
            name_lower = name.lower()
            if all(t in name_lower for t in terms):
                results.append({
                    "path": dpath,
                    "name": name,
                    "location": "폴더명 일치",
                    "snippet": "",
                    "is_dir": True,
                })
                if len(results) >= limit:
                    return results

        # ---- 파일 매칭 ----
        for path, data in items:
            path_norm = os.path.normcase(os.path.normpath(path))
            matches = _matched_folders(path_norm)
            if not matches:
                continue
            wants_filename = any(fo for _, fo in matches)
            wants_content = any(not fo for _, fo in matches)

            content_hits = []
            if wants_content:
                for entry in data.get("entries", []):
                    text = entry["text"]
                    text_lower = text.lower()
                    if not all(t in text_lower for t in terms):
                        continue
                    idx = text_lower.find(q_lower)
                    start = max(0, idx - SNIPPET_RADIUS)
                    end = min(len(text), idx + len(q_lower) + SNIPPET_RADIUS, start + MAX_SNIPPET_LEN)
                    snippet = text[start:end]
                    if start > 0:
                        snippet = "…" + snippet
                    if end < len(text):
                        snippet = snippet + "…"
                    result = {
                        "path": path,
                        "name": os.path.basename(path),
                        "location": entry["location"],
                        "snippet": snippet,
                    }
                    # 더블클릭 시 파일을 열면서 검색된 위치(엑셀 시트/행, PDF 페이지)로
                    # 바로 이동할 수 있도록, 있으면 같이 넘긴다.
                    if "sheet" in entry:
                        result["sheet"] = entry["sheet"]
                        result["row"] = entry["row"]
                    if "page" in entry:
                        result["page"] = entry["page"]
                    content_hits.append(result)

            if content_hits:
                # 같은 파일이 파일명 등록과 내용 등록 둘 다에 걸리면("폴더 두 번 추가")
                # 내용 일치 쪽이 정확한 위치+미리보기까지 주는 상위 정보라, 굳이 "파일명
                # 일치"를 따로 또 보여주지 않는다 — 그러면 같은 파일이 결과에 여러 번
                # 나와서 아무거나 두 개 열면 파일이 두 번 열리는 것처럼 보인다.
                for result in content_hits:
                    results.append(result)
                    if len(results) >= limit:
                        return results
            elif wants_filename:
                name = os.path.basename(path)
                name_lower = name.lower()
                if all(t in name_lower for t in terms):
                    results.append({
                        "path": path,
                        "name": name,
                        "location": "파일명 일치",
                        "snippet": "",
                        "is_dir": False,
                    })
                    if len(results) >= limit:
                        return results
        return results

    def file_count(self) -> int:
        with self._lock:
            return len(self._files)
