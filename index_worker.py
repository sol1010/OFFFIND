"""백그라운드 스레드에서 Indexer.rebuild 를 실행."""
from PySide6.QtCore import QThread, Signal

from indexer import Indexer


class IndexWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(int)

    def __init__(self, indexer: Indexer, folder_modes):
        super().__init__()
        self.indexer = indexer
        self.folder_modes = list(folder_modes)
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        self.indexer.rebuild(
            self.folder_modes,
            progress=lambda name: self.progress.emit(name),
            cancel_check=lambda: self._cancelled,
        )
        self.finished_ok.emit(self.indexer.file_count())
