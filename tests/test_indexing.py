"""색인 동작 회귀 테스트 — 파싱 실패 재시도, 중복 등록, 삭제 정리, 스키마 이관."""
import os
import sqlite3

from conftest import make_xlsx


def _rebuild(ix, modes):
    idx = ix.Indexer()
    idx.rebuild(modes, all_folder_modes=modes)
    return idx


def _file_row(ix, path):
    con = sqlite3.connect(ix.CACHE_DB_PATH)
    try:
        return con.execute(
            "SELECT content_indexed, entries_schema, parse_failed_at, "
            "(SELECT COUNT(*) FROM content_entries ce WHERE ce.file_id = f.id) "
            "FROM files f WHERE f.path_norm = ?",
            (os.path.normcase(os.path.normpath(path)),),
        ).fetchone()
    finally:
        con.close()


def test_broken_file_is_marked_failed_not_empty(offfind, tmp_path):
    """파싱 실패를 "내용 0개인 파일"로 저장하면, mtime 이 그대로인 한 다시는
    시도하지 않아서 그 파일이 검색에서 영구히 빠진다. 실패로 남겨야 한다."""
    data = tmp_path / "data"
    data.mkdir()
    broken = data / "broken.xlsx"
    broken.write_bytes(b"not a real xlsx at all")

    _rebuild(offfind, [(str(data), False)])

    _ci, _es, failed_at, entries = _file_row(offfind, broken)
    assert failed_at is not None, "실패했는데 실패 표시가 없다"
    assert entries == 0


def test_empty_document_is_not_marked_failed(offfind, tmp_path):
    """열리긴 했는데 글자가 없는 문서(스캔본 등)는 실패가 아니다 — 실패로
    취급하면 멀쩡한 파일을 계속 다시 파싱하게 된다."""
    data = tmp_path / "data"
    data.mkdir()
    empty = data / "empty.xlsx"
    make_xlsx(empty, [])

    _rebuild(offfind, [(str(data), False)])

    _ci, _es, failed_at, entries = _file_row(offfind, empty)
    assert failed_at is None
    assert entries == 0


def test_failed_file_not_retried_within_window(offfind, tmp_path):
    """깨진 큰 파일을 색인할 때마다 다시 파싱하면 낭비다."""
    data = tmp_path / "data"
    data.mkdir()
    broken = data / "broken.xlsx"
    broken.write_bytes(b"nope")

    _rebuild(offfind, [(str(data), False)])
    first = _file_row(offfind, broken)[2]
    _rebuild(offfind, [(str(data), False)])
    assert _file_row(offfind, broken)[2] == first, "대기시간 안인데 다시 시도했다"


