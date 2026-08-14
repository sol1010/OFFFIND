"""검색 결과를 열 때, 가능하면 파일만 여는 게 아니라 검색된 위치(엑셀 셀/PDF
페이지)로 바로 이동시킨다. 위치 정보가 없거나, 이동 방법을 모르는 뷰어거나,
뭔가 실패하면 예전처럼 그냥 기본 프로그램으로 파일만 연다 — 어떤 경우에도
"파일이 안 열리는" 상황은 생기지 않는다."""
import os
import subprocess
import winreg

# 명령줄로 페이지 이동을 지원한다고 알려진 PDF 뷰어들(실행파일 이름 기준, 소문자).
# 목록에 없는 뷰어(예: 알PDF 등)는 이동 없이 그냥 파일만 연다.
_PDF_PAGE_LAUNCHERS = {
    "msedge.exe": lambda exe, path, page: [exe, f"file:///{path.replace(os.sep, '/')}#page={page}"],
    "acrobat.exe": lambda exe, path, page: [exe, "/A", f"page={page}", path],
    "acrord32.exe": lambda exe, path, page: [exe, "/A", f"page={page}", path],
    "sumatrapdf.exe": lambda exe, path, page: [exe, "-page", str(page), path],
    "foxitreader.exe": lambda exe, path, page: [exe, "/A", f"page={page}", path],
    "foxitpdfreader.exe": lambda exe, path, page: [exe, "/A", f"page={page}", path],
}


def _default_handler_exe(ext: str):
    """이 확장자를 더블클릭했을 때 실제로 실행되는 exe 경로를 레지스트리에서 찾는다."""
    prog_id = None
    try:
        key_path = rf"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\{ext}\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            prog_id, _ = winreg.QueryValueEx(key, "ProgId")
    except OSError:
        pass

    if not prog_id:
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, ext) as key:
                prog_id, _ = winreg.QueryValueEx(key, "")
        except OSError:
            return None

    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{prog_id}\shell\open\command") as key:
            command, _ = winreg.QueryValueEx(key, "")
    except OSError:
        return None

    command = command.strip()
    if command.startswith('"'):
        return command[1:command.index('"', 1)]
    return command.split(" ")[0]


def _open_pdf(path: str, page):
    if page:
        exe = _default_handler_exe(".pdf")
        launcher = _PDF_PAGE_LAUNCHERS.get(os.path.basename(exe).lower()) if exe else None
        if launcher:
            try:
                subprocess.Popen(launcher(exe, path, page))
                return
            except OSError:
                pass
    os.startfile(path)


def _open_excel(path: str, sheet, row):
    if sheet and row:
        try:
            import win32com.client
            # Dispatch()는 이미 떠 있는 Excel 인스턴스에 그대로 붙어버릴 수 있는데,
            # 그러면 사용자가 따로 작업 중이던 Excel 창에 관여하게 되고(실제로 테스트
            # 중 선택 셀이 엉뚱한 곳으로 잡히는 문제가 있었다), 새 창으로 열리지 않을
            # 수도 있다. DispatchEx로 항상 새 Excel 프로세스를 띄운다.
            excel = win32com.client.DispatchEx("Excel.Application")
            excel.Visible = True
            wb = excel.Workbooks.Open(path)
            try:
                ws = wb.Worksheets(sheet)
                ws.Activate()
                ws.Cells(row, 1).Select()
            except Exception:
                pass  # 시트/행 이동만 실패한 것 — 파일 자체는 이미 열렸으니 그냥 둔다
            return
        except Exception:
            pass  # Excel COM 자동화 실패(Excel 미설치 등) — 아래에서 그냥 파일만 연다
    os.startfile(path)


def open_result(result: dict):
    """검색 결과 항목을 연다."""
    path = result["path"]
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".pdf" and result.get("page"):
            _open_pdf(path, result["page"])
        elif ext in (".xlsx", ".xlsm") and result.get("sheet"):
            _open_excel(path, result["sheet"], result.get("row"))
        else:
            os.startfile(path)
    except OSError:
        pass
