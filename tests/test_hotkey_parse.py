"""parse_hotkey 회귀 테스트 — 설정 화면(HotkeyCaptureEdit)이 만드는 문자열 형식이
RegisterHotKey 인자로 제대로 풀리는지. 파싱이 틀리면 단축키 등록이 소리 없이
실패해서 앱을 열 방법이 트레이 클릭밖에 안 남는다."""
from hotkey_manager import parse_hotkey, MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN


def test_default_hotkey():
    assert parse_hotkey("ctrl+space") == (MOD_CONTROL, 0x20)


def test_three_modifiers():
    assert parse_hotkey("ctrl+alt+space") == (MOD_CONTROL | MOD_ALT, 0x20)
    assert parse_hotkey("ctrl+shift+f5") == (MOD_CONTROL | MOD_SHIFT, 0x74)


def test_windows_modifier():
    assert parse_hotkey("windows+space") == (MOD_WIN, 0x20)


def test_letter_key():
    mods, vk = parse_hotkey("ctrl+k")
    assert mods == MOD_CONTROL
    assert vk == ord("K")  # VkKeyScanW 는 대문자 기준 가상 키코드를 준다


def test_case_insensitive():
    assert parse_hotkey("Ctrl+Space") == parse_hotkey("ctrl+space")


def test_invalid_forms():
    assert parse_hotkey("ctrl+") is None       # 실제 키 없음
    assert parse_hotkey("ctrl") is None        # 수정자만
    assert parse_hotkey("ctrl+a+b") is None    # 키가 두 개
    assert parse_hotkey("ctrl+f99") is None    # 없는 F키
    assert parse_hotkey("") is None
