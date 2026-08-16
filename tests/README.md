# 테스트

```bash
python -m pytest tests -q
```

`pytest` 가 없으면 `python -m pip install pytest` 로 설치한다.

## 왜 이 테스트들인가

경로와 등록 조합의 경우의 수가 많아서(부모/자식 중첩, 같은 폴더 이중 등록,
드라이브 루트, 비활성 폴더, 파싱 실패…) 눈으로 회귀 검증하면 반드시 놓친다.
여기 있는 케이스는 대부분 **실제로 겪은 버그**를 그대로 굳힌 것이다.

- `test_search_scope.py` — 검색 범위. 특히 `test_limit_never_drops_in_scope_results`
  는 "SQL LIMIT 이 폴더 범위보다 먼저 걸려서 결과가 통째로 사라지던" 버그를 잡는다
  (실측 당시 `C:\Program Files` 안의 2,811건이 0건으로 나왔다).
- `test_indexing.py` — 색인. 파싱 실패를 "내용 0개인 파일"로 저장해서 그 파일이
  영구히 검색에서 빠지던 버그, 중복 등록 시 행이 늘어나던 문제, 삭제 정리,
  예전 스키마 DB 자동 이관을 확인한다.

## 격리

`conftest.py` 의 `offfind` 픽스처가 `APPDATA` 를 임시 폴더로 돌린 뒤 모듈을 다시
임포트한다. 테스트가 **사용자의 실제 색인 DB나 설정을 건드리지 않는다** —
픽스처 안에 그걸 보장하는 단언이 들어 있다.
