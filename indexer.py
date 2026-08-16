"""폴더를 스캔하여 엑셀/PDF 내용을 색인하고 검색을 제공하는 모듈.

파일명·폴더명·내용(엑셀 행/PDF 페이지) 전부 SQLite + FTS5(trigram 토크나이저)로
색인한다. 예전엔 파일 61만 개, 내용 항목 135만 개를 매 키 입력마다 Python 레벨에서
전부 훑었는데(검색 한 번에 3~4초 이상), trigram 인덱스를 쓰면 3글자 이상 검색어는
실측 수십 ms 안팎으로 끝난다. trigram은 구조상 3글자 미만 질의를 인덱싱하지 못하므로
(3글자 미만은 트라이그램 자체가 안 생김) 2글자 이하는 LIKE 로 폴백한다 — 그래도
SQLite C 엔진이라 Python 반복문보다 빠르다.
"""
import json
import os
import sqlite3
import threading
import time
from typing import Callable, Dict, List, Optional

from config import CACHE_PATH, CACHE_DB_PATH
from parsers import EXTRACTORS

MAX_RESULTS = 5000  # 결과 목록이 가상화되어 있어(보이는 행만 그림) 이 정도는 가볍게 처리된다
SNIPPET_RADIUS = 220
MAX_SNIPPET_LEN = 480

# 트리거/FTS5 가상 테이블을 만들기 "전에" 원본 테이블에 대량으로 넣어야 하는 경우가
# 있어서(최초 JSON 마이그레이션 — 수십만~100만 건을 한 건씩 트리거로 FTS 색인하면
# 몇 분씩 걸린다, 실측으로 확인함) 스키마를 세 조각으로 나눠 둔다. 평소(이미 DB가
# 있는 정상 실행)엔 그냥 합쳐서 쓴다.
BASE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    path_norm TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    is_dir INTEGER NOT NULL,
    content_indexed INTEGER NOT NULL DEFAULT 0,
    entries_schema INTEGER NOT NULL DEFAULT 0,
    mtime REAL,
    size INTEGER
);
-- 동일성 판단은 항상 path_norm(정규화된 경로) 기준이어야 한다: 같은 물리적 파일도
-- 어느 등록 루트를 거쳐 스캔됐는지에 따라 os.scandir 가 만드는 path 문자열의
-- 구분자 표기(슬래시/백슬래시)가 달라질 수 있다(예: "C:/" 루트로 스캔한 파일은
-- "C:/Users\\sol\\..." 인데, "C:/Users/sol/Downloads" 루트로 스캔한 같은 파일은
-- "C:/Users/sol/Downloads\\..." 가 됨 — 둘 다 os.path.normpath 를 거치면 같아진다).
-- path 자체를 UNIQUE 로 쓰면 이 표기 차이 때문에 같은 파일이 두 행으로 중복
-- 색인되고, 검색 결과 dedup(파일명+내용 둘 다 걸린 파일은 내용 쪽만 보여주기)이
-- 깨진다(실측으로 발견함 — 파일명 일치/내용 일치가 같은 파일인데 따로 나왔었음).
CREATE UNIQUE INDEX IF NOT EXISTS idx_files_path_norm ON files(path_norm);

CREATE TABLE IF NOT EXISTS content_entries (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    location TEXT NOT NULL,
    text TEXT NOT NULL,
    page INTEGER,
    sheet TEXT,
    row_num INTEGER,
    paragraph INTEGER,
    slide INTEGER
);
CREATE INDEX IF NOT EXISTS idx_content_entries_file_id ON content_entries(file_id);
"""

_CONTENT_ENTRIES_EXTRA_COLUMNS = {"paragraph": "INTEGER", "slide": "INTEGER"}


def _ensure_content_columns(con: sqlite3.Connection):
    """Word/PowerPoint 지원을 나중에 추가하면서 content_entries 에 paragraph/slide
    컬럼이 새로 생겼는데, CREATE TABLE IF NOT EXISTS 는 이미 만들어진(예전 버전이
    만든) 테이블엔 새 컬럼을 안 붙여준다 — 있는지 확인해서 없으면 그때 추가한다."""
    cols = {row[1] for row in con.execute("PRAGMA table_info(content_entries)")}
    for col, coltype in _CONTENT_ENTRIES_EXTRA_COLUMNS.items():
        if col not in cols:
            con.execute(f"ALTER TABLE content_entries ADD COLUMN {col} {coltype}")

FTS_SCHEMA_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(
    name, content='files', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS files_ai AFTER INSERT ON files BEGIN
    INSERT INTO files_fts(rowid, name) VALUES (new.id, new.name);
END;
CREATE TRIGGER IF NOT EXISTS files_ad AFTER DELETE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name) VALUES ('delete', old.id, old.name);
END;
CREATE TRIGGER IF NOT EXISTS files_au AFTER UPDATE ON files BEGIN
    INSERT INTO files_fts(files_fts, rowid, name) VALUES ('delete', old.id, old.name);
    INSERT INTO files_fts(rowid, name) VALUES (new.id, new.name);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS content_fts USING fts5(
    text, content='content_entries', content_rowid='id', tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS content_ai AFTER INSERT ON content_entries BEGIN
    INSERT INTO content_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS content_ad AFTER DELETE ON content_entries BEGIN
    INSERT INTO content_fts(content_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""

SCHEMA_SQL = BASE_SCHEMA_SQL + FTS_SCHEMA_SQL

_UPSERT_FILE_SQL = """
INSERT INTO files (path, path_norm, name, is_dir, content_indexed, entries_schema, mtime, size)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(path_norm) DO UPDATE SET
    path=excluded.path, name=excluded.name, is_dir=excluded.is_dir,
    content_indexed=excluded.content_indexed, entries_schema=excluded.entries_schema,
    mtime=excluded.mtime, size=excluded.size
