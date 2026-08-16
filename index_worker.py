"""백그라운드 스레드에서 Indexer.rebuild 를 실행."""
from PySide6.QtCore import QThread, Signal

from indexer import Indexer


class IndexWorker(QThread):
    progress = Signal(str)
    # 폴더 하나(하위 전체)를 다 훑고 커밋했을 때마다 (path_norm, 파일명만검색여부).
    # 같은 폴더가 파일명/내용 두 가지로 등록될 수 있어서 경로만으로는 어느 등록이
    # 끝났는지 구분이 안 된다 — 검색창 로딩 표시를 등록별로 끄려면 모드가 필요하다.
    folder_done = Signal(str, bool)
    folder_progress = Signal(str, int, int)  # (path_norm, 찾은 개수, 기준 총량)
    finished_ok = Signal(int, bool)  # (전체 파일 수, 새로 생기거나 바뀌거나 지워진 게 있었는지)

    def __init__(self, indexer: Indexer, folder_modes, all_folder_modes=None):
        super().__init__()
        self.indexer = indexer
        self.folder_modes = list(folder_modes)
        # 이번 호출이 훑을 폴더 목록(folder_modes)과, 파일명만/내용까지 중복 등록을
        # 감지하는 데 쓰는 "등록된 폴더 전체" 목록(all_folder_modes)은 다르다 —
        # main.py가 선택된/나머지 폴더를 서로 다른 IndexWorker로 나눠 돌리기 때문에,
        # 이 구분이 없으면 한쪽 라운드가 다른 쪽이 이미 색인한 내용을 지워버린다.
        self.all_folder_modes = list(all_folder_modes) if all_folder_modes is not None else None
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        changed = self.indexer.rebuild(
            self.folder_modes,
            progress=lambda name: self.progress.emit(name),
            cancel_check=lambda: self._cancelled,
            folder_done=lambda path_norm, fo: self.folder_done.emit(path_norm, fo),
            folder_progress=lambda p, f, t: self.folder_progress.emit(p, f, t),
            all_folder_modes=self.all_folder_modes,
        )
        self.finished_ok.emit(self.indexer.file_count(), changed)
