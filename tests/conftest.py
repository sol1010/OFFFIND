"""테스트 공용 설정.

가장 중요한 건 격리다. Indexer 는 %APPDATA%\\OFFFIND 아래의 실제 DB를 쓰기
때문에, 격리 없이 테스트를 돌리면 사용자의 진짜 색인을 건드린다(예전에 테스트
스크립트가 실제 설정 파일을 덮어쓴 적이 있어서 config.Settings 쪽에는 이미
안전장치가 들어가 있다). 여기서는 APPDATA 자체를 임시 폴더로 돌려서,
config/indexer 가 임포트되는 시점에 이미 임시 경로를 보게 만든다.
"""
import importlib
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


def pytest_configure(config):
    """테스트가 만드는 임시 파일을 사용자가 색인하는 범위 밖에 두게 한다.

    기본 tmp_path 는 %TEMP% 아래(보통 C: 안)라, C:\\ 를 등록해 둔 사용자의 색인에
    테스트 픽스처('소방점검.xlsx' 같은)가 그대로 섞여 들어간다 — 실제로 검색
    결과에 나타나서 알아챘다. 저장소 안의 무시되는 폴더로 옮긴다."""
    base = os.path.join(REPO_ROOT, ".pytest_tmp")
    os.makedirs(base, exist_ok=True)
    config.option.basetemp = config.option.basetemp or base


@pytest.fixture()
def offfind(tmp_path, monkeypatch):
    """임시 APPDATA 를 쓰는 indexer 모듈을 새로 임포트해서 돌려준다.

    config/indexer 는 임포트 시점에 APPDATA 를 읽어 경로 상수를 굳히므로,
    monkeypatch 만으로는 부족하고 모듈을 다시 임포트해야 한다."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    os.makedirs(tmp_path / "appdata" / "OFFFIND", exist_ok=True)

    for mod in ("config", "indexer", "parsers"):
        sys.modules.pop(mod, None)
    import indexer as ix
    importlib.reload(ix)
    assert str(tmp_path) in ix.CACHE_DB_PATH, "테스트가 실제 DB를 볼 뻔했다"
    return ix


@pytest.fixture()
def make_tree(tmp_path):
    """테스트용 폴더/파일을 만들어 주는 도우미."""
    def _make(rel_paths, root="data"):
        base = tmp_path / root
        for rel in rel_paths:
            p = base / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("dummy", encoding="utf-8")
        base.mkdir(parents=True, exist_ok=True)
        return str(base)
    return _make


def make_xlsx(path, rows):
    """openpyxl 로 실제 xlsx 를 만든다(내용 검색 테스트용)."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for i, row in enumerate(rows, start=1):
        ws.cell(row=i, column=1, value=row)
    wb.save(path)
    wb.close()
