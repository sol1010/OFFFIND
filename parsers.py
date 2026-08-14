"""엑셀(xlsx/xlsm) / PDF / Word(docx) / PowerPoint(pptx) 파일에서 검색 가능한
텍스트 조각(entry)을 추출. 구버전 바이너리 포맷(.xls/.doc/.ppt)은 대상 라이브러리가
아예 못 읽어서 지원하지 않는다(README 알려진 제약에 명시)."""
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
                    "sheet": sheet.title,
                    "row": row_idx,
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
                    "page": i,
                })
    except Exception:
        return entries
    return entries


def extract_docx(path: str) -> List[Dict[str, Any]]:
    """문단 단위로 텍스트를 추출한다(표 안 텍스트는 표 단위로 별도 entry)."""
    import docx

    entries = []
    try:
        doc = docx.Document(path)
    except Exception:
        return entries

    for i, para in enumerate(doc.paragraphs, start=1):
        text = para.text.strip()
        if not text:
            continue
        entries.append({
            "location": f"{i}번째 문단",
            "text": text,
            "paragraph": i,
        })

    # 표 안 텍스트는 doc.paragraphs 에 안 잡힌다(별도 요소라서) — 놓치면 안 되니
    # 표는 표대로 행 단위로 추출한다. 표 안에서 더블클릭하면 문서는 열리지만
    # 문단 이동과 달리 그 행으로 바로 스크롤하는 기능은 없다(paragraph 키가 없음).
    for t_idx, table in enumerate(doc.tables, start=1):
        for r_idx, row in enumerate(table.rows, start=1):
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if not cells:
                continue
            entries.append({
                "location": f"표{t_idx} · {r_idx}행",
                "text": " | ".join(cells),
            })
    return entries


def extract_pptx(path: str) -> List[Dict[str, Any]]:
    """슬라이드 단위로 텍스트를 추출한다(표/발표자 노트 포함)."""
    import pptx

    entries = []
    try:
        prs = pptx.Presentation(path)
    except Exception:
        return entries

    for i, slide in enumerate(prs.slides, start=1):
        parts = []
        for shape in slide.shapes:
            if shape.has_text_frame and shape.text_frame.text.strip():
                parts.append(shape.text_frame.text.strip())
            if shape.has_table:
                for row in shape.table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                parts.append(notes)
        text = " ".join(" ".join(parts).split())
        if not text:
            continue
        entries.append({
            "location": f"{i}번 슬라이드",
            "text": text,
            "slide": i,
        })
    return entries


EXTRACTORS = {
    ".xlsx": extract_xlsx,
    ".xlsm": extract_xlsx,
    ".pdf": extract_pdf,
    ".docx": extract_docx,
    ".pptx": extract_pptx,
}