"""


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


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(CACHE_DB_PATH)
    con.executescript(SCHEMA_SQL)
    _ensure_content_columns(con)
    con.execute("PRAGMA journal_mode=WAL")  # 백그라운드 색인(쓰기)과 검색(읽기)이 서로 안 막게
    con.execute("PRAGMA foreign_keys=ON")   # 파일 삭제 시 content_entries 도 같이 지워지게(CASCADE)
    return con


def _term_search_sql(table: str, text_col: str, fts_table: str, select_cols: str,
                      con: sqlite3.Connection, terms: List[str], extra_where: str = "",
                      extra_params: tuple = (), sql_limit: int = 3000):
    """terms 전부가 text_col 에 부분일치하는 행을 반환한다. 전부 3글자 이상이면
    FTS5(trigram) MATCH — 인덱스를 타서 매칭되는 것만 바로 찾는다. 하나라도 2글자
    이하면 trigram 인덱스가 토큰 자체를 안 만들어서 매칭이 안 되므로 LIKE 로
    폴백한다(전량 스캔이지만 SQLite C 엔진이라 Python 반복문보다 빠르다).

    SQL에 LIMIT을 반드시 걸어야 한다 — 예전엔 없어서, "a"처럼 흔한 1~2글자
    검색어가 파일 70만+ 개 중 수만 건에 매칭되면 그 전부를 fetchall()로 끌어온
    다음에야 Python 쪽 limit(표시 500개)이 적용됐다. 결과가 많이 매칭될수록
    화면엔 어차피 "더 구체적으로 입력하라"는 안내만 보여주면서 검색이 6초 넘게
    걸리는 원인이었다(실측으로 확인함). LIMIT이 있으면 SQLite가 그만큼 찾자마자
    스캔을 멈춘다. sql_limit은 최종 표시 개수보다 넉넉히 크게 잡아서(기본 3000),
    이후 폴더 범위 필터링/내용-파일명 중복 제거에 쓸 후보가 부족해지지 않게 한다."""
    where = f" AND {extra_where}" if extra_where else ""
    if all(len(t) >= 3 for t in terms):
        match_expr = " ".join('"' + t.replace('"', '""') + '"' for t in terms)
        try:
            return con.execute(
                f"""
                SELECT {select_cols} FROM {fts_table}
                JOIN {table} t ON t.id = {fts_table}.rowid
                WHERE {fts_table} MATCH ?{where}
                LIMIT ?
                """,
                (match_expr, *extra_params, sql_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            pass  # 검색어에 FTS5 구문을 깨는 문자가 있으면 LIKE 로 안전하게 폴백
    conds = " AND ".join([f"t.{text_col} LIKE ?"] * len(terms))
    params = [f"%{t}%" for t in terms]
    return con.execute(
        f"SELECT {select_cols} FROM {table} t WHERE {conds}{where} LIMIT ?",
        [*params, *extra_params, sql_limit],
    ).fetchall()


def _content_search_sql(con: sqlite3.Connection, terms: List[str], sql_limit: int = 3000):
    """terms 전부가 내용 텍스트에 부분일치하는 (path, path_norm, name, location, text,
    page, sheet, row_num, paragraph, slide) 행을 반환한다. content_entries 는 files 와
    별도 테이블이라 files 를 조인해야 하므로 _term_search_sql 의 단일 테이블 가정을
    그대로 못 쓴다. sql_limit 이유는 _term_search_sql 문서 참고 — content_entries는
    행 하나하나가 문장 전체라 LIKE 스캔 비용이 더 크다."""
    select_cols = ("f.path, f.path_norm, f.name, ce.location, ce.text, "
                   "ce.page, ce.sheet, ce.row_num, ce.paragraph, ce.slide")
    if all(len(t) >= 3 for t in terms):
        match_expr = " ".join('"' + t.replace('"', '""') + '"' for t in terms)
        try:
            return con.execute(
                f"""
                SELECT {select_cols}
                FROM content_fts
                JOIN content_entries ce ON ce.id = content_fts.rowid
                JOIN files f ON f.id = ce.file_id
                WHERE content_fts MATCH ?
                LIMIT ?
                """,
                (match_expr, sql_limit),
            ).fetchall()
        except sqlite3.OperationalError:
            pass  # 검색어에 FTS5 구문을 깨는 문자가 있으면 LIKE 로 안전하게 폴백
    conds = " AND ".join(["ce.text LIKE ?"] * len(terms))
    params = [f"%{t}%" for t in terms]
    return con.execute(
        f"""
        SELECT {select_cols}
        FROM content_entries ce JOIN files f ON f.id = ce.file_id
        WHERE {conds}
        LIMIT ?
        """,
        [*params, sql_limit],
    ).fetchall()


_INSERT_FILE_RETURNING_ID_SQL = """
INSERT INTO files (path, path_norm, name, is_dir, content_indexed, entries_schema, mtime, size)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(path_norm) DO UPDATE SET
    path=excluded.path, name=excluded.name, is_dir=excluded.is_dir,
    content_indexed=excluded.content_indexed, entries_schema=excluded.entries_schema,
    mtime=excluded.mtime, size=excluded.size
