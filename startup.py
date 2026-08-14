"""윈도우 시작프로그램 등록/해제 (레지스트리 Run 키 사용)."""
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "OFFFIND"
_OLD_APP_NAMES = ["KS-Finder", "FileSearcher"]


def _command() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    script = sys.argv[0]
    return f'"{sys.executable}" "{script}"'


def _migrate_old_registry_entry():
    """예전 이름(KS-Finder, FileSearcher)으로 자동 시작이 등록돼 있으면 새 이름으로 옮겨준다
    (그냥 두면 등록 여부를 새 이름으로만 확인하니 옵션 창엔 꺼진 것처럼 보이는데,
    실제로는 예전 이름 항목이 남아있어 시작 시 중복 실행될 수 있다)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_ALL_ACCESS) as key:
            for old_name in _OLD_APP_NAMES:
                try:
                    old_value, _ = winreg.QueryValueEx(key, old_name)
                except FileNotFoundError:
                    continue
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, old_value)
                winreg.DeleteValue(key, old_name)
                return
    except OSError:
        pass


_migrate_old_registry_entry()


def set_startup(enabled: bool):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass


def is_startup_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
