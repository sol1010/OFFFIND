@echo off
REM KS-Finder 배포용 빌드 (PyInstaller). 콘솔창 없이 실행됨.
REM
REM --onefile 대신 --onedir을 쓴다: onefile은 실행할 때마다 임시 폴더에 압축을
REM 풀어내는 방식이라(자기 자신을 해제하는 동작 패턴) 백신/스마트스크린이 이걸
REM 실제 악성코드 드로퍼와 비슷하다고 오탐하는 경우가 매우 흔하다. onedir은
REM 그런 실행 시점 압축 해제가 없어서 오탐 확률이 훨씬 낮다.
REM --noupx: UPX로 실행파일을 압축하면 이것도 백신 오탐의 흔한 원인이라 끈다.
REM
REM 주의: 이 빌드는 서명(코드사이닝)이 안 된 exe라서, 처음 실행할 때 윈도우
REM 스마트스크린이 "알 수 없는 게시자" 경고를 띄우는 것 자체는 막을 수 없다
REM (정식 코드 서명 인증서가 있어야 없앨 수 있음). "자세히" -> "실행" 으로 넘기면 됨.
REM pandas/numpy/numba는 코드에서 쓰지 않는데도 pdfplumber가 선택적으로 참조하는
REM 부분 때문에 PyInstaller가 자동으로 딸려 들어가 용량만 40MB+ 키운다 — 제외한다.
pyinstaller --noconfirm --onedir --windowed --noupx ^
    --name KS-Finder ^
    --icon icon_ks.ico ^
    --add-data "icon_ks.png;." ^
    --exclude-module pandas ^
    --exclude-module numpy ^
    --exclude-module numba ^
    --exclude-module matplotlib ^
    main.py
echo.
echo 빌드 완료: dist\KS-Finder\KS-Finder.exe
pause
