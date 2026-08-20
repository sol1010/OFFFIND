"""파일 드래그 아웃을 탐색기 드래그처럼 만들어 주는 Shell IDList 데이터 생성.

파일 URL(CF_HDROP)만 실어 보내는 드래그는 탐색기가 "외부 앱 드래그"로 취급해서,
바탕화면·폴더에 놓았을 때 아이콘이 놓은 자리가 아니라 임의의 빈 칸에 생기고
새로고침하면 자리가 또 바뀌는 어색한 동작이 된다(실제로 겪음). 탐색기끼리의
드래그에는 CFSTR_SHELLIDLIST("Shell IDList Array") 포맷이 같이 실리는데, 이게
있어야 대상 폴더 뷰가 놓은 좌표에 아이콘을 배치하고 그 위치를 기억한다 —
같은 데이터를 우리가 직접 만들어 QMimeData에 얹는다.

CIDA 구조(문서화된 공개 포맷):
    UINT cidl;                // 항목 개수
    UINT aoffset[cidl + 1];   // [0]=부모 폴더 PIDL, [1..]=각 항목의 자식 PIDL
                              // (오프셋은 구조체 시작 기준)
이어서 PIDL 바이트들이 그대로 붙는다.
"""
import ctypes
import struct
from ctypes import wintypes

_shell32 = ctypes.windll.shell32
_ole32 = ctypes.windll.ole32

_shell32.SHParseDisplayName.restype = ctypes.c_long  # HRESULT
_shell32.SHParseDisplayName.argtypes = [
    ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ctypes.c_ulong, ctypes.c_void_p,
]
_shell32.ILFindLastID.restype = ctypes.c_void_p
_shell32.ILFindLastID.argtypes = [ctypes.c_void_p]
_shell32.ILGetSize.restype = ctypes.c_uint
_shell32.ILGetSize.argtypes = [ctypes.c_void_p]
_shell32.ILRemoveLastID.restype = wintypes.BOOL
_shell32.ILRemoveLastID.argtypes = [ctypes.c_void_p]
_ole32.CoTaskMemFree.restype = None
_ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

# QMimeData에서 이 이름으로 setData 하면 Qt(QWindowsMimeRegistry)가 실제
# 클립보드 포맷 "Shell IDList Array"(CFSTR_SHELLIDLIST)로 등록해 준다.
SHELL_IDLIST_MIME = 'application/x-qt-windows-mime;value="Shell IDList Array"'


def shell_idlist_bytes(path: str):
    """path 하나짜리 CIDA 바이트열을 만든다. 실패하면 None(드래그 자체는
    CF_HDROP만으로도 동작하니, 이건 어디까지나 있으면 좋은 부가 데이터다)."""
    pidl = ctypes.c_void_p()
    try:
        hr = _shell32.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None)
    except OSError:
        return None
    if hr != 0 or not pidl.value:
        return None
    try:
        child = _shell32.ILFindLastID(pidl)
        if not child:
            return None
        # ILRemoveLastID 는 pidl 을 제자리에서 잘라 부모 PIDL 로 만드는데, 그
        # 잘리는 지점이 바로 child 가 가리키는 메모리다 — 자르기 전에 복사해 둔다.
        child_bytes = ctypes.string_at(child, _shell32.ILGetSize(child))
        if not _shell32.ILRemoveLastID(pidl):
            return None
        parent_bytes = ctypes.string_at(pidl, _shell32.ILGetSize(pidl))
    finally:
        _ole32.CoTaskMemFree(pidl)

    header = struct.pack("<3I", 1, 12, 12 + len(parent_bytes))
    return header + parent_bytes + child_bytes
