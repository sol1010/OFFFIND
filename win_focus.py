"""전역 단축키(백그라운드 훅)로 창을 띄울 때도 확실히 키보드 포커스를 가져오도록
Windows API로 강제 전경화한다. Qt의 activateWindow()만으로는 다른 프로세스에서
전환된 직후 포커스가 넘어오지 않는 경우가 있다."""
import ctypes

DWMWA_TRANSITIONS_FORCEDISABLED = 3


def disable_show_animation(hwnd: int):
    """새 네이티브 창이 뜰 때 Windows(DWM)의 페이드인 애니메이션을 끈다. 이
    애니메이션 도중에는 Qt가 아직 스타일시트로 칠하지 않은 창의 기본(흰색)
    배경이 그대로 화면에 잠깐 보인다 — QSS/팔레트를 아무리 미리 맞춰놔도 이
    애니메이션 자체가 원인이라 안 없어졌다(실제로 겪음)."""
    try:
        dwmapi = ctypes.windll.dwmapi
        value = ctypes.c_int(1)
        dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_TRANSITIONS_FORCEDISABLED,
                                      ctypes.byref(value), ctypes.sizeof(value))
    except OSError:
        pass


def force_foreground(hwnd: int):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    foreground_hwnd = user32.GetForegroundWindow()
    current_thread = kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(foreground_hwnd, None)

    attached = False
    if foreground_thread and foreground_thread != current_thread:
        attached = bool(user32.AttachThreadInput(foreground_thread, current_thread, True))

    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        user32.BringWindowToTop(hwnd)
    finally:
        # 이 사이에 예외가 나도(예: 그 순간 대상 창이 닫힘) 스레드 입력 연결은 반드시
        # 풀어야 한다 — 안 풀면 이 프로세스의 입력 스레드가 다른 프로세스의 입력
        # 큐에 계속 붙어있는 채로 남아서, 이후 포커스/입력이 미묘하게 꼬일 수 있다.
        if attached:
            user32.AttachThreadInput(foreground_thread, current_thread, False)
