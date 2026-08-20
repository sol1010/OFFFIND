"""shell_idlist_bytes 회귀 테스트 — 드래그 아웃에 실리는 CIDA 데이터가 형식에
맞는지. 형식이 깨지면 탐색기가 드롭을 통째로 거부하거나 엉뚱하게 동작할 수 있다."""
import struct

from shell_drag import shell_idlist_bytes


def test_cida_layout_for_real_file(tmp_path):
    f = tmp_path / "드래그대상.txt"
    f.write_text("x", encoding="utf-8")

    data = shell_idlist_bytes(str(f))
    assert data is not None

    cidl, off_parent, off_child = struct.unpack_from("<3I", data, 0)
    assert cidl == 1
    assert off_parent == 12  # 헤더(UINT 3개) 바로 뒤
    assert 12 < off_child < len(data)
    # 각 PIDL 은 2바이트 null 터미네이터(cb=0)로 끝난다
    assert data[off_child - 2:off_child] == b"\x00\x00"
    assert data[-2:] == b"\x00\x00"


def test_missing_path_returns_none():
    assert shell_idlist_bytes(r"C:\없는폴더_zz\없는파일_zz.txt") is None
