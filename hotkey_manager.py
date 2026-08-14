"""전역(시스템 전체) 단축키 등록/해제. keyboard 라이브러리 사용.

keyboard 의 콜백은 별도 훅 스레드에서 호출되므로, Qt 위젯을 직접 건드리지 않고
시그널을 emit 하여 메인 스레드로 안전하게 전달한다.
"""
from PySide6.QtCore import QObject, Signal


class HotkeyManager(QObject):
    triggered = Signal()
    error = Signal(str)

    def __init__(self):
        super().__init__()
        self._current_hotkey = None

    def register(self, hotkey: str) -> bool:
        """새 단축키를 등록한다. 실패 시 False 반환."""
        import keyboard

        self.unregister()
        try:
            keyboard.add_hotkey(hotkey, self.triggered.emit)
            self._current_hotkey = hotkey
            return True
        except (ValueError, ImportError) as e:
            self.error.emit(str(e))
            return False

    def unregister(self):
        import keyboard

        if self._current_hotkey:
            try:
                keyboard.remove_hotkey(self._current_hotkey)
            except (KeyError, ValueError):
                pass
            self._current_hotkey = None
