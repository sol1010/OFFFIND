"""OFFFIND 진입점.

폴더 안의 엑셀(xlsx/xlsm) · PDF 파일 내용을 검색하는 항상-실행형 트레이 앱.
전역 단축키(기본 ctrl+alt+space)로 검색창을 열고 닫는다.
"""
import os
import sys
import time

from PySide6.QtCore import QThread, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

import win_focus
from config import Settings
from icons import ICON_PATH
from indexer import Indexer
from index_worker import IndexWorker
from hotkey_manager import HotkeyManager
from search_window import SearchWindow
from settings_dialog import SettingsDialog
from tray_icon import TrayIcon

AUTO_REINDEX_INTERVAL_MS = 5 * 60 * 1000  # 5분마다 새 파일/폴더 변경사항 자동 반영
# 검색창을 열 때마다(단축키로 껐다 켰다 할 때마다) 매번 등록 폴더 전체(수십만 개 파일)를
# 다시 훑으면 그 스캔이 끝날 때까지 검색까지 같이 느려진다(같은 프로세스 안에서 GIL을
# 나눠 쓰니까) — 실측으로 확인함. 방금 갱신했으면 창을 여닫는 정도로는 다시 훑지 않는다.
MIN_AUTO_REINDEX_GAP_SEC = 90