def test_failed_file_retried_after_window(offfind, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    broken = data / "broken.xlsx"
    broken.write_bytes(b"nope")

    _rebuild(offfind, [(str(data), False)])
    first = _file_row(offfind, broken)[2]

    monkeypatch.setattr(offfind, "PARSE_RETRY_AFTER_SEC", 0)
    _rebuild(offfind, [(str(data), False)])
    assert _file_row(offfind, broken)[2] != first, "대기시간이 지났는데 재시도 안 했다"


def test_repaired_file_gets_indexed(offfind, tmp_path):
    """잠깐 잠겨 있다가 정상으로 돌아온 파일은 내용이 들어와야 한다."""
    data = tmp_path / "data"
    data.mkdir()
    f = data / "doc.xlsx"
    f.write_bytes(b"broken for now")

    _rebuild(offfind, [(str(data), False)])
    assert _file_row(offfind, f)[2] is not None

    make_xlsx(f, ["소방 점검 결과"])  # 파일이 바뀌었으니 즉시 재시도돼야 한다
    idx = _rebuild(offfind, [(str(data), False)])

    _ci, _es, failed_at, entries = _file_row(offfind, f)
    assert failed_at is None and entries > 0
    assert idx.search("소방", limit=10, folder_modes=[(str(data), False)])


def test_same_folder_dual_mode_indexes_once(offfind, tmp_path):
    """같은 폴더를 파일명·내용 두 가지로 등록해도 파일 행은 하나여야 한다."""
    data = tmp_path / "data"
    data.mkdir()
    make_xlsx(data / "doc.xlsx", ["소방 점검"])

    modes = [(str(data), True), (str(data), False)]
    _rebuild(offfind, modes)

    con = sqlite3.connect(offfind.CACHE_DB_PATH)
    try:
        n = con.execute("SELECT COUNT(*) FROM files WHERE is_dir = 0").fetchone()[0]
    finally:
        con.close()
    assert n == 1, f"같은 파일이 {n}행으로 중복 색인됐다"


def test_deleted_file_is_removed_from_index(offfind, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    gone = data / "gone.txt"
    gone.write_text("x", encoding="utf-8")
    (data / "stays.txt").write_text("x", encoding="utf-8")

    idx = _rebuild(offfind, [(str(data), True)])
    assert len(idx.search("txt", limit=10, folder_modes=[(str(data), True)])) == 2

    gone.unlink()
    idx = _rebuild(offfind, [(str(data), True)])
    names = [os.path.basename(r["path"]) for r in
             idx.search("txt", limit=10, folder_modes=[(str(data), True)])]
    assert names == ["stays.txt"], names


def test_same_file_via_different_root_spelling_is_one_row(offfind, tmp_path):
    """같은 폴더를 슬래시/백슬래시로 다르게 적어 등록해도 한 파일은 한 행이다
    (path_norm 기준 동일성)."""
    data = tmp_path / "data"
    data.mkdir()
    (data / "a.txt").write_text("x", encoding="utf-8")

    slashed = str(data).replace("\\", "/")
    _rebuild(offfind, [(str(data), True), (slashed, True)])

    con = sqlite3.connect(offfind.CACHE_DB_PATH)
    try:
        n = con.execute("SELECT COUNT(*) FROM files WHERE is_dir = 0").fetchone()[0]
    finally:
        con.close()
    assert n == 1, f"같은 파일이 {n}행으로 중복 색인됐다"


def _counts(ix):
    con = sqlite3.connect(ix.CACHE_DB_PATH)
    try:
        dirs = con.execute("SELECT COUNT(*) FROM files WHERE is_dir = 1").fetchone()[0]
        files = con.execute("SELECT COUNT(*) FROM files WHERE is_dir = 0").fetchone()[0]
        return dirs, files
    finally:
        con.close()


def test_content_only_pass_does_not_wipe_dirs_and_other_files(offfind, tmp_path):
    """내용 칩만 켠 채 색인해도 폴더 행과 문서가 아닌 파일이 지워지면 안 된다.

    내용 패스는 문서 확장자만 보고 폴더는 기록하지 않는다. 그 범위로 "이번에 못
    찾았으니 삭제됐다"고 판단하면 멀쩡한 색인이 통째로 날아가고, 파일명 칩을 다시
    켜면 되살아나서 칩을 토글할 때마다 '색인 갱신됨' 배너가 떴다."""
    data = tmp_path / "data"
    sub = data / "설계도면"
    sub.mkdir(parents=True)
    make_xlsx(sub / "doc.xlsx", ["소방 점검"])
    (sub / "memo.txt").write_text("x", encoding="utf-8")

    both = [(str(data), True), (str(data), False)]
    content_only = [(str(data), False)]

    _rebuild(offfind, both)
    before = _counts(offfind)
    assert before[0] >= 1 and before[1] >= 2, before

    idx = offfind.Indexer()
    changed = idx.rebuild(content_only, all_folder_modes=content_only)
    assert _counts(offfind) == before, "내용 칩만 켰다고 폴더/파일 색인이 지워졌다"
    assert changed is False, "바뀐 게 없는데 변경으로 보고했다(배너가 뜬다)"


def test_dual_mode_folder_still_indexes_non_document_files(offfind, tmp_path):
    """같은 폴더를 파일명·내용으로 함께 등록해도 문서가 아닌 파일이 빠지면 안 된다.

    파일명 패스가 "내용 등록이 처리할 것"이라며 내용 폴더 아래 파일을 통째로
    건너뛰면, 내용 패스가 보지 않는 확장자(txt/jpg 등)는 어디에서도 색인되지
    않아 파일명 칩으로도 영영 못 찾게 된다."""
    data = tmp_path / "data"
    data.mkdir()
    make_xlsx(data / "doc.xlsx", ["소방 점검"])
    (data / "메모.txt").write_text("x", encoding="utf-8")

    modes = [(str(data), True), (str(data), False)]
    idx = _rebuild(offfind, modes)

    names = [os.path.basename(r["path"])
             for r in idx.search("메모", limit=10, folder_modes=[(str(data), True)])]
    assert names == ["메모.txt"], names


def test_repeated_index_with_no_changes_reports_no_change(offfind, tmp_path):
    """아무것도 안 바뀌었으면 '색인 갱신됨' 배너가 뜨면 안 된다."""
    data = tmp_path / "data"
    data.mkdir()
    make_xlsx(data / "doc.xlsx", ["소방 점검"])
    (data / "a.txt").write_text("x", encoding="utf-8")

    modes = [(str(data), True), (str(data), False)]
    _rebuild(offfind, modes)

    idx = offfind.Indexer()
    assert idx.rebuild(modes, all_folder_modes=modes) is False


def test_schema_migration_adds_missing_column(offfind, tmp_path):
    """예전 버전이 만든 DB(파싱 실패 컬럼 없음)를 열어도 자동으로 컬럼이 붙어야
    한다 — 안 붙으면 앱이 시작하자마자 SELECT 에서 죽는다."""
    os.makedirs(os.path.dirname(offfind.CACHE_DB_PATH), exist_ok=True)
    con = sqlite3.connect(offfind.CACHE_DB_PATH)
    try:
        con.executescript("""
            CREATE TABLE files (
                id INTEGER PRIMARY KEY, path TEXT NOT NULL, path_norm TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL, is_dir INTEGER NOT NULL,
                content_indexed INTEGER NOT NULL DEFAULT 0,
                entries_schema INTEGER NOT NULL DEFAULT 0, mtime REAL, size INTEGER
            );
            CREATE TABLE content_entries (
                id INTEGER PRIMARY KEY,
                file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
                location TEXT NOT NULL, text TEXT NOT NULL, page INTEGER, sheet TEXT,
                row_num INTEGER
            );
        """)
        con.commit()
    finally:
        con.close()

    offfind.Indexer()  # 여기서 마이그레이션이 돌아야 한다

    con = sqlite3.connect(offfind.CACHE_DB_PATH)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(files)")}
        entry_cols = {r[1] for r in con.execute("PRAGMA table_info(content_entries)")}
    finally:
        con.close()
    assert "parse_failed_at" in cols
    assert {"paragraph", "slide"} <= entry_cols
