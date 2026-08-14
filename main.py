"""OFFFIND 진입점.

폴더 안의 엑셀(xlsx/xlsm) · PDF 파일 내용을 검색하는 항상-실행형 트레이 앱.
전역 단축키(기본 ctrl+alt+space)로 검색창을 열고 닫는다.
"""
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from config import Settings
from icons import ICON_PATH
from indexer import Indexer
from index_worker import IndexWorker
from hotkey_manager import HotkeyManager
from search_window import SearchWindow
from settings_dialog import SettingsDialog
from tray_icon import TrayIcon

AUTO_REINDEX_INTERVAL_MS = 5 * 60 * 1000  # 5분마다 새 파일/폴더 변경사항 자동 반영


class App:
    def __init__(self):
        self.settings = Settings.load()
        self.indexer = Indexer()

        self.search_window = SearchWindow(self.indexer, self.settings)
        self.tray = TrayIcon()
        self.hotkey_manager = HotkeyManager()

        self._worker = None
        self._settings_dialog = None

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
        opening = not self.search_window.isVisible()
        self.search_window.toggle()
        if opening:
            self._start_indexing()

    # ---------- 색인 ----------
    def _start_indexing(self, silent: bool = True):
        if self._worker is not None and self._worker.isRunning():
            return
        if not self.settings.folders:
            if not silent:
                self.tray.show_message("검색 폴더 없음", "옵션에서 검색할 폴더를 먼저 추가해주세요.")
            return
        folder_modes = []
        for f in self.settings.folders:
            filename_only = self.settings.folder_filename_only.get(f, False)
            members = self.settings.folder_groups.get(f)
            if members:
                folder_modes.extend((m, filename_only) for m in members)
            else:
                folder_modes.append((f, filename_only))
        self._worker = IndexWorker(self.indexer, folder_modes)
        self._worker.finished_ok.connect(lambda count: self._on_index_finished(count, silent))
        self._worker.start()

    def _on_index_finished(self, count: int, silent: bool = True):
        if not silent:
            self.tray.show_message("색인 완료", f"{count}개 파일이 색인되었습니다.")

    # ---------- 옵션 ----------
    def open_settings(self):
        old_hotkey = self.settings.hotkey
        old_folders = list(self.settings.folders)
        old_folder_modes = dict(self.settings.folder_filename_only)
        old_folder_groups = {k: list(v) for k, v in self.settings.folder_groups.items()}

        dialog = SettingsDialog(self.settings)
        self._settings_dialog = dialog

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
        dialog.exec()

    def quit(self):
        self.hotkey_manager.unregister()
        if self._worker is not None and self._worker.isRunning():
            self._worker.cancel()
            self._worker.wait(2000)
        QApplication.quit()


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(QIcon(ICON_PATH))
    _controller = App()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
