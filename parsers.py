"""엑셀(xlsx/xlsm/xls) / PDF 파일에서 검색 가능한 텍스트 조각(entry)을 추출."""
from typing import List, Dict, Any


def extract_xlsx(path: str) -> List[Dict[str, Any]]:
    """시트별 각 행을 하나의 검색 entry로 반환."""
    import openpyxl

    entries = []
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception:
        return entries

    try:
        for sheet in wb.worksheets:
            for row_idx, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                cells = [str(c) for c in row if c is not None and str(c).strip() != ""]
                if not cells:
                    continue
                text = " | ".join(cells)
                entries.append({
                    "location": f"{sheet.title} · {row_idx}행",
                    "text": text,
                })
    finally:
        wb.close()
    return entries


def extract_pdf(path: str) -> List[Dict[str, Any]]:
    """페이지별 텍스트를 하나의 검색 entry로 반환."""
    import pdfplumber

    entries = []
    try:
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                text = " ".join(text.split())
                if not text:
                    continue
                entries.append({
                    "location": f"{i}페이지",
                    "text": text,
                })
    except Exception:
        return entries
    return entries


EXTRACTORS = {
    ".xlsx": extract_xlsx,
    ".xlsm": extract_xlsx,
    ".pdf": extract_pdf,
}
