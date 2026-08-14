"""백그라운드 스레드에서 Indexer.search 를 실행.

검색어 길이에 따라 SQLite 처리 방식이 갈린다 — 3글자 이상은 FTS5(trigram)
인덱스를 타서 몇 ms 안에 끝나지만, 1~2글자는 trigram 자체가 안 만들어져서
LIKE 로 전체를 훑는데 이건 1초 가까이 걸릴 수 있다. 메인(UI) 스레드에서
그대로 부르면 그 시간 동안 검색창이 멈춘 것처럼 보인다."""
from PySide6.QtCore import QThread, Signal

from indexer import Indexer


class SearchWorker(QThread):
    finished_ok = Signal(str, list)  # (query, results) - 늦게 도착한 결과를 구분하려 query 를 같이 넘긴다

    def __init__(self, indexer: Indexer, query: str, limit: int, folder_modes):
        super().__init__()
        self.indexer = indexer
        self.query = query
        self.limit = limit
        self.folder_modes = folder_modes

    def run(self):
        results = self.indexer.search(self.query, limit=self.limit, folder_modes=self.folder_modes)
        self.finished_ok.emit(self.query, results)
