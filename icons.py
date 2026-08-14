"""앱 전역에서 공유하는 아이콘 경로.

PyInstaller로 묶은 exe에서 실행될 때는(onedir/onefile 모두) __file__ 기준 경로가
아니라 sys._MEIPASS(빌드 시 --add-data로 함께 넣은 리소스가 풀리는 위치)를 써야
아이콘 파일을 찾을 수 있다. 일반 파이썬 스크립트로 실행할 때는 그냥 이 파일과
같은 폴더를 쓴다.
"""
import os
import sys


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


ICON_PATH = os.path.join(_base_dir(), "icon_ks.png")
