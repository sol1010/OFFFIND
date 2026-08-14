"""프레임리스 다크 검색창.
평소에는 둥근 알약 모양 입력창만 보이고, 검색어를 입력하면 그 위쪽으로
결과 목록이 펼쳐진다 (입력창의 하단 위치는 고정, 위로만 자란다).
"""
import os

from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QPoint, QPointF, QRect, QSize, QPropertyAnimation, QEasingCurve
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter,
    QShortcut, QTextCharFormat, QTextLayout,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QToolButton,
    QListWidget, QListWidgetItem, QFrame, QSizePolicy, QPushButton,
    QStyle, QStyledItemDelegate,
)

import win_focus
from file_opener import open_result
from search_worker import SearchWorker

INPUT_HEIGHT = 60
FOLDER_ROW_HEIGHT = 34
FOLDER_ARROW_WIDTH = 26
MIN_QUERY_LENGTH = 2  # 한두 글자로는(특히 영문 한 글자) 결과가 너무 많아져 검색창이 멈춘 것처럼 느려짐
SEARCH_DISPLAY_LIMIT = 5000  # 결과 목록이 화면에 보이는 행만 그리는 방식이라 이 정도는 가볍다
MIN_WIDTH_PERCENT = 0.20  # 검색창 너비를 직접 드래그로 줄일 수 있는 최소치(화면 너비 대비)
MAX_WIDTH_PERCENT = 0.90
RESIZE_HANDLE_WIDTH = 6

STYLE = """
#card {
    background-color: rgba(32, 33, 36, 235);
    border: 1px solid rgba(255, 255, 255, 28);
    border-radius: 26px;
}
#settingsBtn {
    background: transparent;
    border: none;
    color: #9aa0a6;
    font-size: 17px;
    border-radius: 14px;
    padding: 4px;
}
#settingsBtn:hover {
    background-color: rgba(255, 255, 255, 24);
}
#loadingLabel {
    background: transparent;
    border: none;
    color: #9aa0a6;
    font-size: 13px;
}
#staleBanner {
    background-color: rgba(138, 180, 248, 32);
    color: #8ab4f8;
    border: none;
    border-top: 1px solid rgba(255, 255, 255, 20);
    font-size: 12px;
    padding: 7px 20px;
    text-align: left;
}
#staleBanner:hover {
    background-color: rgba(138, 180, 248, 50);
}
#lineEdit {
    background: transparent;
    border: none;
    color: #e8eaed;
    font-size: 16px;
    padding: 0 4px;
}
#resultsList {
    background: transparent;
    border: none;
    outline: none;
    padding: 6px 4px;
}
#resultsList QScrollBar:vertical {
    background: transparent;
    width: 8px;
    margin: 2px 0 2px 0;
}
#resultsList QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 55);
    border-radius: 4px;
    min-height: 24px;
}
#resultsList QScrollBar::handle:vertical:hover {
    background: rgba(255, 255, 255, 80);
}
#resultsList QScrollBar::add-line:vertical, #resultsList QScrollBar::sub-line:vertical {
    height: 0px;
}
#resultsList QScrollBar::add-page:vertical, #resultsList QScrollBar::sub-page:vertical {
    background: transparent;
}
#statusLabel {
    color: #9aa0a6;
    font-size: 12px;
    padding: 6px 20px;
}
#folderChip {
    background-color: rgba(255, 255, 255, 16);
    color: rgba(232, 234, 237, 110);
    border: none;
    border-radius: 10px;
    padding: 3px 10px;
    font-size: 11px;
}
#folderChip:hover {
    background-color: rgba(255, 255, 255, 26);
}
#folderChip:checked {
    background-color: rgba(138, 180, 248, 50);
    color: #e8eaed;
}
#folderPageArrow {
    background-color: rgba(255, 255, 255, 14);
    color: #9aa0a6;
    border: none;
    border-radius: 10px;
    padding: 3px 8px;
    font-size: 11px;
}
#folderPageArrow:hover {
    background-color: rgba(255, 255, 255, 28);
    color: #e8eaed;
}
"""

# 결과 목록 안의 개별 위젯을 만드는 대신 델리게이트가 직접 그리므로,
# 관련 색상/폰트는 여기(파이썬 상수)로 옮겨서 관리한다.
RESULT_BG_HOVER = QColor(255, 255, 255, 22)
NAME_COLOR = QColor("#b0b3b8")
LOC_COLOR = QColor("#8ab4f8")
SNIPPET_COLOR = QColor("#d4d6d9")
FOLDER_COLOR = QColor("#8ab4f8")
FOLDER_COLOR_HOVER = QColor("#aecbfa")
CATEGORY_COLOR = QColor("#9aa0a6")
CATEGORY_COLOR_HOVER = QColor("#c9cdd2")
MEMBER_COLOR = QColor("#bdc7d6")
NOTICE_COLOR = QColor("#f4b95a")
HIGHLIGHT_COLOR = QColor("#1c1d1f")
HIGHLIGHT_BG = QColor("#f4d35e")

# 확장자별 분류(폴더별 그룹 안에서의 정리 순서)
_EXT_CATEGORY = {
    ".pdf": "PDF",
    ".xlsx": "엑셀",
    ".xlsm": "엑셀",
}
CATEGORY_ORDER = ["폴더", "PDF", "엑셀", "기타"]


def _categorize_result(result: dict) -> str:
    if result.get("is_dir"):
        return "폴더"
    ext = os.path.splitext(result["path"])[1].lower()
    return _EXT_CATEGORY.get(ext, "기타")


# 표시할 줄 수만큼만 덮을 대략적인 글자수로 잘라서 그린다 — 너무 넉넉하면 clip
# 되기 전에 Qt가 그 전체를 word-wrap 레이아웃해야 해서(그리는 텍스트가 몇 줄이
# 되든 drawText는 전체를 한 번 감싸본다) 오히려 느려진다. 폰트 계산 없이 문자수로만 자른다.
# 정확한 줄바꿈 위치를 QFontMetrics.boundingRect로 계산하면(옛날 방식) 결과 하나당
# 수 ms가 걸려서, 스크롤로 새 행이 계속 화면에 들어올 때마다 그 계산이 쌓여 버벅였다.
# 대신 넉넉한 글자수로만 빠르게 자르고, 그리기 영역을 clip해서 넘치는 부분은
# 화면에 안 보이게만 하면 충분하다 — 시각적으로는 거의 차이가 없다.
CHARS_PER_LINE_AT_12PX = 60  # 기준 폰트 크기(12px)에서의 대략적인 줄당 글자수


def _char_cap(lines: int, font_px: int) -> int:
    per_line = max(20, round(CHARS_PER_LINE_AT_12PX * 12 / max(font_px, 6)))
    return per_line * max(1, lines)


