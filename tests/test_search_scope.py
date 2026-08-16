"""검색 범위(등록 폴더) 관련 회귀 테스트.

여기 있는 케이스 대부분은 "사람이 눈으로 회귀 검증하다가 반드시 놓치는" 조합이다
— 부모/자식 중첩, 드라이브 루트, 같은 폴더 이중 등록, 경로 속 '_' 같은 것들.
"""
import os


def _rebuild(ix, modes):
    idx = ix.Indexer()
    idx.rebuild(modes, all_folder_modes=modes)
    return idx


def test_limit_never_drops_in_scope_results(offfind, tmp_path):
    """SQL LIMIT 이 폴더 범위보다 먼저 걸려서 결과가 통째로 사라지면 안 된다.

    실제로 있었던 버그: 범위 조건 없이 LIMIT 을 먼저 걸어서, 늦게 색인된(=rowid 가
    큰) 폴더의 결과가 앞쪽 후보에 밀려 하나도 안 나왔다. 실측으로 C:\\Program Files
    안의 2,811건이 0건이 되는 걸 확인했다."""
    outside = tmp_path / "outside"
    inside = tmp_path / "inside"
    outside.mkdir()
    inside.mkdir()
    # 바깥 폴더가 sql_limit(최소 1000) 을 혼자 다 채우도록 넉넉히 만든다.
    for i in range(1200):
        (outside / f"zz_{i:05d}.txt").write_text("x", encoding="utf-8")
    for i in range(3):
        (inside / f"zz_in_{i}.txt").write_text("x", encoding="utf-8")

    # 바깥을 먼저 색인해서 낮은 rowid 를 차지하게 만든다(버그 재현 조건).
    idx = _rebuild(offfind, [(str(outside), True), (str(inside), True)])

    # 2글자라 LIKE 폴백 → rowid 순서로 스캔된다(재현이 결정적이 되도록).
    res = idx.search("zz", limit=250, folder_modes=[(str(inside), True)])
    assert len(res) == 3, "범위 안 결과가 LIMIT 에 밀려 사라졌다"
    for r in res:
        assert str(inside).lower() in r["path"].lower()


