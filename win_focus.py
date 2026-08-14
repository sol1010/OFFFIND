"""전역 단축키(백그라운드 훅)로 창을 띄울 때도 확실히 키보드 포커스를 가져오도록
Windows API로 강제 전경화한다. Qt의 activateWindow()만으로는 다른 프로세스에서
전환된 직후 포커스가 넘어오지 않는 경우가 있다."""
import ctypes


def force_foreground(hwnd: int):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    foreground_hwnd = user32.GetForegroundWindow()
    current_thread = kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(foreground_hwnd, None)

    attached = False
    if foreground_thread and foreground_thread != current_thread:
        attached = bool(user32.AttachThreadInput(foreground_thread, current_thread, True))

    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)

    if attached:
        user32.AttachThreadInput(foreground_thread, current_thread, False)