def _cap_text(text: str, char_cap: int) -> str:
    if len(text) <= char_cap:
        return text
    return text[:char_cap].rstrip() + "…"


def _px_font(px: int, weight: QFont.Weight = QFont.Normal) -> QFont:
    f = QFont()
    f.setPixelSize(px)
    f.setWeight(weight)
    return f


class ResultDelegate(QStyledItemDelegate):
    """결과 목록의 각 행을 실제 QWidget 없이 직접 그린다(Everything 같은 파일 검색기가
    수만 건도 가볍게 보여주는 방식과 동일 — 화면에 실제로 보이는 행만 paint()가 호출된다).
    행마다 QWidget/QLayout을 만들던 이전 방식은 결과가 몇백 개만 넘어가도 위젯 생성
    비용 때문에 검색창이 멈춘 것처럼 느려졌다."""

    FOLDER_H_PAD = 14
    MEMBER_H_PAD = 20
    CATEGORY_H_PAD = 26
    NOTICE_H_PAD = 20
    RESULT_H_PAD = 14
    RESULT_V_PAD = 6

    def __init__(self, parent=None, filename_font_px: int = 10, content_font_px: int = 12,
                 snippet_max_lines: int = 2):
        super().__init__(parent)
        self._highlight_terms = []
        self.set_config(filename_font_px, content_font_px, snippet_max_lines)

    def set_highlight_terms(self, terms):
        """지금 검색 중인 단어 목록. 스니펫 안에서 이 단어들과 일치하는 부분을
        강조해서 그린다(대소문자 무관)."""
        self._highlight_terms = [t for t in terms if t]

    def set_config(self, filename_font_px: int = 10, content_font_px: int = 12,
                    snippet_max_lines: int = 2):
        """옵션에서 텍스트 크기(px)/표시 줄 수가 바뀔 때마다(혹은 최초 생성 시) 폰트와
        높이 계산에 쓰는 값들을 다시 만든다. 델리게이트 자체를 새로 만들지 않고
        이 값들만 갱신하면 되므로, 검색창을 새로 열지 않아도 옵션 저장 즉시 반영된다.
        파일명 크기는 파일명/위치/폴더·카테고리 등 제목류 텍스트에, 내용 크기는
        검색된 스니펫 본문에만 적용한다(둘을 따로 조절하고 싶다는 요청). 파일명은
        항상 한 줄만 보여준다(여러 줄로 감싸면 목록이 세로로 너무 길어짐)."""
        self.filename_font_px = filename_font_px
        self.content_font_px = content_font_px
        self.snippet_max_lines = max(1, snippet_max_lines)

        # 폴더/카테고리/알림 같은 제목류 텍스트는 원래 파일명(10px)보다 조금씩
        # 크거나 같게 설계돼 있었다(11/10/10/11px) — 그 상대적인 크기 차이를
        # 그대로 유지한 채, 파일명 크기가 바뀐 만큼(delta)만 같이 밀어준다.
        delta = filename_font_px - 10

        def title_px(base: int) -> int:
            return max(8, base + delta)

        self.name_font = _px_font(max(8, filename_font_px), QFont.Medium)
        self.loc_font = _px_font(max(8, filename_font_px))
        self.snippet_font = _px_font(max(8, content_font_px))
        self.folder_font = _px_font(title_px(11), QFont.Bold)
        self.member_font = _px_font(title_px(10), QFont.DemiBold)
        self.category_font = _px_font(title_px(10), QFont.DemiBold)
        self.notice_font = _px_font(title_px(11))
        # QFontMetrics 생성 자체가 공짜가 아니라서(폰트 DB 조회), 매 paint()마다
        # 새로 만들지 않고 한 번만 만들어 재사용한다 — 스크롤 중 반복 호출되므로 중요하다.
        self.name_fm = QFontMetrics(self.name_font)
        self.loc_fm = QFontMetrics(self.loc_font)
        self.snippet_fm = QFontMetrics(self.snippet_font)

        self._name_block_h = self.name_fm.height()
        self._snippet_block_h = self.snippet_fm.lineSpacing() * self.snippet_max_lines
        self._snippet_char_cap = _char_cap(self.snippet_max_lines, max(8, content_font_px))

    # ---------- 높이 계산 (population 시점에 setSizeHint 하는 데 씀, 텍스트 레이아웃 없이 즉시 계산) ----------
    def folder_height(self) -> int:
        return QFontMetrics(self.folder_font).height() + 12  # 위10 + 아래2

    def member_height(self) -> int:
        return QFontMetrics(self.member_font).height() + 8  # 위6 + 아래2

    def category_height(self) -> int:
        return QFontMetrics(self.category_font).height() + 6  # 위4 + 아래2

    def notice_height(self) -> int:
        return QFontMetrics(self.notice_font).height() + 16  # 위8 + 아래8

    def measure_result(self, result: dict) -> int:
        has_snippet = bool(result.get("snippet"))
        return (self.RESULT_V_PAD + self._name_block_h
                + (2 + self._snippet_block_h if has_snippet else 0)
                + self.RESULT_V_PAD)

    # ---------- 그리기 ----------
    def paint(self, painter: QPainter, option, index):
        payload = index.data(Qt.UserRole) or {}
        kind = payload.get("kind")
        rect = option.rect
        hovered = bool(option.state & QStyle.State_MouseOver)
        selected = bool(option.state & QStyle.State_Selected)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)

        if kind == "result":
            if hovered or selected:
                painter.setBrush(RESULT_BG_HOVER)
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(rect.adjusted(4, 1, -4, -1), 12, 12)
            self._paint_result(painter, rect, payload)
        elif kind == "folder":
            self._paint_header(painter, rect, payload, self.folder_font,
                                FOLDER_COLOR_HOVER if hovered else FOLDER_COLOR, self.FOLDER_H_PAD)
        elif kind == "member":
            self._paint_header(painter, rect, payload, self.member_font, MEMBER_COLOR, self.MEMBER_H_PAD)
        elif kind == "category":
            self._paint_header(painter, rect, payload, self.category_font,
                                CATEGORY_COLOR_HOVER if hovered else CATEGORY_COLOR, self.CATEGORY_H_PAD)
        elif kind == "notice":
            painter.setFont(self.notice_font)
            painter.setPen(NOTICE_COLOR)
            painter.drawText(rect.adjusted(self.NOTICE_H_PAD, 0, -self.NOTICE_H_PAD, 0), Qt.AlignVCenter,
                              payload.get("text", ""))

        painter.restore()

    def _paint_header(self, painter, rect, payload, font, color, left_pad):
        painter.setFont(font)
        painter.setPen(color)
        text_rect = rect.adjusted(left_pad, 0, -14, 0)
        painter.drawText(text_rect, Qt.AlignVCenter, payload.get("text", ""))

    def _paint_result(self, painter, rect, payload):
        result = payload["result"]
        icon = payload["icon"]

        left = rect.left() + self.RESULT_H_PAD
        right = rect.right() - self.RESULT_H_PAD
        top = rect.top() + self.RESULT_V_PAD

        loc_text = result["location"]
        name_text = f"{icon} {result['name']}"

        loc_w = self.loc_fm.horizontalAdvance(loc_text)
        name_rect = QRect(left, top, max(10, (right - left) - loc_w - 8), self.name_fm.height())
        painter.setFont(self.name_font)
        painter.setPen(NAME_COLOR)
        painter.drawText(name_rect, Qt.AlignVCenter,
                          self.name_fm.elidedText(name_text, Qt.ElideRight, name_rect.width()))

        loc_rect = QRect(right - loc_w, top, loc_w, self.name_fm.height())
        painter.setFont(self.loc_font)
        painter.setPen(LOC_COLOR)
        painter.drawText(loc_rect, Qt.AlignVCenter | Qt.AlignRight, loc_text)
        next_top = top + self.name_fm.height() + 2

        if result.get("snippet"):
            snippet_rect = QRect(left, next_top, right - left, self._snippet_block_h)
            self._draw_snippet(painter, snippet_rect, _cap_text(result["snippet"], self._snippet_char_cap))

    def _draw_snippet(self, painter, rect, text):
        """스니펫을 그린다. 검색어와 일치하는 부분은 배경을 칠해서 강조한다.
        QTextLayout으로 줄바꿈은 Qt에 맡기되, 실제로 화면에 그리는 줄은
        snippet_max_lines로 제한해서(넘치는 줄은 만들지도 않음) 느려지지 않게 한다."""
        layout = QTextLayout(text, self.snippet_font)

        if self._highlight_terms:
            lower = text.lower()
            formats = []
            hl_format = QTextCharFormat()
            hl_format.setBackground(HIGHLIGHT_BG)
            hl_format.setForeground(HIGHLIGHT_COLOR)
            for term in self._highlight_terms:
                start = 0
                while True:
                    pos = lower.find(term, start)
                    if pos == -1:
                        break
                    fmt_range = QTextLayout.FormatRange()
                    fmt_range.start = pos
                    fmt_range.length = len(term)
                    fmt_range.format = hl_format
                    formats.append(fmt_range)
                    start = pos + len(term)
            layout.setFormats(formats)

        layout.beginLayout()
        y = 0.0
        for _ in range(self.snippet_max_lines):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(rect.width())
            line.setPosition(QPointF(0, y))
            y += self.snippet_fm.lineSpacing()
        layout.endLayout()

        painter.save()
        painter.setClipRect(rect)
        painter.setPen(SNIPPET_COLOR)
        layout.draw(painter, QPointF(rect.left(), rect.top()))
        painter.restore()