def test_scope_excludes_other_folders(offfind, tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir(); b.mkdir()
    (a / "report.txt").write_text("x", encoding="utf-8")
    (b / "report.txt").write_text("x", encoding="utf-8")

    idx = _rebuild(offfind, [(str(a), True), (str(b), True)])

    res = idx.search("report", limit=100, folder_modes=[(str(a), True)])
    assert len(res) == 1
    assert str(a).lower() in res[0]["path"].lower()


def test_no_folder_modes_means_no_restriction(offfind, tmp_path):
    """folder_modes=None 은 "폴더 제한 없음"이자 "전부 내용 모드로 취급"이다
    (등록된 폴더가 하나도 없을 때만 쓰이는 경로). 파일명만 색인된 파일은
    이 모드에서 결과로 나오지 않는 것이 기존 계약이다."""
    from conftest import make_xlsx

    a = tmp_path / "a"
    a.mkdir()
    make_xlsx(a / "report.xlsx", ["소방 점검 결과"])
    idx = _rebuild(offfind, [(str(a), False)])  # 내용 모드로 색인

    assert idx.search("소방", limit=100, folder_modes=None)


def test_empty_folder_modes_returns_nothing(offfind, tmp_path):
    """활성화된 폴더가 하나도 없으면(칩을 전부 끈 상태) 결과도 없어야 한다."""
    a = tmp_path / "a"
    a.mkdir()
    (a / "report.txt").write_text("x", encoding="utf-8")
    idx = _rebuild(offfind, [(str(a), True)])

    assert idx.search("report", limit=100, folder_modes=[]) == []


def test_parent_and_child_registered_together(offfind, tmp_path):
    """부모와 자식이 둘 다 등록돼 있어도 결과가 중복되거나 빠지지 않아야 한다."""
    parent = tmp_path / "parent"
    child = parent / "child"
    child.mkdir(parents=True)
    (parent / "report_p.txt").write_text("x", encoding="utf-8")
    (child / "report_c.txt").write_text("x", encoding="utf-8")

    idx = _rebuild(offfind, [(str(parent), True), (str(child), True)])

    res = idx.search("report", limit=100, folder_modes=[(str(parent), True), (str(child), True)])
    paths = sorted(os.path.basename(r["path"]) for r in res)
    assert paths == ["report_c.txt", "report_p.txt"], paths

    # 자식만 켠 경우 부모 직속 파일은 안 나와야 한다.
    res = idx.search("report", limit=100, folder_modes=[(str(child), True)])
    assert [os.path.basename(r["path"]) for r in res] == ["report_c.txt"]


def test_underscore_in_path_is_not_a_wildcard(offfind, tmp_path):
    """경로에 '_' 가 있어도 다른 폴더가 딸려 들어오면 안 된다.

    범위 조건을 LIKE 'prefix%' 로 짰다면 '_' 가 '아무 글자 하나' 로 해석돼서
    a_b 를 등록했을 뿐인데 axb 결과까지 나온다. 이 저장소 경로부터 '\\_\\' 를
    포함하고 있어서 실제로 걸릴 수 있는 조합이다."""
    a_b = tmp_path / "a_b"
    axb = tmp_path / "axb"
    a_b.mkdir(); axb.mkdir()
    (a_b / "report.txt").write_text("x", encoding="utf-8")
    (axb / "report.txt").write_text("x", encoding="utf-8")

    idx = _rebuild(offfind, [(str(a_b), True), (str(axb), True)])

    res = idx.search("report", limit=100, folder_modes=[(str(a_b), True)])
    assert len(res) == 1
    assert "a_b" in res[0]["path"]


def test_drive_root_scope_matches_everything_under_it(offfind):
    """드라이브 루트('C:\\')는 끝에 이미 구분자가 붙어 있어 접두사 계산이
    한 칸 어긋나기 쉽다 — 범위 조건이 실제로 하위 전체를 덮는지 본다."""
    sql, params = offfind._scope_where(["c:\\"], "t.path_norm")
    assert "?" in sql and len(params) == 3
    root, prefix, upper = params
    assert prefix == "c:\\"
    # 하위 경로가 [prefix, upper) 범위 안에 들어와야 한다.
    assert prefix <= "c:\\users\\sol\\a.txt" < upper


def test_scope_where_empty_means_no_condition(offfind):
    assert offfind._scope_where([], "t.path_norm") == ("", [])


def test_is_under_handles_drive_root(offfind):
    """드라이브 루트는 normpath 가 이미 '\\' 를 붙여서 돌려준다 — 여기에 구분자를
    한 번 더 붙이면 'c:\\\\' 가 돼서 그 밑의 어떤 경로와도 안 맞는다.

    이 실수가 검색창 쪽 폴더 분류에서 실제로 났고, 그래서 C:\\ 로 등록한 폴더의
    결과가 자기 칩이 아니라 '기타' 로 빠졌다."""
    assert offfind._is_under("c:\\users\\sol\\a.txt", "c:\\")
    assert offfind._is_under("c:\\", "c:\\")
    assert offfind._is_under("c:\\users\\sol\\a.txt", "c:\\users\\sol")
    # 이름이 겹치는 옆 폴더까지 딸려 들어오면 안 된다
    assert not offfind._is_under("c:\\users\\solaris\\a.txt", "c:\\users\\sol")
    assert not offfind._is_under("d:\\users\\sol\\a.txt", "c:\\")


def test_drive_root_scope_finds_files_deep_under_it(offfind, tmp_path):
    """드라이브 루트로 등록했을 때 그 아래 깊은 경로의 파일이 검색돼야 한다
    (범위 조건이 드라이브 루트에서 깨지지 않는지 실제 검색으로 확인)."""
    deep = tmp_path / "x" / "y" / "z"
    deep.mkdir(parents=True)
    (deep / "report.txt").write_text("x", encoding="utf-8")

    # tmp_path 가 속한 드라이브 루트를 등록 폴더로 쓴다
    drive = os.path.splitdrive(str(tmp_path))[0] + os.sep
    idx = _rebuild(offfind, [(str(tmp_path), True)])

    res = idx.search("report", limit=10, folder_modes=[(drive, True)])
    assert len(res) == 1, "드라이브 루트 등록인데 하위 파일을 못 찾았다"


def test_chips_are_independent_same_file_can_appear_twice(offfind, tmp_path):
    """폴더 칩(등록)은 각각 독립이다.

    같은 폴더를 파일명용·내용용으로 등록했고 파일 이름과 내용에 둘 다 검색어가
    있으면, 파일명 결과와 내용 결과가 모두 나와야 한다. 예전엔 내용 결과가 있으면
    파일명 결과를 버려서, 파일명 칩에서만 보여야 할 항목이 다른 칩의 등록 때문에
    사라졌다."""
    from conftest import make_xlsx

    data = tmp_path / "data"
    data.mkdir()
    make_xlsx(data / "소방점검.xlsx", ["소방 설비 점검 결과"])

    idx = _rebuild(offfind, [(str(data), True), (str(data), False)])

    both = idx.search("소방", limit=100,
                      folder_modes=[(str(data), True), (str(data), False)])
    locations = {r["location"] for r in both}
    assert "파일명 일치" in locations, "파일명 결과가 내용 결과에 밀려 사라졌다"
    assert any(l != "파일명 일치" for l in locations), "내용 결과가 없다"


def test_one_chip_result_does_not_depend_on_other_chips(offfind, tmp_path):
    """한 칩의 결과는 다른 칩을 켜고 끄는 것에 영향을 받으면 안 된다."""
    from conftest import make_xlsx

    data = tmp_path / "data"
    data.mkdir()
    make_xlsx(data / "소방점검.xlsx", ["소방 설비 점검 결과"])

    idx = _rebuild(offfind, [(str(data), True), (str(data), False)])

    only_name = idx.search("소방", limit=100, folder_modes=[(str(data), True)])
    with_both = idx.search("소방", limit=100,
                           folder_modes=[(str(data), True), (str(data), False)])

    name_only_paths = sorted(r["path"] for r in only_name)
    name_in_both = sorted(r["path"] for r in with_both if r["location"] == "파일명 일치")
    assert name_only_paths == name_in_both, "내용 칩을 켰다고 파일명 칩 결과가 달라졌다"