class App:
    def __init__(self):
        self.settings = Settings.load()
        self.indexer = Indexer()

        self.search_window = SearchWindow(self.indexer, self.settings)
        self.tray = TrayIcon()
        self.hotkey_manager = HotkeyManager()

        self._worker = None
        self._settings_dialog = None
        self._pending_rest_modes = []
        self._pending_all_modes = []
        self._pending_silent = True
        self._last_index_finished_at = float("-inf")  # 시작 직후 첫 색인은 스로틀 없이 항상 실행
        self._active_indexing_counts = {}  # path_norm -> 아직 안 끝난 등록 개수(중복 등록 대비)

        self._reindex_timer = QTimer()
        self._reindex_timer.setInterval(AUTO_REINDEX_INTERVAL_MS)
        self._reindex_timer.timeout.connect(self._start_indexing)
        self._reindex_timer.start()

        self._wire_signals()
        self.tray.show()

        if not self._register_hotkey(self.settings.hotkey):
            self.tray.show_message(
                "단축키 등록 실패",
                f"'{self.settings.hotkey}' 단축키를 등록하지 못했습니다. 옵션에서 변경해주세요.",
            )

        if self.settings.folders:
            self._start_indexing()
        else:
            self.open_settings()

    def _wire_signals(self):
        self.tray.open_requested.connect(self._open_search_window)
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.refresh_requested.connect(lambda: self._start_indexing(silent=False))
        self.tray.quit_requested.connect(self.quit)

        self.hotkey_manager.triggered.connect(self._toggle_search_window)
        self.search_window.open_settings_requested.connect(self.open_settings)

    def _register_hotkey(self, hotkey: str) -> bool:
        return self.hotkey_manager.register(hotkey)

    def _open_search_window(self):
        self.search_window.show_window()
        self._start_indexing()  # 새로 생긴 파일/폴더가 바로 검색되도록 열 때마다 백그라운드로 갱신

    def _toggle_search_window(self):
        if self._settings_dialog is not None and self._settings_dialog.isVisible():
            # 설정창(일반 모달)이 떠 있는 동안 단축키로 검색창(항상 위)을 다시
            # 띄우면 두 창이 포커스를 두고 다투면서 검색창이 엉망으로 보이다가
            # 저절로 꺼지는 문제가 있었다 — 설정창이 떠 있으면 그쪽을 앞으로
            # 가져오기만 하고 검색창은 건드리지 않는다.
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        opening = not self.search_window.isVisible()
        self.search_window.toggle()
        if opening:
            self._start_indexing()

    # ---------- 색인 ----------
    def _folder_modes_split(self):
        """(선택된 폴더 folder_modes, 나머지 등록된 폴더 folder_modes) 를 반환한다.
        "선택된"은 검색창 폴더 칩에서 지금 포함된(folder_enabled) 폴더 — 지금 하는
        검색과 직접 관련 있어서 진행 상황/완료 알림을 검색창에 보여줄 대상이다.
        "나머지"는 등록은 됐지만 검색창에서 지금 꺼둔 폴더 — 색인 자체는 최신으로
        유지하되, 검색창에는 아무것도 보여주지 않는다. 이 구분이 없으면 지금 관심도
        없는(꺼둔) 폴더가 백그라운드에서 스캔될 때도 "색인 갱신됨, 다시 검색하세요"
        배너가 떠서 혼란스럽다."""
        selected, rest = [], []
        for f in self.settings.folders:
            filename_only = self.settings.folder_filename_only.get(f, False)
            members = self.settings.folder_groups.get(f)
            paths = members if members else [f]
            target = selected if self.settings.folder_enabled.get(f, True) else rest
            target.extend((p, filename_only) for p in paths)
        # 파일명만 색인하는(빠른) 폴더를 내용까지 읽는(느린 — 엑셀/PDF/워드/PPT를
        # 직접 열어야 함) 폴더보다 먼저 처리한다. rebuild()는 폴더 하나를 다 훑을
        # 때마다 바로 커밋하므로(folder_done), 이렇게 순서만 바꿔도 빠른 폴더들이
        # 검색 가능해지는 시점이 훨씬 앞당겨진다 — 안 그러면 등록 순서상 느린 폴더가
        # 먼저 걸리면 빠른 폴더까지 덩달아 한참 기다려야 했다. sort는 안정 정렬이라
        # 같은 모드 안에서의 순서는 그대로 유지된다.
        selected.sort(key=lambda t: 0 if t[1] else 1)
        rest.sort(key=lambda t: 0 if t[1] else 1)
        return selected, rest

    def _start_indexing(self, silent: bool = True):
        if self._worker is not None and self._worker.isRunning():
            return
        if not self.settings.folders:
            if not silent:
                self.tray.show_message("검색 폴더 없음", "옵션에서 검색할 폴더를 먼저 추가해주세요.")
            return
        # silent=True 는 창 열기/토글/주기 타이머처럼 "자동" 트리거다. 방금 갱신을
        # 마쳤으면(전체 스캔은 61만 개 기준 20~30초대) 또 도는 대신 지금 있는 색인을
        # 그대로 쓴다. silent=False(수동 새로고침, 폴더 설정 변경)는 스로틀하지 않는다.
        if silent and (time.perf_counter() - self._last_index_finished_at) < MIN_AUTO_REINDEX_GAP_SEC:
            return

        selected_modes, rest_modes = self._folder_modes_split()
        self._pending_rest_modes = rest_modes
        self._pending_silent = silent
        # 선택된 폴더는 IndexWorker 하나로, 나머지는 별도의 IndexWorker로 따로
        # 돌리는데(검색창에 진행상황을 보여줄 대상을 구분하려고), 같은 폴더가
        # 한쪽엔 내용 모드로 다른 쪽엔 파일명만 모드로 중복 등록돼 있을 수 있다
        # (그룹으로 같은 경로를 두 번 추가한 경우). 그 중복을 감지하는 기준은 항상
        # "이번에 도는 폴더들"이 아니라 "등록된 폴더 전체"여야 하므로, 두 IndexWorker
        # 모두에게 이 합친 목록을 같이 넘긴다 — 안 그러면 한쪽 라운드가 다른 쪽이
        # 방금 색인한 내용을 "여긴 파일명만인 줄 알고" 지워버린다(실측으로 발견한
        # 실제 사고: 다운로드 폴더 내용 색인이 몇 분씩 걸려 끝나자마자, 뒤이어 도는
        # "나머지" 라운드가 같은 폴더의 파일명만 중복 등록분을 처리하면서 방금 쌓인
        # content_entries를 통째로 삭제함).
        all_modes = selected_modes + rest_modes
        self._pending_all_modes = all_modes

        # 이번 라운드에서 건드릴 폴더 전부(선택된 것 + 나머지)를 미리 "아직 안 끝남"
        # 으로 표시해 둔다 — 안에서 폴더 하나하나 끝날 때마다(folder_done 시그널)
        # 여기서 지워나간다. 파일명만 등록된 빠른 폴더가 뒤에 처리되는 무거운(내용
        # 파싱) 폴더와 같은 배치에 있어도, 그 폴더 자체가 끝나면 바로 로딩 표시가
        # 내려가야 하는데(그 전엔 배치 전체가 끝나야만 지워져서 안 지워지는 것처럼
        # 보였다 — 실측으로 확인함), 그러려면 폴더별로 따로 추적해야 한다.
        #
        # 추적 단위는 "경로"가 아니라 "등록"(경로 + 검색 방식)이다. 같은 폴더를
        # 파일명용·내용용으로 두 번 등록할 수 있는데, 경로만으로 세면 두 등록이
        # 한 덩어리로 묶여서 둘 다 끝나야 로딩 표시가 꺼진다 — 파일명 패스가 몇 초
        # 만에 끝나도, 뒤로 밀린 내용 패스(정렬상 항상 마지막)가 끝날 때까지 두 칩이
        # 같이 돌아서 "속도 차이가 하나도 안 보이는" 문제가 있었다(실측으로 확인함).
        # 등록별로 세면 각 칩이 자기 패스가 끝나는 즉시 꺼진다.
        self._active_indexing_counts = {}
        for path, fo in selected_modes + rest_modes:
            key = (os.path.normcase(os.path.normpath(path)), bool(fo))
            self._active_indexing_counts[key] = self._active_indexing_counts.get(key, 0) + 1
        self.search_window.set_indexing_paths(set(self._active_indexing_counts))

        if selected_modes:
            self._worker = IndexWorker(self.indexer, selected_modes, all_folder_modes=all_modes)
            self._worker.progress.connect(self.search_window.on_index_progress)
            self._worker.folder_done.connect(self._on_folder_indexed)
            self._worker.folder_progress.connect(self.search_window.set_folder_progress)
            self._worker.finished_ok.connect(self._on_selected_indexing_finished)
            self.search_window.on_index_started()
            # 낮은 우선순위로 돌려서, 무거운 색인이 도는 동안에도 검색(다른 스레드)이
            # OS 스케줄러에서 밀리지 않게 한다.
            self._worker.start(QThread.LowPriority)
        else:
            self._start_rest_indexing()

    def _on_folder_indexed(self, path_norm: str, filename_only: bool):
        key = (path_norm, bool(filename_only))
        remaining = self._active_indexing_counts.get(key, 0) - 1
        if remaining <= 0:
            self._active_indexing_counts.pop(key, None)
        else:
            self._active_indexing_counts[key] = remaining
        self.search_window.set_indexing_paths(set(self._active_indexing_counts))

    def _on_selected_indexing_finished(self, count: int, changed: bool):
        self.search_window.on_index_finished(count, changed)
        self._start_rest_indexing()

    def _start_rest_indexing(self):
        rest_modes = self._pending_rest_modes
        silent = self._pending_silent
        if not rest_modes:
            self._last_index_finished_at = time.perf_counter()
            self._active_indexing_counts.clear()
            self.search_window.set_indexing_paths(set())
            if not silent:
                self.tray.show_message("색인 완료", f"{self.indexer.file_count()}개 파일이 색인되었습니다.")
            return
        self._worker = IndexWorker(self.indexer, rest_modes, all_folder_modes=self._pending_all_modes)
        self._worker.folder_done.connect(self._on_folder_indexed)
        self._worker.folder_progress.connect(self.search_window.set_folder_progress)
        self._worker.finished_ok.connect(lambda count, changed: self._on_index_finished(count, silent))
        self._worker.start(QThread.LowPriority)

    def _on_index_finished(self, count: int, silent: bool = True):
        self._last_index_finished_at = time.perf_counter()
        self._active_indexing_counts.clear()
        self.search_window.set_indexing_paths(set())
        if not silent:
            self.tray.show_message("색인 완료", f"{count}개 파일이 색인되었습니다.")

    # ---------- 옵션 ----------
    def open_settings(self, focus: str = ""):
        """focus 가 있으면 그 설정이 있는 페이지를 열고 해당 칸을 잠깐 강조한다
        (검색 결과의 "표시 개수 바꾸기" 링크에서 넘어올 때)."""
        # 검색창은 기본적으로 "항상 위"(always_on_top)라서, 이 창이 아직 떠 있는
        # 채로 설정창(항상 위 아님)을 띄우려 하면 포그라운드 쟁탈전이 나서
        # 설정창이 안 보이거나 멈춘 것처럼 보였다(실제로 겪음) — WindowDeactivate로
        # 검색창이 알아서 닫히길 기다리지 말고, 여기서 미리 확실히 닫아둔다.
        self.search_window.hide_window()
        old_hotkey = self.settings.hotkey
        old_folders = list(self.settings.folders)
        old_folder_modes = dict(self.settings.folder_filename_only)
        old_folder_groups = {k: list(v) for k, v in self.settings.folder_groups.items()}

        dialog = SettingsDialog(self.settings, focus=focus)
        self._settings_dialog = dialog
        # winId()가 네이티브 창을 만들어내는 부작용이 있다 — 아직 화면에 보이기
        # 전(첫 show() 이전)에 미리 호출해서, 애니메이션을 끄는 시점이 창이
        # 뜨기 전이 되도록 한다(뜬 다음에 꺼봐야 이미 한 번 번쩍인 뒤라 소용없다).
        win_focus.disable_show_animation(int(dialog.winId()))

        def on_saved():
            if self.settings.hotkey != old_hotkey:
                if not self._register_hotkey(self.settings.hotkey):
                    QMessageBox.warning(
                        dialog, "단축키 등록 실패",
                        f"'{self.settings.hotkey}' 단축키를 등록하지 못했습니다. 다른 조합을 시도해주세요.",
                    )
                    self._register_hotkey(old_hotkey)
                    self.settings.hotkey = old_hotkey
                    self.settings.save()

            self.search_window.apply_settings()

            if (self.settings.folders != old_folders
                    or self.settings.folder_filename_only != old_folder_modes
                    or self.settings.folder_groups != old_folder_groups):
                self._start_indexing(silent=False)

        dialog.saved.connect(on_saved)
        # 이 창도 검색창과 마찬가지로 전역 단축키/트레이 클릭 등 "다른 창에서 온
        # 액션"으로 뜬다 — Windows가 포그라운드 잠금 때문에 Qt의 기본 show()만으로는
        # 실제 화면 맨 앞으로 안 올라오고 안 보이는 것처럼 남을 수 있다(실제로 겪음).
        # exec() 전에 따로 show()를 먼저 부르면(한때 그렇게 했었다) 모달 전환이 두
        # 단계로 나뉘면서 흰 화면이 잠깐 번쩍이고 창이 순간이동하는 것처럼 보이는
        # 부작용이 있었다 — show()는 exec() 안에서 딱 한 번만 일어나게 두고,
        # 강제 전경화만 다음 이벤트 루프 틱으로 미뤄서 따로 적용한다.
        QTimer.singleShot(0, lambda: win_focus.force_foreground(int(dialog.winId())))
        dialog.exec()

    def quit(self):
        self.hotkey_manager.unregister()
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            # cancel_check는 폴더 안 항목 사이(파일 하나 파싱하는 도중은 아님)에서만
            # 확인되므로, 마침 큰 엑셀/PDF/워드/PPT 하나를 파싱 중이면 취소 신호를
            # 줘도 몇 초씩 안 끝날 수 있다(실측상 파일당 여러 초 걸리는 경우가 흔함).
            # wait()의 반환값을 안 보고 그냥 QApplication.quit()으로 넘어가면, 아직
            # 살아있는 백그라운드 스레드가 SQLite 연결을 쥔 채로 프로세스가 종료될
            # 수 있다 — 이 시점엔 데이터 보존보다 확실한 종료가 우선이므로, 충분히
            # 기다려도 안 끝나면 강제 종료한다(index_cache.db는 WAL + 300개 단위
            # 중간 커밋이라 최악의 경우도 최근 배치 하나만 다시 훑으면 된다).
            if not self._worker.wait(5000):
                self._worker.terminate()
                self._worker.wait(2000)
        QApplication.quit()


def _set_taskbar_identity():
    """Windows 작업표시줄에 파이썬 아이콘 대신 이 앱 아이콘이 뜨게 한다.

    작업표시줄은 창을 AppUserModelID로 묶어서 아이콘을 정하는데, 그 값을 안 정해
    주면 실행 파일(python.exe/pythonw.exe)의 것을 그대로 쓴다 — setWindowIcon을
    아무리 해도 작업표시줄만 파이썬 아이콘으로 남는 이유다(실제로 겪음).
    창이 하나라도 만들어지기 전에 불러야 한다."""
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OFFFIND.Search.App")
    except Exception:
        pass  # 이 설정에 실패해도 앱 동작 자체에는 지장이 없다


def main():
    _set_taskbar_identity()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(ICON_PATH))
    _controller = App()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
