"""전역(시스템 전체) 단축키 등록/해제. Win32 RegisterHotKey 사용.

예전엔 keyboard 라이브러리(저수준 키보드 훅, WH_KEYBOARD_LL)를 썼는데, Windows는
훅 콜백이 제한 시간 안에 응답하지 못하면 그 훅을 소리 없이 제거해 버린다 — 이
앱은 색인(수십만 파일 스캔)이 같은 프로세스에서 GIL을 잡고 돌기 때문에 색인 중
훅 콜백이 밀리기 쉽고, 실제로 앱은 멀쩡히 떠 있는데 단축키만 영영 안 먹는 일이
있었다(실측 — 재시작 전까지 복구 안 됨). RegisterHotKey는 훅이 아니라 OS가
WM_HOTKEY 메시지를 이 스레드의 큐에 직접 넣어주는 방식이라, 프로세스가 아무리
바빠도 등록이 해제되지 않고 눌린 키는 큐에 남아 있다가 처리된다.

부수 변화: RegisterHotKey는 조합키를 시스템 전역에서 소비한다 — 예전 훅 방식
(suppress 안 함)과 달리 같은 조합이 다른 앱/IME로 같이 전달되지 않는다. 검색창
호출용으로는 이쪽이 오히려 맞는 동작이다.

WM_HOTKEY는 "등록한 스레드"의 메시지 큐로 온다 — 이 객체는 반드시 메인(Qt 이벤트
루프) 스레드에서 만들고 register()도 그 스레드에서 불러야 한다(지금 구조상
main.App 이 메인 스레드에서 만들므로 자연히 지켜진다).
"""
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal

WM_HOTKEY = 0x0312
_HOTKEY_ID = 1  # 앱 전체에 전역 단축키가 하나뿐이라 고정 id 로 충분

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000  # 꾹 누르고 있을 때 자동반복으로 창이 깜빡깜빡 토글되지 않게

_MODIFIER_FLAGS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "meta": MOD_WIN,
}

# 설정 화면(HotkeyCaptureEdit)은 QKeySequence(...).toString(NativeText).lower()로
# 키 이름을 만든다 — 거기서 나올 수 있는 특수키 이름들을 가상 키코드로 잇는다.
_SPECIAL_VKS = {
    "space": 0x20,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "return": 0x0D,
    "enter": 0x0D,
    "pause": 0x13,
    "caps lock": 0x14,
    "capslock": 0x14,
    "ins": 0x2D,
    "insert": 0x2D,
    "del": 0x2E,
    "delete": 0x2E,
    "home": 0x24,
    "end": 0x23,
    "pgup": 0x21,
    "page up": 0x21,
    "pgdown": 0x22,
    "page down": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
}


def parse_hotkey(hotkey: str):
    """'ctrl+alt+space' 형식을 (수정자 플래그, 가상 키코드)로 푼다. 실패 시 None.

    '+' 로 나누므로 키 자체가 '+' 인 조합은 표현할 수 없는데, 예전 keyboard
    라이브러리도 같은 형식을 썼으므로 기존 설정 파일에 그런 값이 있을 수 없다."""
    mods = 0
    vk = None
    for token in hotkey.lower().split("+"):
        token = token.strip()
        if not token:
            return None
        if token in _MODIFIER_FLAGS:
            mods |= _MODIFIER_FLAGS[token]
            continue
        if vk is not None:
            return None  # 수정자 아닌 키가 두 개 — 형식 오류
        if token in _SPECIAL_VKS:
            vk = _SPECIAL_VKS[token]
        elif len(token) > 1 and token[0] == "f" and token[1:].isdigit():
            n = int(token[1:])
            if not 1 <= n <= 24:
                return None
            vk = 0x70 + n - 1  # VK_F1 = 0x70
        elif len(token) == 1:
            # 문자 하나(a~z, 0~9, 문장부호)는 현재 키보드 배열 기준으로 변환.
            # 하위 바이트가 가상 키코드, 상위 바이트는 시프트 상태(무시).
            scan = ctypes.windll.user32.VkKeyScanW(ctypes.c_wchar(token))
            if scan == -1:
                return None
            vk = scan & 0xFF
        else:
            return None
    if vk is None:
        return None  # 수정자만 있고 실제 키가 없음
    return mods, vk


class _WmHotkeyFilter(QAbstractNativeEventFilter):
    """Qt 이벤트 루프에 끼어들어 WM_HOTKEY 네이티브 메시지를 잡는다."""

    def __init__(self, on_hotkey):
        super().__init__()
        self._on_hotkey = on_hotkey

    def nativeEventFilter(self, event_type, message):
        if event_type == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == _HOTKEY_ID:
                self._on_hotkey()
                return True, 0
        return False, 0


class HotkeyManager(QObject):
    triggered = Signal()
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self._current_hotkey = None
        self._registered = False
        # 필터 객체는 파이썬 쪽에서 참조를 붙들고 있어야 한다 — installNativeEventFilter
        # 는 소유권을 가져가지 않아서, 지역변수로 두면 GC 되는 순간 크래시한다.
        self._filter = _WmHotkeyFilter(self.triggered.emit)
        app = QCoreApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._filter)

    def register(self, hotkey: str) -> bool:
        """새 단축키를 등록한다. 실패 시 False 반환."""
        self.unregister()
        parsed = parse_hotkey(hotkey)
        if parsed is None:
            self.error.emit(f"해석할 수 없는 단축키: {hotkey}")
            return False
        mods, vk = parsed
        ok = ctypes.windll.user32.RegisterHotKey(None, _HOTKEY_ID, mods | MOD_NOREPEAT, vk)
        if not ok:
            # 대부분 다른 프로그램이 같은 조합을 이미 전역 등록해 둔 경우다.
            self.error.emit(f"단축키 등록 실패(다른 프로그램이 사용 중일 수 있음): {hotkey}")
            return False
        self._registered = True
        self._current_hotkey = hotkey
        return True

    def unregister(self):
        if self._registered:
            ctypes.windll.user32.UnregisterHotKey(None, _HOTKEY_ID)
            self._registered = False
        self._current_hotkey = None