RETURNING id
"""


class Indexer:
    def __init__(self):
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(CACHE_DB_PATH), exist_ok=True)

        if not os.path.exists(CACHE_DB_PATH) and os.path.exists(CACHE_PATH):
            self._migrate_json_cache()

        con = _connect()
        try:
            # rebuild() 가 파일마다 "이미 색인됐는지" 판단할 때 SQLite 왕복 없이 바로
            # 확인할 수 있도록, 가벼운 메타데이터만(내용 텍스트는 빼고) 메모리에 올려
            # 둔다 — 내용 자체는 이제 content_fts 인덱스로 바로 검색하므로 메모리에
            # 따로 들고 있을 필요가 없다.
            # path_norm(정규화된 경로)을 키로 쓴다 — 같은 물리적 파일이 등록 루트에
            # 따라 다른 구분자 표기의 path 문자열로 발견될 수 있어서, "이미 색인된
            # 파일인가?"는 항상 path_norm 기준으로 판단해야 한다.
            self._meta: Dict[str, tuple] = {}
            for path, path_norm, is_dir, content_indexed, entries_schema, mtime, size in con.execute(
                "SELECT path, path_norm, is_dir, content_indexed, entries_schema, mtime, size FROM files"
            ):
                self._meta[path_norm] = (path, is_dir, content_indexed, entries_schema, mtime, size)
        finally:
            con.close()

    def _migrate_json_cache(self):
        """예전 JSON 캐시를 SQLite로 1회성으로 옮긴다. FTS5 가상 테이블/트리거를 만들기
        전에 원본 테이블부터 통째로 채우고, 그 다음 FTS 인덱스를 한 번에 벌크로 채운다
        — 트리거가 있는 상태로 수십만~100만 건을 한 건씩 넣으면 매번 트리거가 도는
        탓에 몇 분씩 걸린다(처음에 이렇게 짰다가 실측하고 알게 됨)."""
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return

        files = data.get("files", data if "dirs" not in data else {})
        dirs = data.get("dirs", {})

        # 같은 물리적 파일이 서로 다른 path 문자열(구분자 표기 차이 — 예전 JSON
        # 버전도 원본 경로 문자열을 그대로 키로 썼다)로 중복 저장돼 있을 수 있다.
        # path_norm 기준으로 합치고, 내용이 있는 쪽을 우선한다(더 정보가 많은 쪽).
        by_norm: Dict[str, tuple] = {}
        for path, meta in files.items():
            name = meta.get("name") or os.path.basename(path)
            path_norm = meta.get("path_norm") or os.path.normcase(os.path.normpath(path))
            content_indexed = 1 if meta.get("content_indexed") else 0
            entries = meta.get("entries") or []
            schema_ok = 0
            if entries:
                ext = os.path.splitext(path)[1].lower()
                schema_key = "page" if ext == ".pdf" else "sheet"
                schema_ok = 1 if schema_key in entries[0] else 0
            existing = by_norm.get(path_norm)
            if existing is None or (content_indexed and not existing[2]):
                by_norm[path_norm] = (path, name, content_indexed, schema_ok,
                                       meta.get("mtime"), meta.get("size"), entries)

        file_rows = []
        content_rows_by_norm = {}
        for path_norm, (path, name, content_indexed, schema_ok, mtime, size, entries) in by_norm.items():
            file_rows.append((path, path_norm, name, 0, content_indexed, schema_ok, mtime, size))
            if content_indexed and entries:
                content_rows_by_norm[path_norm] = entries

        seen_dir_norms = set()
        for path, meta in dirs.items():
            name = meta.get("name") or os.path.basename(path)
            path_norm = meta.get("path_norm") or os.path.normcase(os.path.normpath(path))
            if path_norm in seen_dir_norms:
                continue
            seen_dir_norms.add(path_norm)
            file_rows.append((path, path_norm, name, 1, 0, 0, None, None))

        con = sqlite3.connect(CACHE_DB_PATH)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.executescript(BASE_SCHEMA_SQL)
            con.executemany(
                "INSERT INTO files (path, path_norm, name, is_dir, content_indexed, "
                "entries_schema, mtime, size) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                file_rows,
            )
            if content_rows_by_norm:
                id_by_norm = dict(con.execute("SELECT path_norm, id FROM files WHERE content_indexed = 1"))
                content_rows = []
                for path_norm, entries in content_rows_by_norm.items():
                    file_id = id_by_norm.get(path_norm)
                    if file_id is None:
                        continue
                    for e in entries:
                        content_rows.append((
                            file_id, e["location"], e["text"],
                            e.get("page"), e.get("sheet"), e.get("row"),
                            e.get("paragraph"), e.get("slide"),
                        ))
                con.executemany(
                    "INSERT INTO content_entries (file_id, location, text, page, sheet, row_num, paragraph, slide) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    content_rows,
                )
            con.executescript(FTS_SCHEMA_SQL)
            con.execute("INSERT INTO files_fts(rowid, name) SELECT id, name FROM files")
            con.execute("INSERT INTO content_fts(rowid, text) SELECT id, text FROM content_entries")
            con.commit()
        finally:
            con.close()

        try:
            os.replace(CACHE_PATH, CACHE_PATH + ".bak")
        except OSError:
            pass

    # ---------- 색인 ----------
    def rebuild(self, folder_modes: List[tuple], progress: Optional[Callable[[str], None]] = None,
                cancel_check: Optional[Callable[[], bool]] = None,
                folder_done: Optional[Callable[[str, bool], None]] = None,
                folder_progress: Optional[Callable[[str, int, int], None]] = None,
                all_folder_modes: Optional[List[tuple]] = None):
        """folder_modes: (폴더 경로, filename_only) 목록.
        filename_only 폴더는 확장자 제한 없이 모든 파일명을 대상으로 하고, 내용은 파싱하지
        않는다(엑셀/PDF를 열어 읽는 과정을 통째로 건너뛰므로 색인이 훨씬 빠르다 — 파일명
        매핑만 미리 만들어 두는 셈). 하위 폴더 이름도 함께 기록해 폴더명 검색에 쓴다.
        내용 검색 폴더는 기존처럼 xlsx/pdf만 파싱한다.

        os.walk 대신 os.scandir 를 명시적 스택으로 직접 순회한다: DirEntry 는 Windows에서
        FindFirstFile/FindNextFile 결과를 이미 들고 있어 is_dir()/is_symlink()/stat() 가
        추가 시스템 콜 없이 끝난다. 재귀 대신 스택을 쓰는 이유는 아주 깊은 폴더 트리에서
        재귀 깊이 제한에 걸리지 않기 위해서다.

        바뀐 파일만 SQLite 에 반영한다(전체 재작성 아님). 파일명만 색인하는 파일/폴더는
        (다수, 보통 전체의 99%+) 한 트랜잭션으로 일괄 upsert 하고, 내용까지 색인하는
        파일(소수)은 파일별로 content_entries 를 통째로 지우고 다시 넣는다 — 행/페이지
        단위로 뭐가 바뀌었는지 대조하는 것보다 훨씬 간단하고, 애초에 파싱 자체가 그
        파일 전체를 다시 읽는 작업이라 추가 비용도 거의 없다."""
        found_paths = set()
        found_dirs = set()
        scanned_roots = []  # 이번 호출에서 실제로 스캔을 시작한 폴더 (stale 정리 범위 제한용)
        denied_paths = []   # 접근거부 등으로 열거 자체를 못한 하위 경로 (stale 정리에서 제외)
        changed = [False]  # 새 파일/변경/삭제가 하나라도 있었는지 — "색인 갱신됨" 배너 표시 여부에 씀

        # 진행 상황 알림: 파일마다 emit하면(수십만 번) 신호 폭주로 오히려 느려지니
        # 일정 시간 간격으로만 보낸다. 콘텐츠 파싱 중 progress(name) 호출도 같은
        # 스로틀을 공유해서, 짧은 시간에 작은 파일이 여러 개 몰려도 과하게 안 보낸다.
        _last_progress_at = [0.0]

        def _emit_progress(text: str):
            if not progress:
                return
            now = time.perf_counter()
            if now - _last_progress_at[0] < 0.2:
                return
            _last_progress_at[0] = now
            progress(text)

        # 같은 폴더를 "파일명만"과 "내용까지" 두 가지 방식으로 각각 등록할 수 있는데
        # (설정 화면에서 같은 경로를 두 번 추가), 처리 순서에 따라 filename_only 쪽이
        # 나중에 돌면서 방금 파싱한 내용을 덮어써버릴 수 있다. 내용 모드로도 덮이는
        # 경로 범위를 미리 알아두고, filename_only 처리에서는 그 범위를 건드리지 않는다.
        #
        # 이 범위는 반드시 "이번 호출에 넘어온 folder_modes"가 아니라 "등록된 폴더
        # 전체"를 기준으로 계산해야 한다 — main.py가 활성/비활성 폴더를 선택 라운드/
        # 나머지 라운드로 나눠서 rebuild()를 각각 따로 부르는데(검색창에 보여줄
        # 진행상황을 구분하려고), 같은 폴더가 한쪽엔 내용 모드로, 다른 쪽엔 파일명만
        # 모드로 등록돼 있으면(그룹으로 중복 등록) 그 둘이 서로 다른 rebuild() 호출에
        # 나뉘어 들어간다. folder_modes만 보고 계산하면 "나머지" 라운드는 그 폴더가
        # 내용 모드로도 등록돼 있다는 사실 자체를 몰라서, 방금 몇 분씩 걸려 색인한
        # 내용을 파일명만 라운드가 그대로 지워버리는 사고가 났다(실측으로 발견 —
        # 내용 색인이 끝난 것처럼 보이다가 다음 라운드에서 content_entries가 통째로
        # 0으로 리셋됨). all_folder_modes가 주어지면 그걸 기준으로 계산한다.
        content_mode_roots = [
            os.path.normcase(os.path.normpath(f)) for f, fo in (all_folder_modes or folder_modes)
            if not fo and os.path.isdir(f)
        ]

        # path_norm(정규화된 경로)을 키로 쓴다 — 같은 물리적 파일이라도 어느 등록
        # 루트를 거쳐 스캔됐는지에 따라 os.scandir 가 만드는 path 문자열의 구분자
        # 표기가 달라질 수 있어서, "이미 색인된 파일인가?"는 항상 path_norm 기준으로
        # 판단해야 한다(안 그러면 같은 파일이 두 행으로 중복 색인된다).
        pending: Dict[str, tuple] = {}          # path_norm -> _UPSERT_FILE_SQL 파라미터 그대로
        content_pending: Dict[str, tuple] = {}  # path_norm -> (path, name, mtime, size, entries)
        downgrade_norms = []  # 예전엔 내용까지 색인됐는데 이번엔 파일명만으로 바뀐 path_norm (content_entries 정리 대상)

        # 예전엔 전체 스캔(여러 폴더, 최대 수십만 개 파일)이 다 끝난 뒤에야 한 번에
        # 커밋했다 — 그러다 보니 앱이 중간에 죽거나(강제종료) 취소되면 이미 다 훑고
        # 파싱한 것까지 통째로 날아가서, 다음 실행 때 처음부터 다시 훑어야 했다
        # (엑셀/PDF 내용 파싱이 있는 폴더는 파일 하나하나 여는 게 오래 걸려서 특히
        # 뼈아팠음 — 실측으로 확인함: 재시작할 때마다 매번 처음부터). 일정量 쌓일
        # 때마다 중간 커밋해서, 죽거나 취소돼도 그때까지 훑은 건 남게 한다.
        FLUSH_EVERY = 300

        def _flush():
            if not pending and not content_pending and not downgrade_norms:
                return
            with self._lock:
                con = _connect()
                try:
                    if pending:
                        con.executemany(_UPSERT_FILE_SQL, list(pending.values()))

                    if downgrade_norms:
                        CHUNK = 500
                        for i in range(0, len(downgrade_norms), CHUNK):
                            chunk = downgrade_norms[i:i + CHUNK]
                            placeholders = ",".join("?" * len(chunk))
                            con.execute(
                                f"DELETE FROM content_entries WHERE file_id IN "
                                f"(SELECT id FROM files WHERE path_norm IN ({placeholders}))",
                                chunk,
                            )

                    for path_norm, (path, name, mtime, size, entries) in content_pending.items():
                        cur = con.execute(
                            _INSERT_FILE_RETURNING_ID_SQL,
                            (path, path_norm, name, 0, 1, 1, mtime, size),
                        )
                        file_id = cur.fetchone()[0]
                        con.execute("DELETE FROM content_entries WHERE file_id = ?", (file_id,))
                        if entries:
                            con.executemany(
                                "INSERT INTO content_entries (file_id, location, text, page, sheet, row_num, paragraph, slide) "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                [(file_id, e["location"], e["text"], e.get("page"), e.get("sheet"), e.get("row"),
                                  e.get("paragraph"), e.get("slide"))
                                 for e in entries],
                            )
                    con.commit()
                finally:
                    con.close()
            pending.clear()
            content_pending.clear()
            downgrade_norms.clear()

        def _maybe_flush():
            if len(pending) + len(content_pending) >= FLUSH_EVERY:
                _flush()

        def _queue_plain(path, path_norm, name, is_dir):
            pending[path_norm] = (path, path_norm, name, is_dir, 0, 0, None, None)
            with self._lock:
                self._meta[path_norm] = (path, is_dir, 0, 0, None, None)
            changed[0] = True
            _maybe_flush()

        def _queue_content(path, path_norm, name, mtime, size, entries):
            content_pending[path_norm] = (path, name, mtime, size, entries)
            with self._lock:
                self._meta[path_norm] = (path, 0, 1, 1, mtime, size)
            changed[0] = True
            _maybe_flush()

        # 설정에서 같은 폴더를 같은 검색 방식으로 두 번 등록해 뒀을 수 있다(예:
        # "그냥 두 개 다 보여주자" 방식으로 UI에서 중복을 허용하기로 함) — 실제
        # 디스크 스캔은 어차피 완전히 똑같은 결과를 내므로, 정확히 같은 (경로,
        # 검색 방식) 조합을 이 호출 안에서 두 번째부터는 실제로 훑지 않는다.
        # folder_done은 등록 횟수만큼 그대로 보내야 한다(main.py가 등록별로
        # 카운트를 세서 로딩 표시를 관리하기 때문 — 스킵해도 신호는 보낸다).
        seen_exact_modes = set()

        for folder, filename_only in folder_modes:
            if not os.path.isdir(folder):
                continue
            folder_norm = os.path.normcase(os.path.normpath(folder))
            scanned_roots.append(folder_norm)

            exact_key = (folder_norm, bool(filename_only))
            if exact_key in seen_exact_modes:
                if folder_done:
                    folder_done(folder_norm, bool(filename_only))
                continue
            seen_exact_modes.add(exact_key)

            # 진행률(%) 표시용 기준 총량 — 정확한 총 개수는 미리 다 훑어봐야 알 수
            # 있어서(비용이 큼) 대신 지난번 색인 때 이 폴더 밑에 있던 파일 수를
            # 어림값으로 쓴다. 첫 색인(기준값 없음)이면 퍼센트 없이 "색인 중…"만
            # 보여주는 쪽으로 처리한다(GUI 쪽에서 total<=0이면 None 반환).
            with self._lock:
                folder_total_hint = sum(1 for pn in self._meta if _is_under(pn, folder_norm))
            folder_found = [0]
            _last_folder_progress_at = [0.0]

            def _emit_folder_progress():
                if not folder_progress:
                    return
                now = time.perf_counter()
                if now - _last_folder_progress_at[0] < 0.2:
                    return
                _last_folder_progress_at[0] = now
                folder_progress(folder_norm, folder_found[0], folder_total_hint)

            stack = [folder]
            while stack:
                if cancel_check and cancel_check():
                    _flush()
                    return changed[0]
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
                        _flush()
                        return
                    try:
                        is_dir = entry.is_dir()  # follow_symlinks=True(기본): 링크로 연결된 폴더도 목록엔 포함
                    except OSError:
                        continue

                    if is_dir:
                        dpath = entry.path
                        if filename_only:
                            dpath_norm = os.path.normcase(os.path.normpath(dpath))
                            found_dirs.add(dpath_norm)
                            if dpath_norm not in self._meta:
                                _queue_plain(dpath, dpath_norm, entry.name, 1)
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
                    path_norm = os.path.normcase(os.path.normpath(path))
                    found_paths.add(path_norm)
                    _emit_progress(f"{len(found_paths):,}개 확인 중…")
                    folder_found[0] += 1
                    _emit_folder_progress()
                    if len(found_paths) % 100 == 0:
                        # 색인 스레드 우선순위를 낮춰도(QThread.LowPriority) GIL은 OS
                        # 우선순위와 무관하게 스레드 간에 나눠 써서, 이 스레드가 계속
                        # CPU를 쓰면 검색창 텍스트 입력(메인 스레드)이 밀릴 수 있다 —
                        # 실측으로 확인함(입력이 느려짐). time.sleep(0)은 그 자리에서
                        # GIL을 확실히 내놓게 강제한다.
                        time.sleep(0)

                    cached = self._meta.get(path_norm)  # (path, is_dir, content_indexed, entries_schema, mtime, size)
                    has_content = cached is not None and cached[2] == 1

                    if filename_only:
                        if content_mode_roots and any(_is_under(path_norm, r) for r in content_mode_roots):
                            continue  # 이 파일은 다른 등록이 내용까지 색인하므로 여기선 손대지 않는다
                        if cached is not None and not has_content:
                            continue  # 이미 파일명만으로 색인됨 - stat 자체가 필요 없다
                        if has_content:
                            downgrade_norms.append(path_norm)  # 내용 -> 파일명만 전환: 예전 content_entries 정리 필요
                        _queue_plain(path, path_norm, name, 0)
                        continue

                    # 내용 검색 폴더: entry.stat() 은 scandir 결과에 이미 있는 걸 재사용하는
                    # 것이라 os.stat(path) 와 달리 추가 시스템 콜이 들지 않는다.
                    try:
                        stat = entry.stat()
                    except OSError:
                        continue
                    # PDF 페이지/엑셀 시트 이동 기능이 추가되기 전에 색인된 캐시는 entries_schema=0
                    # (마이그레이션 시 표시됨). mtime/size 만 보고 "안 바뀌었으니 재파싱 안 해도
                    # 됨"이라 판단하면 그 구버전 데이터가 영원히 재사용돼서 페이지 이동이 계속
                    # 안 되는 채로 남는다.
                    same_stat = cached is not None and cached[4] == stat.st_mtime and cached[5] == stat.st_size
                    if has_content and same_stat and cached[3] == 1:
                        continue  # 변경 없고 이미 최신 스키마로 내용까지 색인됨

                    _emit_progress(f"{name} 읽는 중…")
                    # 개별 추출기(parsers.py)가 각자 내부적으로 예외를 삼키게 되어
                    # 있긴 하지만, 그건 각 추출기 구현이 맞게 짜여 있다는 전제에
                    # 기댄 것이다 — 실제로 그 전제가 깨진 적이 있었다(python-pptx의
                    # 예상 못한 None 케이스가 여기까지 새서 색인 스레드 전체가
                    # 죽었음). 파일 하나의 파싱 실패가 전체 재색인을 멈추게 하면
                    # 안 되므로, 마지막 방어선으로 한 번 더 감싼다.
                    try:
                        entries = EXTRACTORS[ext](path) if ext in EXTRACTORS else []
                    except Exception:
                        entries = []
                    _queue_content(path, path_norm, name, stat.st_mtime, stat.st_size, entries)

            # 이 폴더(하위 전체)를 다 훑었다 — 이번 folder_modes 안에 뒤에 처리할
            # 무거운(내용 파싱) 폴더가 더 있어도, 방금 끝난 폴더는 검색창 칩에서
            # 바로 로딩 표시를 내려도 된다. 그러려면 여기 쌓인 것부터 커밋해야
            # 실제로 검색 가능한 상태가 된다(search()는 self._meta가 아니라 DB를
            # 직접 읽는다) — 폴더별 완료를 알리기 전에 먼저 flush.
            _flush()
            if folder_done:
                folder_done(os.path.normcase(os.path.normpath(folder)), bool(filename_only))

        # 삭제된 파일/폴더 정리: 이번에 실제로 스캔에 성공한 루트 범위 안에서만 지운다
        # (접근거부로 못 본 하위 트리는 범위에서 제외 — 못 찾은 걸 삭제된 걸로 오인하지 않게).
        def _under_any(p_norm: str, roots: list) -> bool:
            return any(_is_under(p_norm, r) for r in roots)

        with self._lock:
            meta_snapshot = list(self._meta.items())  # (path_norm, (path, is_dir, content_indexed, entries_schema, mtime, size))

        stale_files = [
            path_norm for path_norm, (_path, is_dir, *_rest) in meta_snapshot
            if is_dir == 0 and path_norm not in found_paths
            and _under_any(path_norm, scanned_roots) and not _under_any(path_norm, denied_paths)
        ]
        stale_dirs = [
            path_norm for path_norm, (_path, is_dir, *_rest) in meta_snapshot
            if is_dir == 1 and path_norm not in found_dirs
            and _under_any(path_norm, scanned_roots) and not _under_any(path_norm, denied_paths)
        ]
        stale = stale_files + stale_dirs

        _flush()  # 위 스캔 루프에서 FLUSH_EVERY 단위로 이미 대부분 커밋됐고, 남은 나머지만 여기서 마저 커밋한다

        if stale:
            with self._lock:
                con = _connect()
                try:
                    # SQLite 파라미터 개수 제한(기본 999)을 피하려고 묶어서 지운다.
                    # content_entries 는 FK ON DELETE CASCADE 로 같이 정리된다.
                    CHUNK = 500
                    for i in range(0, len(stale), CHUNK):
                        chunk = stale[i:i + CHUNK]
                        placeholders = ",".join("?" * len(chunk))
                        con.execute(f"DELETE FROM files WHERE path_norm IN ({placeholders})", chunk)
                    con.commit()
                finally:
                    con.close()
                # self._meta 딕셔너리 크기를 바꾸는 작업이라 락 밖에서 하면 안 된다 —
                # 다른 스레드(메인 스레드의 file_count() 등)가 그 사이에 self._meta를
                # 락 잡고 순회 중이면 "dictionary changed size during iteration"
                # RuntimeError가 날 수 있다(실측은 못 했지만 코드 경합 자체는 명백함).
                for p in stale:
                    self._meta.pop(p, None)

        if stale:
            changed[0] = True
        return changed[0]

    # ---------- 검색 ----------
    def search(self, query: str, limit: int = MAX_RESULTS,
               folder_modes: Optional[List[tuple]] = None) -> List[dict]:
        """folder_modes 가 주어지면(None 이 아니면) 그 폴더들 아래 파일에서만 검색한다
        (검색창 하단의 폴더 포함/제외 토글용). 각 항목은 (폴더 경로, filename_only) 튜플이며,
        폴더별로 내용 검색/파일명만 검색을 다르게 지정할 수 있다.
        None 이면 폴더 제한 없이 전체를 내용 검색한다.
        검색어에 공백으로 여러 단어를 넣으면 전부 포함하는 것만 찾는다(순서/인접 무관) —
        이미 검색한 결과에 단어를 더 입력해서 계속 좁혀나갈 수 있다(결과 안에서 추가 검색).

        파일명/폴더명/내용 전부 SQLite 인덱스로 후보를 찾고(느린 Python 전체 스캔이
        아니라), 폴더 범위 판단(같은 경로가 파일명만/내용까지 두 가지로 등록됐을 수
        있어 후보 하나가 여러 폴더에 걸릴 수 있다)만 그 소수의 후보에 대해 Python 에서
        처리한다."""
        query = query.strip()
        if not query:
            return []
        terms = [t for t in query.lower().split() if t]
        if not terms:
            return []
        results = []

        # 화면에 보여줄 개수(limit, 설정의 search_display_limit에서 옴)보다
        # 넉넉하게 잡는다 — 폴더 범위 필터링/내용-파일명 중복 제거를 거치면서
        # 후보 중 일부가 최종 결과에서 빠지기 때문에, SQL LIMIT을 limit과
        # 똑같이 걸면 필터링 후 실제 표시 개수가 모자랄 수 있다. 50000은
        # 사용자가 설정에서 극단적으로 큰 값을 넣어도 한 번에 너무 많이
        # 긁어오지 않도록 두는 안전판일 뿐, 평소엔 걸릴 일이 없다.
        sql_limit = min(50000, max(limit * 4, 1000))

        allowed_norm = None
        if folder_modes is not None:
            # (루트, 접두사, filename_only) — 접두사(끝에 구분자 붙인 버전)를 폴더
            # 개수만큼만 미리 만들어 둔다.
            allowed_norm = []
            for f, fo in folder_modes:
                root_norm = os.path.normcase(os.path.normpath(f))
                prefix = root_norm if root_norm.endswith(os.sep) else root_norm + os.sep
                allowed_norm.append((root_norm, prefix, bool(fo)))

        def _matched_folders(p_norm: str):
            """p_norm 이 속하는 모든 (폴더_norm, filename_only) 를 반환한다(여러 개 가능 —
            같은 경로를 파일명만/내용까지 두 가지로 각각 등록했을 수 있다). 첫 매치 하나만
            채택하면, 둘 다 등록된 파일은 먼저 나온 등록의 모드로만 취급돼서 다른 쪽 모드의
            검색 결과가 아예 안 나오는 문제가 있다."""
            if allowed_norm is None:
                return [("", False)]  # 폴더 제한 없음 = 내용 검색으로 취급
            return [(root_norm, fo) for root_norm, prefix, fo in allowed_norm
                    if p_norm == root_norm or p_norm.startswith(prefix)]

        con = _connect()
        try:
            # ---- 폴더명 매칭: filename_only 폴더에서만, 폴더 자체를 결과로 보여준다 ----
            dir_rows = _term_search_sql(
                "files", "name", "files_fts", "t.path, t.path_norm, t.name",
                con, terms, extra_where="t.is_dir = 1", sql_limit=sql_limit,
            )
            for path, path_norm, name in dir_rows:
                matches = _matched_folders(path_norm)
                if not any(fo for _, fo in matches):
                    continue
                name_lower = name.lower()
                if all(t in name_lower for t in terms):
                    results.append({
                        "path": path,
                        "name": name,
                        "location": "폴더명 일치",
                        "snippet": "",
                        "is_dir": True,
                    })
                    if len(results) >= limit:
                        return results

            # ---- 파일명 후보 ----
            name_rows = _term_search_sql(
                "files", "name", "files_fts", "t.path, t.path_norm, t.name",
                con, terms, extra_where="t.is_dir = 0", sql_limit=sql_limit,
            )
            name_hits = {path: (path_norm, name) for path, path_norm, name in name_rows}

            # ---- 내용 후보 (page/sheet/row 컬럼이 실제 SQL 컬럼이라 인덱스를 탄다) ----
            content_rows = _content_search_sql(con, terms, sql_limit=sql_limit)
        finally:
            con.close()

        content_by_path: Dict[str, list] = {}
        for path, path_norm, name, location, text, page, sheet, row_num, paragraph, slide in content_rows:
            text_lower = text.lower()
            if not all(t in text_lower for t in terms):
                continue  # FTS 후보에는 들어왔지만 실제로 전부 포함은 아닐 수 있어 다시 확인
            idx = text_lower.find(terms[0])
            start = max(0, idx - SNIPPET_RADIUS)
            end = min(len(text), idx + len(terms[0]) + SNIPPET_RADIUS, start + MAX_SNIPPET_LEN)
            snippet = text[start:end]
            if start > 0:
                snippet = "…" + snippet
            if end < len(text):
                snippet = snippet + "…"
            result = {"path": path, "name": name, "location": location, "snippet": snippet}
            if sheet is not None:
                result["sheet"] = sheet
                result["row"] = row_num
            if page is not None:
                result["page"] = page
            if paragraph is not None:
                result["paragraph"] = paragraph
            if slide is not None:
                result["slide"] = slide
            content_by_path.setdefault(path, []).append((path_norm, result))

        # ---- 파일별로 통합: 폴더 범위 판단 + 파일명/내용 우선순위 ----
        all_paths = set(name_hits) | set(content_by_path)
        for path in all_paths:
            if path in content_by_path:
                path_norm = content_by_path[path][0][0]
            else:
                path_norm = name_hits[path][0]
            matches = _matched_folders(path_norm)
            if not matches:
                continue
            wants_filename = any(fo for _, fo in matches)
            wants_content = any(not fo for _, fo in matches)

            content_hits = [r for _, r in content_by_path.get(path, [])] if wants_content else []

            if content_hits:
                # 같은 파일이 파일명 등록과 내용 등록 둘 다에 걸리면("폴더 두 번 추가")
                # 내용 일치 쪽이 정확한 위치+미리보기까지 주는 상위 정보라, 굳이 "파일명
                # 일치"를 따로 또 보여주지 않는다 — 그러면 같은 파일이 결과에 여러 번
                # 나와서 아무거나 두 개 열면 파일이 두 번 열리는 것처럼 보인다.
                for result in content_hits:
                    results.append(result)
                    if len(results) >= limit:
                        return results
            elif wants_filename and path in name_hits:
                name = name_hits[path][1]
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
            return sum(1 for _path, is_dir, *_rest in self._meta.values() if is_dir == 0)