HEADER_KINDS = ("folder", "member", "category")


class ResultsListWidget(QListWidget):
    """헤더(폴더/그룹 멤버/카테고리) 행 클릭을 감지해 콜백으로 넘기고, 그 위에서는
    커서를 손가락 모양으로 바꿔 클릭 가능함을 알려준다."""

    def __init__(self, on_header_click, parent=None):
        super().__init__(parent)
        self._on_header_click = on_header_click
        self.setMouseTracking(True)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid():
                payload = idx.data(Qt.UserRole) or {}
                if payload.get("kind") in HEADER_KINDS:
                    self._on_header_click(payload)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        idx = self.indexAt(event.position().toPoint())
        kind = (idx.data(Qt.UserRole) or {}).get("kind") if idx.isValid() else None
        self.viewport().setCursor(Qt.PointingHandCursor if kind in HEADER_KINDS else Qt.ArrowCursor)
        super().mouseMoveEvent(event)


class DraggableRow(QFrame):
    """검색창 이동용 핸들. line_edit/settings_btn 등 자식 위젯 위에서는
    클릭이 먼저 그 위젯으로 가므로, 이 프레임 자신의 빈 배경을 눌렀을 때만
    드래그가 시작된다."""

    def __init__(self, on_drag_start, on_drag_move, on_drag_end, parent=None):
        super().__init__(parent)
        self._on_drag_start = on_drag_start
        self._on_drag_move = on_drag_move
        self._on_drag_end = on_drag_end
        self._drag_offset = None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window().pos()
            self._on_drag_start()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and (event.buttons() & Qt.LeftButton):
            new_pos = event.globalPosition().toPoint() - self._drag_offset
            self.window().move(new_pos)
            self._on_drag_move()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None:
            self._drag_offset = None
            self._on_drag_end()
        super().mouseReleaseEvent(event)


