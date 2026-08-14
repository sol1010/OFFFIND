"""폴더를 스캔하여 엑셀/PDF 내용을 색인하고 검색을 제공하는 모듈.

파일의 mtime+size 를 캐시에 저장해 두어, 변경되지 않은 파일은 재파싱하지 않는다.
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


class Indexer:
    def __init__(self):
        self._lock = threading.Lock()
        # file_path -> {"mtime": float, "size": int, "entries": [{"location", "text"}]}
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
        내용 검색 폴더는 기존처럼 xlsx/pdf만 파싱한다."""
        found_paths = set()
        found_dirs = set()

        for folder, filename_only in folder_modes:
            if not os.path.isdir(folder):
                continue
            for root, dir_names, files in os.walk(folder):
                if filename_only:
                    for d in dir_names:
                        dpath = os.path.join(root, d)
                        found_dirs.add(dpath)
                        if dpath not in self._dirs:
                            with self._lock:
                                self._dirs[dpath] = {}

                for name in files:
                    ext = os.path.splitext(name)[1].lower()
                    if not filename_only and ext not in EXTRACTORS:
                        continue
                    path = os.path.join(root, name)
                    found_paths.add(path)
                    if cancel_check and cancel_check():
                        return
                    try:
                        stat = os.stat(path)
                    except OSError:
                        continue

                    cached = self._files.get(path)
                    same_stat = cached and cached.get("mtime") == stat.st_mtime and cached.get("size") == stat.st_size
                    has_content = cached and cached.get("content_indexed", True)  # 예전 캐시 호환: 키 없으면 내용 있다고 간주
                    if same_stat and (filename_only or has_content):
                        continue  # 변경 없고, 지금 필요한 수준(파일명만/내용)까지 이미 색인됨

                    if filename_only:
                        entries = []
                    else:
                        if progress:
                            progress(name)
                        entries = EXTRACTORS[ext](path) if ext in EXTRACTORS else []

                    with self._lock:
                        self._files[path] = {
                            "mtime": stat.st_mtime,
                            "size": stat.st_size,
                            "entries": entries,
                            "content_indexed": not filename_only,
                        }

        # 삭제된 파일/폴더 정리 (filename_only 로 색인된 폴더 범위 안에서만)
        filename_only_roots = [
            os.path.normcase(os.path.normpath(f)) for f, fo in folder_modes if fo
        ]

        def _under_reindexed_root(p: str) -> bool:
            pn = os.path.normcase(os.path.normpath(p))
            return any(pn == r or pn.startswith(r + os.sep) for r in filename_only_roots)

        with self._lock:
            stale = [p for p in self._files if p not in found_paths]
            for p in stale:
                del self._files[p]
            stale_dirs = [d for d in self._dirs if d not in found_dirs and _under_reindexed_root(d)]
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

        def _match_folder(p_norm: str):
            """p_norm 이 속한 (폴더_norm, filename_only) 를 찾는다. 없으면 None."""
            if allowed_norm is None:
                return ("", False)  # 폴더 제한 없음 = 내용 검색으로 취급
            for folder_norm, fo in allowed_norm:
                if p_norm == folder_norm or p_norm.startswith(folder_norm + os.sep):
                    return (folder_norm, fo)
            return None

        # ---- 폴더명 매칭: filename_only 폴더에서만, 폴더 자체를 결과로 보여준다 ----
        for dpath in dir_items:
            dpath_norm = os.path.normcase(os.path.normpath(dpath))
            match = _match_folder(dpath_norm)
            if match is None or not match[1]:
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
            match = _match_folder(path_norm)
            if match is None:
                continue
            filename_only = match[1]

            if filename_only:
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
                continue

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
                results.append(result)
                if len(results) >= limit:
                    return results
        return results

    def file_count(self) -> int:
        with self._lock:
            return len(self._files)
