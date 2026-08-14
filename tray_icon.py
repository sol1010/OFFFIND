"""시스템 트레이 아이콘 + 메뉴."""
from PySide6.QtCore import Signal, QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QSystemTrayIcon, QMenu

from icons import ICON_PATH


class TrayIcon(QObject):
    open_requested = Signal()
    settings_requested = Signal()
    refresh_requested = Signal()
    quit_requested = Signal()

    def __init__(self):
        super().__init__()
        self.tray = QSystemTrayIcon(QIcon(ICON_PATH))
        self.tray.setToolTip("OFFFIND")

        menu = QMenu()
        open_action = menu.addAction("검색창 열기")
        open_action.triggered.connect(self.open_requested.emit)
        refresh_action = menu.addAction("지금 색인 새로고침")
        refresh_action.triggered.connect(self.refresh_requested.emit)
        settings_action = menu.addAction("옵션…")
        settings_action.triggered.connect(self.settings_requested.emit)
        menu.addSeparator()
        quit_action = menu.addAction("종료")
        quit_action.triggered.connect(self.quit_requested.emit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_activated)

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            self.open_requested.emit()

    def show(self):
        self.tray.show()

    def show_message(self, title: str, message: str):
        self.tray.showMessage(title, message, QSystemTrayIcon.Information, 3000)