class ResizeHandle(QWidget):
    """창 좌우 가장자리의 투명한 얇은 스트립. 마우스를 올리면 좌우 크기조절 커서로
    바뀌고, 드래그하면 검색창 너비를 실시간으로 바꾼다(옵션 슬라이더 없이도 창
    가장자리를 직접 잡아끌어 너비를 조절할 수 있게)."""

    def __init__(self, edge: str, on_drag_start, on_drag, on_drag_end, parent=None):
        super().__init__(parent)
        self._edge = edge  # "left" 또는 "right"
        self._on_drag_start = on_drag_start
        self._on_drag = on_drag
        self._on_drag_end = on_drag_end
        self._dragging = False
        self.setCursor(Qt.SizeHorCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._on_drag_start(self._edge)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._dragging and (event.buttons() & Qt.LeftButton):
            self._on_drag(self._edge, event.globalPosition().toPoint().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging:
            self._dragging = False
            self._on_drag_end()
        super().mouseReleaseEvent(event)


class SearchLineEdit(QLineEdit):
    """한글 등 조합형 입력기를 쓸 때, Qt의 textChanged는 조합(preedit) 중에는 발생하지
    않고 조합이 끝나야(스페이스/새 음절 시작 등) 발생한다. 그래서 기본 QLineEdit로는
    한 음절 입력할 때마다 검색이 갱신되지 않고 한 박자씩 늦게 느껴진다. 조합 중 글자가
    바뀔 때도 알 수 있도록 inputMethodEvent를 직접 잡는다."""

    preedit_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._preedit = ""

    def inputMethodEvent(self, event):
        super().inputMethodEvent(event)
        self._preedit = event.preeditString()
        self.preedit_changed.emit()

    def current_text(self) -> str:
        """조합 중인 글자까지 포함한, 화면에 보이는 그대로의 현재 텍스트."""
        if not self._preedit:
            return self.text()
        pos = self.cursorPosition()
        return self.text()[:pos] + self._preedit + self.text()[pos:]


class SearchWindow(QWidget):
    open_settings_requested = Signal()

    def __init__(self, indexer, settings):
        super().__init__()
        self.indexer = indexer
        self.settings = settings
        self._bottom_y = None
        self._results = []
        self._row_result_index = []
        self._last_query = None
        self._search_worker = None
        self._indexing = False
        self._index_status_text = ""
        self._truncated = False
        self._folder_row_shown = False
        self._folder_chip_specs = []
        self._folder_pages = [[]]
        self._folder_chip_page = 0
        self._collapsed_categories = set()  # {(folder_label, member_label, category_label), ...}
        self._collapsed_folders = set()  # {folder_label, ...}
        self._collapsed_members = set()  # {(folder_label, member_label), ...}

        self._build_ui()
        self._rebuild_folder_chips()
        self._apply_window_flags()
        self.setWindowOpacity(self.settings.opacity)

        self._resize_anim = QPropertyAnimation(self, b"geometry", self)
        self._resize_anim.setDuration(120)
        self._resize_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(280)
        self._debounce.timeout.connect(self._run_search)

        self.line_edit.textChanged.connect(lambda _: self._debounce.start())
        self.line_edit.preedit_changed.connect(self._debounce.start)
        self.line_edit.installEventFilter(self)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self.hide_window)

    # ---------- UI 구성 ----------
    def _build_ui(self):
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("card")
        outer.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self.results_list = ResultsListWidget(self._on_header_click, self.card)
        self.results_list.setObjectName("resultsList")
        self.results_list.setFrameShape(QFrame.NoFrame)
        self.results_list.setVisible(False)
        self.results_list.itemActivated.connect(self._open_selected)
        self._delegate = ResultDelegate(
            self.results_list,
            filename_font_px=self.settings.filename_font_px,
            content_font_px=self.settings.content_font_px,
            snippet_max_lines=self.settings.snippet_max_lines,
        )
        self.results_list.setItemDelegate(self._delegate)
        # 필요할 때만(내용이 넘칠 때) 세로 스크롤바를 보여준다 — 스크롤 가능하다는
        # 걸 알 수 있게. 가로 스크롤은 필요 없으니 계속 꺼둔다.
        self.results_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.results_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        card_layout.addWidget(self.results_list, 1)

        self.status_label = QLabel(self.card)
        self.status_label.setObjectName("statusLabel")
        self.status_label.setVisible(False)
        card_layout.addWidget(self.status_label)

        # 재색인이 끝났을 때, 지금 보고 있는 검색 결과를 조용히 덮어써버리지 않고
        # (스크롤 위치·선택 항목이 날아가면 당황스럽다) 대신 눌러야 갱신되는 배너로
        # 안내한다 — 검색어가 있을 때만 뜬다.
        self.stale_banner = QPushButton("색인이 갱신됐어요 · 눌러서 다시 검색", self.card)
        self.stale_banner.setObjectName("staleBanner")
        self.stale_banner.setCursor(Qt.PointingHandCursor)
        self.stale_banner.setVisible(False)
        self.stale_banner.clicked.connect(self._refresh_stale_search)
        card_layout.addWidget(self.stale_banner)

        input_row = DraggableRow(self._on_drag_start, self._on_dragged, self._on_drag_end, self.card)
        input_row.setFixedHeight(INPUT_HEIGHT)
        row_layout = QHBoxLayout(input_row)
        row_layout.setContentsMargins(20, 0, 14, 0)
        row_layout.setSpacing(10)

        self.line_edit = SearchLineEdit(input_row)
        self.line_edit.setObjectName("lineEdit")
        self.line_edit.setPlaceholderText("검색")
        self.line_edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row_layout.addWidget(self.line_edit, 1)

        # 검색이 백그라운드 스레드에서 도는 동안(1~2글자 검색어는 인덱스를 못 타서
        # 최대 1초 가까이 걸릴 수 있다) 창이 멈춘 게 아니라 그냥 처리 중이라는 걸
        # 알려주는 옅은 텍스트 — 결과가 오면 바로 숨는다.
        self.loading_label = QLabel("검색 중…", input_row)
        self.loading_label.setObjectName("loadingLabel")
        self.loading_label.setVisible(False)
        row_layout.addWidget(self.loading_label)

        self.settings_btn = QToolButton(input_row)
        self.settings_btn.setObjectName("settingsBtn")
        self.settings_btn.setText("⚙")
        self.settings_btn.clicked.connect(self.open_settings_requested.emit)
        row_layout.addWidget(self.settings_btn)

        card_layout.addWidget(input_row)

        self.folder_row = QFrame(self.card)
        self.folder_row.setFixedHeight(FOLDER_ROW_HEIGHT)
        self.folder_row.setVisible(False)
        self.folder_row_layout = QHBoxLayout(self.folder_row)
        self.folder_row_layout.setContentsMargins(16, 2, 16, 8)
        self.folder_row_layout.setSpacing(6)
        card_layout.addWidget(self.folder_row)

        # 좌우 가장자리 리사이즈 핸들. card의 자식이 아니라 self(최상위 창)의
        # 자식으로 만들고 raise_()해서, card 안의 어떤 자식 위젯 위에 겹치더라도
        # 가장자리 6px 폭 안에서는 항상 이 핸들이 클릭을 먼저 받는다.
        self._resize_start = None  # (global_x, start_width, start_x) — 드래그 시작 시점 기록
        self.resize_handle_left = ResizeHandle("left", self._on_resize_start, self._on_resize_drag, self._on_resize_end, self)
        self.resize_handle_right = ResizeHandle("right", self._on_resize_start, self._on_resize_drag, self._on_resize_end, self)

    def _base_height(self) -> int:
        # self.folder_row.isVisible()는 창이 숨겨진 동안(hide 직후 등) 항상 False로
        # 나오므로 쓸 수 없다 — 명시적으로 관리하는 플래그를 사용한다.
        height = INPUT_HEIGHT + (FOLDER_ROW_HEIGHT if self._folder_row_shown else 0)
        if self.stale_banner.isVisible():
            height += self.stale_banner.sizeHint().height()
        return height

    def resizeEvent(self, event):
        super().resizeEvent(event)
        h = self.height()
        self.resize_handle_left.setGeometry(0, 0, RESIZE_HANDLE_WIDTH, h)
        self.resize_handle_right.setGeometry(self.width() - RESIZE_HANDLE_WIDTH, 0, RESIZE_HANDLE_WIDTH, h)
        self.resize_handle_left.raise_()
        self.resize_handle_right.raise_()

    # ---------- 좌우 가장자리 드래그로 너비 직접 조절 ----------
    def _min_max_width(self) -> tuple:
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        screen_w = screen.availableGeometry().width()
        return int(screen_w * MIN_WIDTH_PERCENT), int(screen_w * MAX_WIDTH_PERCENT)

    def _on_resize_start(self, edge: str):
        self._resize_anim.stop()
        self._resize_start = (QCursor.pos().x(), self.width(), self.x())

    def _on_resize_drag(self, edge: str, global_x: int):
        if self._resize_start is None:
            return
        start_x_cursor, start_width, start_win_x = self._resize_start
        delta = global_x - start_x_cursor
        min_w, max_w = self._min_max_width()
        if edge == "right":
            new_width = max(min_w, min(max_w, start_width + delta))
            self.setFixedWidth(new_width)
        else:
            new_width = max(min_w, min(max_w, start_width - delta))
            new_x = start_win_x + (start_width - new_width)
            # setFixedWidth가 최소/최대폭을 새 값으로 다시 고정해야, 곧이어 부르는
            # move()가 이전 폭에 맞춰 clamp되지 않는다(QWidget은 지오메트리 변경 시
            # 고정된 min/max 크기를 벗어나지 못하게 막는다).
            self.setFixedWidth(new_width)
            self.move(new_x, self.y())

    def _on_resize_end(self):
        if self._resize_start is None:
            return
        self._resize_start = None
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        screen_w = screen.availableGeometry().width()
        self.settings.width_percent = round(self.width() / screen_w, 4)
        self.settings.save()
        # 칩이 새 너비에 맞게 다시 줄바꿈/페이지 나뉨 되도록 갱신.
        self._compute_folder_pages()
        self._folder_chip_page = 0
        self._render_folder_chip_page()
        self._resize_to_fit()

    def _rebuild_folder_chips(self):
        """옵션에서 폴더 목록이 바뀔 때마다(혹은 창을 다시 열 때) 하단 토글 칩을
        다시 만든다. 칩이 창 너비를 넘치면 페이지로 나누고, 오른쪽 끝에 화살표를
        붙여 다음 페이지로 순환할 수 있게 한다(전부 다 보여주려다 잘리는 대신)."""
        folders = self.settings.folders
        # 표시 이름은 최상위 폴더/그룹 키뿐 아니라 그룹 안에 숨은 멤버 폴더에도
        # 붙는다(settings.folders에는 그룹 키만 있고 멤버 경로는 folder_groups
        # 안에만 있음). 이걸 빼먹으면 방금 옵션에서 입력한 멤버 표시 이름을
        # "더 이상 존재하지 않는 폴더"로 오인해 저장 직후 바로 지워버린다.
        valid_display_keys = set(folders)
        for members in self.settings.folder_groups.values():
            valid_display_keys.update(members)
        changed = False
        for f in folders:
            if f not in self.settings.folder_enabled:
                self.settings.folder_enabled[f] = True
                changed = True
        for f in list(self.settings.folder_enabled.keys()):
            if f not in folders:
                del self.settings.folder_enabled[f]
                changed = True
        for f in list(self.settings.folder_filename_only.keys()):
            if f not in folders:
                del self.settings.folder_filename_only[f]
                changed = True
        for f in list(self.settings.folder_display_name.keys()):
            if f not in valid_display_keys:
                del self.settings.folder_display_name[f]
                changed = True
        if changed:
            self.settings.save()

        self._folder_row_shown = bool(folders)
        self.folder_row.setVisible(self._folder_row_shown)
        self._compute_folder_pages()
        self._folder_chip_page = 0
        self._render_folder_chip_page()

    def _chip_label(self, folder: str) -> str:
        name = self._folder_display_name(folder)
        mode_hint = " 🔤" if self.settings.folder_filename_only.get(folder, False) else ""
        return name + mode_hint

    def _chip_width(self, label: str) -> int:
        # 버튼을 실제로 만들어 sizeHint()를 읽으면(아직 화면에 붙기 전이라) QSS의
        # font-size가 반영 안 된 값이 나올 수 있어(전에 겪었던 문제), 직접 폰트를
        # 지정해 폭을 추정한다 — 페이지를 나누는 데는 이 정도 정확도면 충분하다.
        font = QFont()
        font.setPixelSize(11)
        return QFontMetrics(font).horizontalAdvance(label) + 24  # 좌우 padding(10+10)+여유

    def _compute_folder_pages(self):
        folders = self.settings.folders
        self._folder_chip_specs = [(f, self._chip_label(f)) for f in folders]
        if not self._folder_chip_specs:
            self._folder_pages = [[]]
            return

        spacing = 6
        margins = 16 + 16
        available = max(60, self.width() - margins)
        widths = [self._chip_width(label) for _, label in self._folder_chip_specs]

        total = sum(widths) + spacing * (len(widths) - 1)
        if total <= available:
            self._folder_pages = [list(range(len(widths)))]
            return

        arrow_reserve = FOLDER_ARROW_WIDTH + spacing
        avail_per_page = max(60, available - arrow_reserve)
        pages = []
        current, current_w = [], 0
        for i, w in enumerate(widths):
            add = w + (spacing if current else 0)
            if current and current_w + add > avail_per_page:
                pages.append(current)
                current, current_w = [], 0
                add = w
            current.append(i)
            current_w += add
        if current:
            pages.append(current)
        self._folder_pages = pages

    def _render_folder_chip_page(self):
        while self.folder_row_layout.count():
            item = self.folder_row_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not self._folder_chip_specs:
            return

        pages = self._folder_pages
        self._folder_chip_page = max(0, min(self._folder_chip_page, len(pages) - 1))
        for i in pages[self._folder_chip_page]:
            f, label = self._folder_chip_specs[i]
            btn = QPushButton(label, self.folder_row)
            btn.setObjectName("folderChip")
            btn.setCheckable(True)
            btn.setChecked(self.settings.folder_enabled.get(f, True))
            btn.setCursor(Qt.PointingHandCursor)
            mode_desc = "파일명만 검색" if self.settings.folder_filename_only.get(f, False) else "내용까지 검색"
            members = self.settings.folder_groups.get(f)
            location_desc = "\n".join(members) if members else f
            btn.setToolTip(f"{location_desc}\n({mode_desc})")
            btn.toggled.connect(lambda checked, folder=f: self._on_chip_toggled(folder, checked))
            self.folder_row_layout.addWidget(btn)
        self.folder_row_layout.addStretch(1)

        if len(pages) > 1:
            arrow = QPushButton("▶", self.folder_row)
            arrow.setObjectName("folderPageArrow")
            arrow.setCursor(Qt.PointingHandCursor)
            arrow.setToolTip(f"다음 폴더 목록 ({self._folder_chip_page + 1}/{len(pages)})")
            arrow.clicked.connect(self._next_folder_page)
            self.folder_row_layout.addWidget(arrow)

        if self.isVisible():
            self._resize_to_fit()

    def _next_folder_page(self):
        if len(self._folder_pages) <= 1:
            return
        self._folder_chip_page = (self._folder_chip_page + 1) % len(self._folder_pages)
        self._render_folder_chip_page()

    def _on_chip_toggled(self, folder: str, checked: bool):
        self.settings.folder_enabled[folder] = checked
        self.settings.save()
        query = self.line_edit.current_text().strip()
        if query:
            self._execute_search(query, force=True)

    def _folder_display_name(self, folder: str) -> str:
        custom = self.settings.folder_display_name.get(folder)
        if custom:
            return custom
        return os.path.basename(folder.rstrip("\\/")) or folder

    def _owning_folder(self, path: str):
        """path 가 속한 설정된 대상 폴더(또는 그 폴더가 속한 그룹) 경로/키를 찾는다.
        그룹으로 묶인 폴더 안의 결과는 그룹 자체로 매핑되어, 결과 목록에도 그룹
        이름으로 묶여 보인다(그 안에서 실제 어느 폴더인지는 _member_folder_for로 또 구분)."""
        p = os.path.normcase(os.path.normpath(path))
        for f in self.settings.folders:
            members = self.settings.folder_groups.get(f)
            candidates = members if members else [f]
            for c in candidates:
                cn = os.path.normcase(os.path.normpath(c))
                if p == cn or p.startswith(cn + os.sep):
                    return f
        return None

    def _member_folder_for(self, group_key: str, path: str):
        """그룹으로 묶인 결과가 실제로는 그룹 안의 어느 폴더에서 왔는지 찾는다."""
        p = os.path.normcase(os.path.normpath(path))
        for m in self.settings.folder_groups.get(group_key, []):
            mn = os.path.normcase(os.path.normpath(m))
            if p == mn or p.startswith(mn + os.sep):
                return m
        return None

    def _group_results(self, results):
        """결과를 대상 폴더(표시 이름 ㄱㄴㄷ순) → (그룹이면) 실제 출처 폴더 →
        확장자 분류(폴더, PDF, 엑셀, 기타 순) → 파일명 ㄱㄴㄷ순으로 정리한 표시
        계획을 만든다. 그룹으로 묶인 폴더는 다 같이 뭉뚱그려지면 어디서 온 결과인지
        알 수 없으니, 그룹 안에서는 실제 폴더별로 한 번 더 나눈다. 접힌 폴더/
        카테고리는 그 안의 결과 행을 뺀다.
        반환값: [("folder", 라벨, 개수, 접힘여부), ("member", 라벨, 접힘키, 개수, 접힘여부),
                ("category", 라벨, 접힘키, 개수, 접힘여부), ("result", result, 원본인덱스), ...]"""
        groups = {}  # folder_key(or None) -> {member_label(or None): {category: [(result, idx)]}}
        for idx, r in enumerate(results):
            folder = self._owning_folder(r["path"])
            member_label = None
            if folder and folder in self.settings.folder_groups:
                member = self._member_folder_for(folder, r["path"])
                if member:
                    # 그 폴더에 표시 이름이 지정돼 있으면 그걸로, 없으면 폴더명으로.
                    member_label = self._folder_display_name(member)
            cat = _categorize_result(r)
            groups.setdefault(folder, {}).setdefault(member_label, {}).setdefault(cat, []).append((r, idx))

        def label_of(folder):
            return self._folder_display_name(folder) if folder else "기타"

        ordered_folders = sorted(groups.keys(), key=lambda f: label_of(f))

        plan = []
        for folder in ordered_folders:
            folder_label = label_of(folder)
            member_map = groups[folder]
            total = sum(len(items) for cats in member_map.values() for items in cats.values())
            folder_collapsed = folder_label in self._collapsed_folders
            plan.append(("folder", folder_label, total, folder_collapsed))
            if folder_collapsed:
                continue

            member_keys = sorted(member_map.keys(), key=lambda m: (m is None, m or ""))
            for member_label in member_keys:
                cats = member_map[member_label]
                member_total = sum(len(items) for items in cats.values())
                member_key = (folder_label, member_label)
                member_collapsed = member_label is not None and member_key in self._collapsed_members
                if member_label is not None:
                    plan.append(("member", member_label, member_key, member_total, member_collapsed))
                    if member_collapsed:
                        continue
                present = [c for c in CATEGORY_ORDER if c in cats]
                present += [c for c in cats if c not in present]
                for cat in present:
                    items = sorted(cats[cat], key=lambda t: t[0]["name"])
                    key = (folder_label, member_label, cat)
                    collapsed = key in self._collapsed_categories
                    plan.append(("category", cat, key, len(items), collapsed))
                    if not collapsed:
                        for r, idx in items:
                            plan.append(("result", r, idx))
        return plan

    def _folder_modes(self):
        """활성화된 폴더별 (경로, 파일명만검색여부) 목록. 그룹은 실제 멤버 폴더들로
        풀어서 넣는다(색인은 실제 경로 기준이라 그룹 키 자체는 검색 대상이 아님).
        폴더가 없으면 None(제한 없음)."""
        if not self.settings.folders:
            return None
        modes = []
        for f in self.settings.folders:
            if not self.settings.folder_enabled.get(f, True):
                continue
            filename_only = self.settings.folder_filename_only.get(f, False)
            members = self.settings.folder_groups.get(f)
            if members:
                modes.extend((m, filename_only) for m in members)
            else:
                modes.append((f, filename_only))
        return modes

    def _apply_window_flags(self):
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self.settings.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)

    def apply_settings(self):
        """옵션 변경 후 호출: 항상위/투명도/폴더 목록/텍스트 크기/줄 수를 즉시 반영."""
        was_visible = self.isVisible()
        self._apply_window_flags()
        self.setWindowOpacity(self.settings.opacity)
        self._delegate.set_config(
            filename_font_px=self.settings.filename_font_px,
            content_font_px=self.settings.content_font_px,
            snippet_max_lines=self.settings.snippet_max_lines,
        )
        self._rebuild_folder_chips()
        if self._results:
            self._render_results()
        if was_visible:
            self.show()
            self.raise_()
            self.activateWindow()

    # ---------- 표시/숨김 ----------
    def toggle(self):
        if self.isVisible():
            self.hide_window()
        else:
            self.show_window()

    def show_window(self):
        base = self._base_height()
        has_saved_pos = self.settings.pos_x is not None and self.settings.pos_bottom_y is not None

        if has_saved_pos:
            # 마우스 커서 위치가 아니라, 마지막으로 저장된 위치가 속한 모니터를 기준으로 삼는다
            # (멀티 모니터에서 단축키를 다른 화면 위에서 눌러도 엉뚱한 화면으로 보정되지 않도록)
            saved_point = QPoint(self.settings.pos_x, self.settings.pos_bottom_y)
            screen = QGuiApplication.screenAt(saved_point)
        else:
            screen = None
        screen = screen or QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()

        width = int(geo.width() * self.settings.width_percent)
        width = max(int(geo.width() * MIN_WIDTH_PERCENT), min(int(geo.width() * MAX_WIDTH_PERCENT), width))

        if has_saved_pos:
            x = self.settings.pos_x
            bottom_y = self.settings.pos_bottom_y
            # 저장 당시와 화면 구성이 달라졌을 수 있으니 현재 화면 안으로 보정
            x = max(geo.x(), min(x, geo.x() + geo.width() - width))
            bottom_y = max(geo.y() + base, min(bottom_y, geo.y() + geo.height()))
        else:
            x = geo.x() + (geo.width() - width) // 2
            bottom_y = geo.y() + int(geo.height() * 0.58)

        self._bottom_y = bottom_y
        self._max_total_height = int(geo.height() * self.settings.max_height_percent)

        self.setFixedWidth(width)
        # 폴더 칩 페이지는 실제 창 너비를 기준으로 나누는데, 맨 처음(__init__
        # 시점)에는 창 크기가 아직 정해지기 전이라 정확하지 않을 수 있다 —
        # 창을 열 때마다 지금 너비 기준으로 다시 계산한다.
        self._compute_folder_pages()
        self._folder_chip_page = 0
        self._render_folder_chip_page()
        self.setGeometry(x, self._bottom_y - base, width, base)

        self.show()
        self.raise_()
        win_focus.force_foreground(int(self.winId()))
        self.activateWindow()
        self.line_edit.setFocus(Qt.ActiveWindowFocusReason)
        self.line_edit.selectAll()
        # 이전에 검색했던 내용을 지우지 않고 그대로 유지한다 — 결과가 남아있으면
        # (results_list를 안 비웠으므로) 다시 열 때 그 크기로 곧바로 펼쳐진다.
        self._resize_to_fit()

    def hide_window(self):
        self.hide()

    def _on_drag_start(self):
        self._resize_anim.stop()

    def _on_dragged(self):
        """드래그로 이동한 뒤에는, 그 위치의 하단을 새 기준점으로 삼아
        이후 검색 결과가 그 자리에서 위로 펼쳐지도록 한다."""
        self._bottom_y = self.y() + self.height()

    def _on_drag_end(self):
        """다음 실행에도 같은 자리에서 열리도록 위치를 저장한다."""
        self.settings.pos_x = self.x()
        self.settings.pos_bottom_y = self._bottom_y
        self.settings.save()

    def eventFilter(self, obj, event):
        if obj is self.line_edit and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Down, Qt.Key_Up):
                self._move_selection(1 if key == Qt.Key_Down else -1)
                return True
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self._open_selected()
                return True
        return super().eventFilter(obj, event)

    def event(self, e):
        if e.type() == QEvent.WindowDeactivate:
            self.hide_window()
        return super().event(e)

    # ---------- 검색 ----------
    def _run_search(self):
        self._execute_search(self.line_edit.current_text().strip())

    def _execute_search(self, query: str, force: bool = False):
        if not query:
            self._last_query = None
            self.stale_banner.setVisible(False)
            self._refresh_status_label()
            self._clear_results()
            return
        if len(query) < MIN_QUERY_LENGTH:
            # 한 글자짜리 검색(특히 흔한 영문 한 글자)은 결과가 수천 건씩 쏟아져서
            # 그 많은 결과 위젯을 다 그리느라 검색창이 멈춘 것처럼 느려질 수 있다.
            self._last_query = None
            self.stale_banner.setVisible(False)
            self._refresh_status_label()
            self._results = []
            self._row_result_index = []
            self.results_list.clear()
            self.results_list.setVisible(False)
            self.status_label.setText(f"검색어를 {MIN_QUERY_LENGTH}자 이상 입력해주세요")
            self.status_label.setVisible(True)
            self._resize_to_fit()
            return
        if not force and query == self._last_query:
            return  # 같은 검색어로 중복 검색/재배치 방지 (깜빡임 방지)
        self._last_query = query
        self.stale_banner.setVisible(False)
        self._delegate.set_highlight_terms(query.lower().split())

        # 검색은 백그라운드 스레드에서 돈다 — 1~2글자 검색어는 trigram 인덱스를 못
        # 타서 SQLite LIKE 로 전체를 훑는데 1초 가까이 걸릴 수 있는데, 메인 스레드에서
        # 그대로 부르면 그동안 검색창이 완전히 멈춘 것처럼 보인다. 이전 요청이 아직
        # 안 끝났어도 그냥 새로 하나 더 띄운다 — 결과가 오면 query 를 대조해서 이미
        # 지나간(더 최신 검색어로 덮인) 결과는 버리므로 순서 꼬임 걱정은 없다.
        self._search_worker = SearchWorker(self.indexer, query, SEARCH_DISPLAY_LIMIT, self._folder_modes())
        self._search_worker.finished_ok.connect(self._on_search_finished)
        self._refresh_status_label()
        self._search_worker.start()

    def _on_search_finished(self, query: str, results: list):
        if query != self._last_query:
            return  # 그사이 검색어가 바뀌어서 이제 필요 없어진 결과
        self._refresh_status_label()
        self._results = results
        self._truncated = len(self._results) >= SEARCH_DISPLAY_LIMIT
        self._render_results()

    def _refresh_status_label(self):
        """검색 중/색인 중 상태에 따라 loading_label 을 갱신한다. 검색이 더 급한
        피드백이라 우선순위를 높게 둔다 — 색인 중이어도 검색은 별도 스레드라
        동시에 돌 수 있다."""
        if self._search_worker is not None and self._search_worker.isRunning():
            self.loading_label.setText("검색 중…")
            self.loading_label.setVisible(True)
        elif self._indexing:
            self.loading_label.setText(self._index_status_text)
            self.loading_label.setVisible(True)
        else:
            self.loading_label.setVisible(False)

    # ---------- 색인 진행 상황 (main.App 이 IndexWorker 시그널을 여기로 연결) ----------
    def on_index_started(self):
        self._indexing = True
        self._index_status_text = "색인 중…"
        self._refresh_status_label()

    def on_index_progress(self, text: str):
        self._index_status_text = text
        self._refresh_status_label()

    def on_index_finished(self, _count: int):
        self._indexing = False
        self._refresh_status_label()
        # 지금 보고 있는 검색 결과가 방금 끝난 재색인으로 바뀌었을 수 있다 — 화면을
        # 조용히 덮어써버리면(스크롤 위치·선택 항목이 날아감) 당황스러우니, 직접
        # 눌러야 갱신되는 배너로만 알려준다.
        if self._last_query:
            self.stale_banner.setVisible(True)
            self._resize_to_fit()

    def _refresh_stale_search(self):
        self.stale_banner.setVisible(False)
        if self._last_query:
            self._execute_search(self._last_query, force=True)
        self._resize_to_fit()

    def _render_results(self):
        """self._results 를 다시 그린다 (카테고리 접기/펼치기처럼 검색을 새로 하지
        않고 화면만 다시 그려야 할 때도 쓰인다)."""
        self.results_list.clear()
        self._row_result_index = []

        if not self._results:
            self.results_list.setVisible(False)
            self.status_label.setText(f"'{self._last_query}' 에 대한 검색 결과가 없습니다  ·  색인된 파일 {self.indexer.file_count()}개")
            self.status_label.setVisible(True)
        else:
            self.status_label.setVisible(False)
            self.results_list.setVisible(True)
            first_result_row = None

            width = self.width()

            if self._truncated:
                notice = QListWidgetItem()
                notice.setFlags(Qt.ItemIsEnabled)
                notice.setData(Qt.UserRole, {
                    "kind": "notice",
                    "text": f"결과가 많아 상위 {SEARCH_DISPLAY_LIMIT}개만 표시 — 검색어를 더 구체적으로 입력해보세요",
                })
                notice.setSizeHint(QSize(width, self._delegate.notice_height()))
                self.results_list.addItem(notice)
                self._row_result_index.append(None)

            for entry in self._group_results(self._results):
                item = QListWidgetItem()
                if entry[0] == "folder":
                    _, label, count, collapsed = entry
                    arrow = "▸" if collapsed else "▾"
                    item.setFlags(Qt.ItemIsEnabled)
                    item.setData(Qt.UserRole, {
                        "kind": "folder", "label": label,
                        "text": f"{arrow} {label} ({count})",
                    })
                    item.setSizeHint(QSize(width, self._delegate.folder_height()))
                    self._row_result_index.append(None)
                elif entry[0] == "member":
                    _, label, key, count, collapsed = entry
                    arrow = "▸" if collapsed else "▾"
                    item.setFlags(Qt.ItemIsEnabled)
                    item.setData(Qt.UserRole, {
                        "kind": "member", "key": key,
                        "text": f"{arrow} {label} ({count})",
                    })
                    item.setSizeHint(QSize(width, self._delegate.member_height()))
                    self._row_result_index.append(None)
                elif entry[0] == "category":
                    _, label, key, count, collapsed = entry
                    arrow = "▸" if collapsed else "▾"
                    item.setFlags(Qt.ItemIsEnabled)
                    item.setData(Qt.UserRole, {
                        "kind": "category", "key": key,
                        "text": f"{arrow} {label} ({count})",
                    })
                    item.setSizeHint(QSize(width, self._delegate.category_height()))
                    self._row_result_index.append(None)
                else:
                    _, r, idx = entry
                    if r.get("is_dir"):
                        icon = "📁"
                    elif os.path.splitext(r["name"])[1].lower() in (".xlsx", ".xlsm"):
                        icon = "📊"
                    else:
                        icon = "📄"
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                    item.setData(Qt.UserRole, {"kind": "result", "result": r, "icon": icon})
                    item.setSizeHint(QSize(width, self._delegate.measure_result(r)))
                    if first_result_row is None:
                        first_result_row = self.results_list.count()
                    self._row_result_index.append(idx)
                self.results_list.addItem(item)
            if first_result_row is not None:
                self.results_list.setCurrentRow(first_result_row)
                # setCurrentRow는 선택된 행이 보이도록 자동 스크롤하는데, 그 행이 맨 위가
                # 아니면(폴더/카테고리 헤더가 위에 있으면) 헤더가 위로 밀려 잘려 보인다.
                # 같은 호출 안에서 scrollToTop을 불러도 Qt가 아직 새 항목들의 스크롤
                # 범위를 갱신하기 전이라 씹히므로, 다음 이벤트 루프 틱으로 미룬다.
                QTimer.singleShot(0, self.results_list.scrollToTop)

        self._resize_to_fit()

    def _on_header_click(self, payload):
        if payload["kind"] == "folder":
            self._toggle_folder(payload["label"])
        elif payload["kind"] == "member":
            self._toggle_member(payload["key"])
        else:
            self._toggle_category(payload["key"])

    def _toggle_category(self, key):
        if key in self._collapsed_categories:
            self._collapsed_categories.discard(key)
        else:
            self._collapsed_categories.add(key)
        self._render_results()

    def _toggle_folder(self, folder_label):
        if folder_label in self._collapsed_folders:
            self._collapsed_folders.discard(folder_label)
        else:
            self._collapsed_folders.add(folder_label)
        self._render_results()

    def _toggle_member(self, member_key):
        if member_key in self._collapsed_members:
            self._collapsed_members.discard(member_key)
        else:
            self._collapsed_members.add(member_key)
        self._render_results()

    def _clear_results(self):
        self._results = []
        self._row_result_index = []
        self._truncated = False
        self.results_list.clear()
        self.results_list.setVisible(False)
        self.status_label.setVisible(False)
        self._resize_to_fit()

    def _resize_to_fit(self):
        if self._bottom_y is None:
            return
        base = self._base_height()
        available = max(0, self._max_total_height - base)

        extra = 0
        if self.results_list.isVisible():
            count = self.results_list.count()
            content_height = sum(self.results_list.item(i).sizeHint().height() for i in range(count)) + 12
            # 다 들어가면 딱 맞게, 넘치면 남은 공간을 최대한 써서 안에서 스크롤
            extra = content_height if content_height <= available else available
        elif self.status_label.isVisible():
            extra = self.status_label.sizeHint().height() + 4

        total_height = base + extra
        total_height = min(total_height, self._max_total_height)

        width = self.width()
        x = self.x()
        new_y = self._bottom_y - total_height
        target = QRect(x, new_y, width, total_height)

        if self.geometry() == target:
            return
        self._resize_anim.stop()
        self._resize_anim.setStartValue(self.geometry())
        self._resize_anim.setEndValue(target)
        self._resize_anim.start()

    def _move_selection(self, delta: int):
        n = len(self._row_result_index)
        if n == 0:
            return
        row = self.results_list.currentRow()
        r = row
        while True:
            r += delta
            if r < 0 or r >= n:
                return  # 더 이상 갈 곳 없음 (헤더를 넘어 랩어라운드하지 않음)
            if self._row_result_index[r] is not None:
                self.results_list.setCurrentRow(r)
                return

    def _open_selected(self, *_):
        row = self.results_list.currentRow()
        if row < 0 or row >= len(self._row_result_index):
            return
        idx = self._row_result_index[row]
        if idx is None or idx >= len(self._results):
            return
        open_result(self._results[idx])
        self.hide_window()
