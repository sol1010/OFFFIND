"""프레임리스 다크 검색창.
평소에는 둥근 알약 모양 입력창만 보이고, 검색어를 입력하면 그 위쪽으로
결과 목록이 펼쳐진다 (입력창의 하단 위치는 고정, 위로만 자란다).
"""
import math
import os

from PySide6.QtCore import Qt, QTimer, Signal, QEvent, QPoint, QPointF, QRect, QRectF, QSize
from PySide6.QtGui import (
    QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QImage, QKeySequence,
    QPainter, QPen, QPixmap, QShortcut, QTextCharFormat, QTextLayout,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel, QToolButton,
    QListWidget, QListWidgetItem, QFrame, QSizePolicy, QPushButton,
    QStyle, QStyledItemDelegate, QMenu, QApplication, QScrollArea,
)

import win_focus
# "이 경로가 저 폴더 안인가" 판단은 드라이브 루트("C:\\") 예외 때문에 직접 짜면
# 틀리기 쉽다(구분자가 두 번 겹쳐 아무것도 안 걸린다) — indexer 쪽 구현을 그대로
# 쓴다. 같은 판단이 두 군데서 서로 다르게 돌면 결과가 엉뚱한 폴더로 분류된다.
from indexer import _is_under as _path_is_under
from file_opener import open_result, open_containing_folder
from search_worker import SearchWorker

# 입력창과 결과 팝업은 서로 다른 최상위 창이라 OS가 각자 따로 그리고 앤티에일리어싱한다
# — 좌표상 정확히 맞닿게 배치해도 이음매에 미세한 픽셀 어긋남이 보일 수 있어서(실측
# 확인), 팝업을 이만큼 입력창 쪽으로 살짝 겹치게 그린다(같은 배경색이라 겹쳐도 티 안 남).
SEAM_OVERLAP = 1
MIN_QUERY_LENGTH = 2  # 한두 글자로는(특히 영문 한 글자) 결과가 너무 많아져 검색창이 멈춘 것처럼 느려짐
# 결과 표시 개수 상한은 설정(settings.search_display_limit)에서 조절한다 —
# _group_results()로 정렬/그룹핑하고 QListWidgetItem을 이 개수만큼 미리 다 만드는
# 건 메인 스레드에서 동기로 돈다. 실측 결과 수천~1만 개도 체감상 문제없이 빠르게
# 끝나서(SQL 쪽이 FTS5 인덱스로 이미 빨라진 덕에 병목이 아니게 됨) 기본값을
# 10000으로 넉넉하게 잡았다 — 그래도 원하면 설정 창에서 더 줄이거나 늘릴 수 있다.
MIN_WIDTH_PERCENT = 0.20  # 검색창 너비를 직접 드래그로 줄일 수 있는 최소치(화면 너비 대비)
MAX_WIDTH_PERCENT = 0.90
RESIZE_HANDLE_WIDTH = 6

_ICON_COLOR = "#c7cad1"
_icon_cache = {}


def _line_icon(key: str, size: int, draw) -> QIcon:
    """이모지 대신 흰/회색 선(스트로크)만으로 직접 그리는 작은 아이콘. draw(painter, size)가
    실제 모양을 그린다 — 폰트 이모지는 OS/폰트마다 색이 다르고(모래시계 이모지는 노란색
    등) 앱 전체 톤(어두운 배경 + 회색 계열)과 안 어울려서, 앱에서 쓰는 아이콘은 전부 이
    방식으로 통일한다. 같은 key는 한 번만 그리고 재사용한다."""
    cached = _icon_cache.get(key)
    if cached is not None:
        return cached
    # QPixmap(size, size)를 바로 만들면 알파 채널이 보장되지 않아 fill(Qt.transparent)가
    # 실제로 투명하게 안 먹을 수 있다(플랫폼에 따라 배경이 그대로 남거나 아예 안 그려짐) —
    # ARGB32 포맷의 QImage에 그린 다음 QPixmap으로 변환해야 투명 배경이 확실히 보장된다.
    image = QImage(size, size, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(_ICON_COLOR))
    pen.setWidthF(1.3)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)
    draw(painter, size)
    painter.end()
    icon = QIcon(QPixmap.fromImage(image))
    _icon_cache[key] = icon
    return icon


_SPINNER_DOTS = 4
_SPINNER_STEPS = _SPINNER_DOTS  # 점이 4개뿐이라 90도씩만 돌아도 눈에 띈다
_SPINNER_GLYPH = 10.0  # 실제로 그려지는 점 링의 기준 크기(캔버스 크기와 별개)
_SPINNER_SHIFT_X = 1.5  # 칩 안에서 살짝 오른쪽으로 밀어 보기 좋게 맞춘 값


def _spinner_icon(frame: int, size: int = 13) -> QIcon:
    """색인 중인 폴더 칩에 붙는 회전 로딩 아이콘. 프레임(0~_SPINNER_STEPS-1)마다
    통째로 다시 그려서 반환한다 — 이미 그려둔 걸 매번 조금씩 회전시키는 방식은
    전에(옛날 스피닝 기어 아이콘) 조금씩 어긋나며 회전축이 드리프트하는 문제가
    있었다. 매 프레임을 고정 각도에서 처음부터 새로 그리면 그럴 일이 없다.

    점 8개에 알파값만 완만하게 그러데이션 주는 첫 버전은 12px 크기에서는 명암
    차이가 너무 옅어서 실제로는 거의 안 움직이는 것처럼 보였다(실측 — 200x200로
    확대해서 직접 비교함). 점을 4개로 줄이고 밝기 차이를 극단적으로(불투명 →
    거의 투명) 둬서, 어느 프레임에서든 "가장 밝은 점"이 뚜렷하게 하나만 보이고
    그게 도는 게 확실히 보이게 한다."""
    def draw(painter, s):
        painter.setPen(Qt.NoPen)
        # 점 링 크기를 캔버스 크기(s)가 아니라 _SPINNER_GLYPH 기준으로 잡는다 —
        # 캔버스에 여유를 남겨두는 셈이라, 중심을 오른쪽으로 밀어도 바깥 점이
        # 캔버스를 넘지 않는다. 예전엔 링이 캔버스를 꽉 채운 상태에서 중심만
        # 밀어서 오른쪽·아래가 잘렸다(실제로 겪음).
        radius = _SPINNER_GLYPH * 0.33
        dot_r = _SPINNER_GLYPH * 0.135
        center = QPointF(s / 2 + _SPINNER_SHIFT_X, s / 2)
        alphas = [255, 150, 70, 20]
        for i in range(_SPINNER_DOTS):
            angle = math.radians((frame + i) * (360 / _SPINNER_DOTS))
            cx = center.x() + radius * math.cos(angle)
            cy = center.y() + radius * math.sin(angle)
            color = QColor(_ICON_COLOR)
            color.setAlpha(alphas[i])
            painter.setBrush(color)
            painter.drawEllipse(QPointF(cx, cy), dot_r, dot_r)
    return _line_icon(f"spinner{frame}_{size}", size, draw)


def _gear_icon(size: int = 16) -> QIcon:
    def draw(painter, s):
        center = QPointF(s / 2, s / 2)
        radius = s * 0.28
        painter.drawEllipse(center, radius, radius)
        for i in range(8):
            painter.save()
            painter.translate(center)
            painter.rotate(360 / 8 * i)
            painter.drawLine(QPointF(0, -radius - 1), QPointF(0, -s * 0.48))
            painter.restore()
    return _line_icon(f"gear{size}", size, draw)


_CARD_RADIUS = 26
# 카드 배경 자체는 완전 불투명하게 둔다 — 예전엔 여기 알파(235 ≈ 92%)를 박아둬서
# 설정에서 투명도를 100%로 올려도 창이 끝까지 불투명해지지 않았다(실제로 겪음).
# 반투명은 오로지 setWindowOpacity(설정의 투명도 슬라이더)만 담당한다.
_CARD_BG = "rgb(32, 33, 36)"


def _set_card_corners(frame, top_left: int, top_right: int, bottom_left: int, bottom_right: int, bg: str = _CARD_BG):
    """입력창과 결과 팝업이 별도의 두 창이 됐지만, 결과가 떠 있을 때는 예전처럼
    하나로 이어진 모양처럼 보이게 한다 — 맞닿는 안쪽 모서리는 각지게(반지름 0),
    바깥쪽 모서리만 둥글게. 전역 STYLE의 "#card { border-radius: 26px }" 규칙은
    그대로 두고(독립적으로 뜰 때의 기본 알약 모양), 이 함수는 그 프레임 하나에만
    더 구체적인(위젯 자체에 직접 건) 스타일시트를 얹어서 필요할 때만 모서리를
    다르게 덮어쓴다."""
    # QFrame 같은 위젯은 WA_StyledBackground가 없으면 인스턴스에 직접 건
    # 스타일시트의 background/border가 아예 안 그려지고 기본 배경만 그려질 수
    # 있다(전역 스타일시트에서 objectName으로 물려받을 때는 겪지 않던 문제 —
    # 실제로 grab()으로 직접 캡처해서 확인함: 모서리 반지름이 통째로 무시되고
    # 사각형으로 그려지고 있었다).
    frame.setAttribute(Qt.WA_StyledBackground, True)
    # border: none 로 두면 Qt가 배경을 모서리에 맞춰 잘라내지 않고 사각형으로
    # 채워버리는 경우가 있다(테두리 선 자체를 없애면서 실제로 겪음) — 눈에는 안
    # 보이는 투명 테두리를 대신 둬서, "테두리 선은 없지만 모서리는 둥글게" 둘 다
    # 되게 한다.
    # 선택자를 "QFrame"(타입)으로 두면 이 프레임의 자식 중 QFrame 계열인
    # input_row/folder_row에도 그대로 매치돼서 걔네들의 더 구체적인
    # #inputRow/#folderRow 규칙을 덮어써버린다(실측으로 확인함 — 전에 겪은
    # "직접 건 스타일시트가 하위 objectName 규칙을 가린다" 문제와 같은 종류,
    # 이번엔 card 쪽에서 재발). "#card"로 한정해서 이 프레임 자신에게만 매치되게 한다.
    frame.setStyleSheet(f"""
        #card {{
            background-color: {bg};
            border: 1px solid transparent;
            border-top-left-radius: {top_left}px;
            border-top-right-radius: {top_right}px;
            border-bottom-left-radius: {bottom_left}px;
            border-bottom-right-radius: {bottom_right}px;
        }}
    """)
    # setStyleSheet()만으로는 이미 그려진 위젯에 새 모서리 반지름이 안 먹고 예전
    # 모양(사각형)이 남아있는 경우가 있었다 — unpolish/polish로 스타일을 강제로
    # 다시 적용시키고 다시 그리게 한다.
    frame.style().unpolish(frame)
    frame.style().polish(frame)
    frame.update()


STYLE = """
#searchWindow {
    background: transparent;
}
#resultsPopup {
    background: transparent;
}
#card {
    background-color: rgb(32, 33, 36);
    border: 1px solid transparent;
    border-radius: 26px;
}
#inputRow {
    background: transparent;
    border: none;
}
#folderRow {
    background: transparent;
    border: none;
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
    /* 색인 중일 때 폴더명 뒤에 로딩 아이콘이 붙으므로, 그게 칩 가장자리에
       닿지 않도록 오른쪽 여백을 조금 더 준다. */
    padding: 4px 12px 4px 11px;
    font-size: 11px;
}
/* 체크 가능한 버튼이라 기본적으로 체크박스 표시(indicator)가 딸려 오는데,
   켜짐/꺼짐은 이미 :checked 배경색으로 보여주고 있어서 그 표시는 필요 없다 —
   숨기지 않으면 글자 앞에 체크 아이콘이 겹쳐 보인다. */
#folderChip::indicator {
    width: 0px;
    height: 0px;
}
#folderChip:hover {
    background-color: rgba(255, 255, 255, 26);
}
#folderChip:checked {
    background-color: rgba(138, 180, 248, 50);
    color: #e8eaed;
}
#folderScroll {
    background: transparent;
    border: none;
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
        # 안내 문구는 검색 결과 자체가 아니라 보조 설명이라, 제목류 중에서도
        # 가장 작게 둔다(결과 목록을 훑는 데 방해가 안 되게).
        self.notice_font = _px_font(title_px(9))
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

    def notice_link_font(self) -> QFont:
        font = QFont(self.notice_font)
        font.setUnderline(True)
        return font

    def notice_layout(self, payload: dict, avail_width: int):
        """안내 행의 (실제로 그릴 본문, 링크 시작 x오프셋, 링크 너비).

        그리기와 클릭 판정이 각자 계산하면 한쪽만 바뀌었을 때 링크 위치가 어긋난다
        — 두 곳 모두 이 함수를 쓴다. 창이 좁아 다 못 담으면 링크는 온전히 남기고
        본문을 "…"로 줄인다(링크가 잘리면 누를 수가 없다)."""
        text = payload.get("text", "")
        link = payload.get("link") or ""
        fm = QFontMetrics(self.notice_font)
        link_w = QFontMetrics(self.notice_link_font()).horizontalAdvance(link) if link else 0
        if link and fm.horizontalAdvance(text) + link_w > avail_width:
            text = fm.elidedText(text, Qt.ElideRight, max(0, avail_width - link_w))
        return text, fm.horizontalAdvance(text), link_w

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
            if hovered or selected:
                painter.setBrush(RESULT_BG_HOVER)
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(rect.adjusted(4, 1, -4, -1), 10, 10)
            self._paint_header(painter, rect, payload, self.folder_font,
                                FOLDER_COLOR_HOVER if (hovered or selected) else FOLDER_COLOR,
                                self.FOLDER_H_PAD)
        elif kind == "member":
            self._paint_header(painter, rect, payload, self.member_font, MEMBER_COLOR, self.MEMBER_H_PAD)
        elif kind == "category":
            if hovered or selected:
                painter.setBrush(RESULT_BG_HOVER)
                painter.setPen(Qt.NoPen)
                painter.drawRoundedRect(rect.adjusted(4, 1, -4, -1), 10, 10)
            self._paint_header(painter, rect, payload, self.category_font,
                                CATEGORY_COLOR_HOVER if (hovered or selected) else CATEGORY_COLOR,
                                self.CATEGORY_H_PAD)
        elif kind == "notice":
            text_rect = rect.adjusted(self.NOTICE_H_PAD, 0, -self.NOTICE_H_PAD, 0)
            body, link_dx, link_w = self.notice_layout(payload, text_rect.width())
            painter.setFont(self.notice_font)
            painter.setPen(NOTICE_COLOR)
            painter.drawText(text_rect, Qt.AlignVCenter, body)
            # 안내 뒤에 "표시 개수 바꾸기" 를 링크처럼 이어 붙인다 — 클릭 판정은
            # ResultsListWidget 이 같은 notice_layout() 으로 위치를 구한다.
            if link_w:
                painter.setFont(self.notice_link_font())
                painter.setPen(LOC_COLOR)
                painter.drawText(QRect(text_rect.left() + link_dx, text_rect.top(),
                                        link_w, text_rect.height()),
                                  Qt.AlignVCenter, payload.get("link", ""))

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
                kind = payload.get("kind")
                if kind in ("category", "folder"):
                    # 카테고리/폴더 헤더는 이제 선택도 가능하다 — 이름 글자 위를
                    # 클릭했을 때만 접기/펼치기, 그 옆 빈 영역은 그냥 선택만 되게
                    # 둔다(아래 super() 호출이 일반 선택 처리를 해준다).
                    if self._point_on_header_text(idx, event.position().toPoint()):
                        self._on_header_click(payload)
                        event.accept()
                        return
                elif kind in HEADER_KINDS:
                    self._on_header_click(payload)
                    event.accept()
                    return
                elif kind == "notice" and payload.get("link"):
                    # 안내 문구 뒤의 링크 글자 위를 눌렀을 때만 반응한다.
                    if self._point_on_notice_link(idx, event.position().toPoint()):
                        self._on_header_click(payload)
                        event.accept()
                        return
        super().mousePressEvent(event)

    def _notice_link_range(self, idx):
        """안내 행에서 링크 글자가 차지하는 x 범위(left, right).
        위치 계산은 델리게이트의 notice_layout() 하나만 쓴다(그리기와 클릭 판정이
        따로 계산하면 한쪽만 바뀌었을 때 어긋난다)."""
        delegate = self.itemDelegate()
        payload = idx.data(Qt.UserRole) or {}
        rect = self.visualRect(idx)
        avail = rect.width() - delegate.NOTICE_H_PAD * 2
        _body, link_dx, link_w = delegate.notice_layout(payload, avail)
        left = rect.left() + delegate.NOTICE_H_PAD + link_dx
        return left, left + link_w

    def _point_on_notice_link(self, idx, pos) -> bool:
        left, right = self._notice_link_range(idx)
        return left <= pos.x() <= right

    def _point_on_header_text(self, idx, pos) -> bool:
        delegate = self.itemDelegate()
        payload = idx.data(Qt.UserRole) or {}
        text = payload.get("text", "")
        rect = self.visualRect(idx)
        if payload.get("kind") == "folder":
            font, pad = delegate.folder_font, delegate.FOLDER_H_PAD
        else:
            font, pad = delegate.category_font, delegate.CATEGORY_H_PAD
        text_width = QFontMetrics(font).horizontalAdvance(text)
        text_left = rect.left() + pad
        return text_left <= pos.x() <= text_left + text_width

    def mouseMoveEvent(self, event):
        pos = event.position().toPoint()
        idx = self.indexAt(pos)
        payload = (idx.data(Qt.UserRole) or {}) if idx.isValid() else {}
        kind = payload.get("kind")
        if kind == "notice" and payload.get("link"):
            hand = self._point_on_notice_link(idx, pos)
        else:
            hand = kind in HEADER_KINDS
        self.viewport().setCursor(Qt.PointingHandCursor if hand else Qt.ArrowCursor)
        super().mouseMoveEvent(event)


class ChipScrollArea(QScrollArea):
    """폴더 칩 한 줄짜리 가로 스크롤 스트립. 세로 휠을 가로 스크롤로 돌린다 —
    QScrollArea 기본 동작은 세로 휠을 세로 스크롤바로만 보내는데, 이 스트립엔
    세로 스크롤이 없어서 휠이 그냥 죽는다(칩 위에서 휠을 굴려도 아무 일도 안
    일어남). 한 줄뿐이니 세로 휠 = 가로 이동으로 해석하는 게 자연스럽다."""

    def wheelEvent(self, event):
        delta = event.angleDelta().y() or event.angleDelta().x()
        bar = self.horizontalScrollBar()
        bar.setValue(bar.value() - delta)
        event.accept()


class FolderChipButton(QPushButton):
    """하단 폴더 칩 하나. 클릭은 켜기/끄기 토글, 좌우로 끌면 순서 변경.

    끌기 시작 전까지는 일반 버튼과 똑같다. 누른 지점에서 startDragDistance
    이상 움직이면 그때부터 순서 변경 모드 — 칩을 레이아웃에서 잠깐 빼서 커서를
    따라 실제로 움직여 보여주고(좌우로만 — 세로는 줄이 하나뿐이라 의미가 없다),
    원래 자리에는 같은 크기의 투명 자리표시자를 넣어 "여기에 놓인다"는 빈 틈이
    같이 움직이게 한다. 레이아웃에 넣은 채 move()해봐야 다음 레이아웃 패스에서
    슬롯 위치로 강제 복귀해서 끌리는 모습이 전혀 안 보인다(그래서 빼는 것).
    놓는 순간 자리표시자 위치로 들어가며 새 순서를 저장한다(on_reordered 콜백).
    순서 변경으로 끝난 드래그는 클릭으로 치지 않는다 — super().mouseReleaseEvent를
    안 불러서 놓는 순간 토글이 같이 일어나는 걸 막는다."""

    def __init__(self, folder_key: str, host_layout, scroll_area, on_reordered, parent=None):
        super().__init__(parent)
        self.folder_key = folder_key
        self._host_layout = host_layout
        self._scroll_area = scroll_area
        self._on_reordered = on_reordered
        self._press_pos = None       # 전역 좌표 — 드래그 시작 판정용
        self._press_local_x = 0      # 칩 안에서 누른 x — 끌 때 커서가 칩의 그 지점을 계속 잡고 있게
        self._dragging = False
        self._placeholder = None
        self._drag_y = 0

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press_pos = event.globalPosition().toPoint()
            self._press_local_x = event.position().toPoint().x()
            self._dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press_pos is not None and (event.buttons() & Qt.LeftButton):
            gp = event.globalPosition().toPoint()
            if (not self._dragging
                    and (gp - self._press_pos).manhattanLength() >= QApplication.startDragDistance()):
                self._begin_drag()
            if self._dragging:
                self._drag_to(gp)
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._dragging and event.button() == Qt.LeftButton:
            self._end_drag()
            event.accept()
            return
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def _chips(self):
        chips = []
        for i in range(self._host_layout.count()):
            w = self._host_layout.itemAt(i).widget()
            if isinstance(w, FolderChipButton):
                chips.append(w)
        return chips

    def _begin_drag(self):
        self._dragging = True
        self._drag_y = self.y()  # 세로는 이 값에 고정 — 좌우로만 끌린다
        idx = self._chips().index(self)
        self._host_layout.removeWidget(self)
        self._placeholder = QWidget(self.parentWidget())
        self._placeholder.setFixedSize(self.size())
        self._host_layout.insertWidget(idx, self._placeholder)
        self.raise_()  # 떠 있는 동안 다른 칩들 위로 지나가 보이게

    def _drag_to(self, global_pos):
        host = self.parentWidget()
        x = host.mapFromGlobal(global_pos).x() - self._press_local_x
        x = max(0, min(x, host.width() - self.width()))
        self.move(x, self._drag_y)

        # 떠 있는 칩의 중심이 지나친 다른 칩 개수 = 자리표시자가 갈 인덱스.
        center = x + self.width() / 2
        others = self._chips()  # 나는 지금 레이아웃 밖이라 자동으로 빠져 있다
        target = sum(1 for w in others if center > w.x() + w.width() / 2)
        if self._host_layout.indexOf(self._placeholder) != target:
            self._host_layout.removeWidget(self._placeholder)
            self._host_layout.insertWidget(target, self._placeholder)

        # 뷰포트 가장자리 근처로 끌면 보이지 않는 칩 쪽으로 자동 스크롤.
        vp = self._scroll_area.viewport()
        vx = vp.mapFromGlobal(global_pos).x()
        bar = self._scroll_area.horizontalScrollBar()
        if vx < 24:
            bar.setValue(bar.value() - 12)
        elif vx > vp.width() - 24:
            bar.setValue(bar.value() + 12)

    def _end_drag(self):
        self._dragging = False
        self._press_pos = None
        idx = self._host_layout.indexOf(self._placeholder)
        self._host_layout.removeWidget(self._placeholder)
        self._placeholder.deleteLater()
        self._placeholder = None
        self._host_layout.insertWidget(idx, self)
        self.setDown(False)
        self._on_reordered()


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


class ResultsPopup(QWidget):
    """검색 결과 목록을 담는, 입력창과는 별개인 최상위 창. 예전엔 입력창과 결과
    목록이 하나의 창(하나의 geometry)이라, 검색어를 입력하거나 지울 때마다 결과
    영역 크기에 맞춰 창 전체의 위쪽 경계가 움직이면서 입력창까지 같이 흔들리는
    것처럼 보였다 — 입력창은 절대 스스로 움직이지 않게 하고, 결과가 나타나고
    사라지며 커졌다 줄어드는 건 이 별도 창만 담당하게 분리한다.

    Qt.WindowDoesNotAcceptFocus를 켜서, 결과를 클릭해도(항목 열기, 폴더/카테고리
    접기·펼치기) 이 창이 키보드 포커스나 "활성 창" 상태를 가져가지 않는다 — 안
    그러면 검색창(SearchWindow)이 포커스를 잃었다고 판단해(WindowDeactivate)
    결과를 클릭하는 순간 전체 UI가 닫혀버린다. 화살표 위/아래·Enter로 결과를
    넘나드는 것도 SearchWindow의 line_edit가 이벤트를 가로채 처리하므로, 이 목록
    자체가 키보드 포커스를 가질 필요가 애초에 없다."""

    def __init__(self, on_header_click):
        super().__init__(None)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("resultsPopup")  # DEBUG: card 바깥, 창 자신의 배경을 구분해서 보려고
        self.setStyleSheet(STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.card = QFrame(self)
        self.card.setObjectName("card")
        # 이 팝업은 항상 입력창 바로 위에 맞닿아서만 뜬다 — 아래쪽(입력창과 맞닿는
        # 쪽) 모서리는 각지게 둬서, 두 창이 하나로 이어진 모양처럼 보이게 한다.
        _set_card_corners(self.card, _CARD_RADIUS, _CARD_RADIUS, 0, 0)
        outer.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        self.results_list = ResultsListWidget(on_header_click, self.card)
        self.results_list.setObjectName("resultsList")
        self.results_list.setFrameShape(QFrame.NoFrame)
        self.results_list.setVisible(False)
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
        card_layout.addWidget(self.stale_banner)

    def apply_flags(self, always_on_top: bool):
        # NoDropShadowWindowHint가 없으면 Windows가 프레임리스 창 주위에 그림자용
        # 여백을 얼마간 남겨서, 입력창과 이 팝업을 좌표상 딱 맞닿게 배치해도(수치는
        # 0) 그 보이지 않는 여백 때문에 둘 사이가 살짝 벌어져 보인다.
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint
        if always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        was_visible = self.isVisible()
        self.setWindowFlags(flags)
        # setWindowFlags()가 네이티브 창을 다시 만들면서 투명 배경 속성이 새
        # 네이티브 창에 안 실릴 수 있다 — 매번 다시 걸어준다.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        if was_visible:
            self.show()


class SearchWindow(QWidget):
    # 인자는 "어느 설정으로 보낼지"(예: "display_limit"). 빈 문자열이면 그냥 연다.
    open_settings_requested = Signal(str)

    def __init__(self, indexer, settings):
        super().__init__()
        self.indexer = indexer
        self.settings = settings
        self._bottom_y = None
        self._results = []
        self._row_result_index = []
        self._last_query = None
        self._search_worker = None
        self._search_workers = []  # 아직 안 끝난 SearchWorker들 — 끝날 때까지 참조를 붙들어 GC로 인한 크래시를 막는다
        self._indexing = False
        self._indexing_paths = set()  # 색인 중인 등록 {(path_norm, 파일명만검색여부)} — 폴더 칩 로딩 표시용
        self._folder_progress = {}  # path_norm -> (찾은 개수, 기준 총량) — 로딩 툴팁 퍼센트용
        self._spinner_frame = 0
        self._spinner_buttons = {}  # folder -> QPushButton — 색인 중인 칩의 로딩 스피너 갱신용
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(90)
        self._spinner_timer.timeout.connect(self._advance_spinner)
        self._truncated = False
        self._folder_row_shown = False
        self._folder_chip_specs = []
        self._collapsed_categories = set()  # {(folder_label, member_label, category_label), ...}
        self._collapsed_folders = set()  # {folder_label, ...}
        self._collapsed_members = set()  # {(folder_label, member_label), ...}
        self._context_menu_open = False

        self._build_ui()

        # 결과 목록은 입력창과 별개인 창이다 — 입력창(self)은 절대 스스로
        # 움직이거나 크기가 바뀌지 않고(폴더 칩 줄 유무 정도만 드물게 바뀜),
        # 검색 중 결과가 나타나고 사라지며 커졌다 줄어드는 건 이 팝업만 담당한다.
        self.results_popup = ResultsPopup(self._on_header_click)
        self.results_list = self.results_popup.results_list
        self.status_label = self.results_popup.status_label
        self.stale_banner = self.results_popup.stale_banner
        self.results_list.itemActivated.connect(self._open_selected)
        self.results_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.results_list.customContextMenuRequested.connect(self._show_result_context_menu)
        self._delegate = ResultDelegate(
            self.results_list,
            filename_font_px=self.settings.filename_font_px,
            content_font_px=self.settings.content_font_px,
            snippet_max_lines=self.settings.snippet_max_lines,
        )
        self.results_list.setItemDelegate(self._delegate)
        self.stale_banner.clicked.connect(self._refresh_stale_search)
        self._set_input_card_connected(False)  # 시작할 때는 항상 독립된 알약 모양

        self._rebuild_folder_chips()
        self._apply_window_flags()
        self.setWindowOpacity(self.settings.opacity)
        self.results_popup.setWindowOpacity(self.settings.opacity)

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
        self.setObjectName("searchWindow")  # DEBUG: card 바깥, 창 자신의 배경을 구분해서 보려고
        self.setStyleSheet(STYLE)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.card = QFrame(self)
        self.card.setObjectName("card")
        outer.addWidget(self.card)

        card_layout = QVBoxLayout(self.card)
        card_layout.setContentsMargins(0, 0, 0, 0)
        card_layout.setSpacing(0)

        # 예전엔 이 줄 높이를 상수(INPUT_HEIGHT)로 고정해 뒀는데, 그 값이 안의 글자
        # 상자(line_edit)가 실제로 필요로 하는 높이보다 작아지면 글자 상자가 이
        # 줄(그리고 그 위의 둥근 카드 프레임) 밖으로 튀어나와서 둥근 모서리가 깨져
        # 보였다(실측 — 디버그용 파란/노란 배경으로 확인함). 고정 숫자 대신 위/아래
        # 여백만 정해 두고, 실제 줄 높이는 안의 내용(글자 상자 등)이 필요로 하는
        # 만큼 레이아웃이 알아서 정하게 한다 — 그러면 절대 넘칠 수가 없다.
        self.input_row = DraggableRow(self._on_drag_start, self._on_dragged, self._on_drag_end, self.card)
        self.input_row.setObjectName("inputRow")
        # objectName으로 전역 스타일시트에서 물려받는 배경이라도, QFrame은
        # WA_StyledBackground가 없으면 그 배경을 실제로는 안 그린다(card에서 이미
        # 겪은 것과 같은 함정 — 이번엔 "직접 건 스타일시트가 상속을 끊는" 쪽이 아니라
        # "물려받기만 하고 안 그리는" 반대쪽으로 다시 나타남. 실측으로 확인함).
        self.input_row.setAttribute(Qt.WA_StyledBackground, True)
        row_layout = QHBoxLayout(self.input_row)
        row_layout.setContentsMargins(20, 8, 14, 8)
        row_layout.setSpacing(10)
        input_row = self.input_row

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
        self.settings_btn.setIcon(_gear_icon())
        self.settings_btn.setIconSize(QSize(16, 16))
        # clicked 는 checked(bool) 를 넘겨주므로 emit 에 직접 연결하면 안 된다
        # (시그널 인자가 str 이라 타입이 안 맞는다) — 빈 문자열로 감싸서 보낸다.
        self.settings_btn.clicked.connect(lambda: self.open_settings_requested.emit(""))
        row_layout.addWidget(self.settings_btn)

        card_layout.addWidget(input_row)

        # 폴더 칩 줄 — 칩이 창 너비를 넘치면 페이지로 나누는 대신(예전 방식,
        # ▶ 버튼으로 순환) 한 줄 그대로 두고 가로 스크롤한다. 칩은 끌어서 순서를
        # 바꿀 수 있다(FolderChipButton).
        self.folder_row = QFrame(self.card)
        self.folder_row.setObjectName("folderRow")
        self.folder_row.setAttribute(Qt.WA_StyledBackground, True)
        self.folder_row.setVisible(False)
        folder_row_outer = QHBoxLayout(self.folder_row)
        folder_row_outer.setContentsMargins(16, 1, 16, 4)
        folder_row_outer.setSpacing(0)

        self.folder_scroll = ChipScrollArea(self.folder_row)
        self.folder_scroll.setObjectName("folderScroll")
        self.folder_scroll.setFrameShape(QFrame.NoFrame)
        self.folder_scroll.setWidgetResizable(True)
        self.folder_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # 스크롤바 없이 휠로만 스크롤한다 — 바를 보이면(예전 버전) 칩 줄이 그만큼
        # 두꺼워져서 검색창 전체가 예전과 달라 보인다는 피드백이 있었다.
        self.folder_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # QScrollArea 뷰포트는 기본적으로 자기 배경(회색)을 칠한다 — 카드의 어두운
        # 배경이 그대로 비치도록 꺼준다.
        self.folder_scroll.viewport().setAutoFillBackground(False)

        self.chips_host = QWidget()
        self.chips_host.setAutoFillBackground(False)
        self.folder_row_layout = QHBoxLayout(self.chips_host)
        self.folder_row_layout.setContentsMargins(0, 0, 0, 0)
        self.folder_row_layout.setSpacing(6)
        self.folder_scroll.setWidget(self.chips_host)
        folder_row_outer.addWidget(self.folder_scroll)
        card_layout.addWidget(self.folder_row)

        # 좌우 가장자리 리사이즈 핸들. card의 자식이 아니라 self(최상위 창)의
        # 자식으로 만들고 raise_()해서, card 안의 어떤 자식 위젯 위에 겹치더라도
        # 가장자리 6px 폭 안에서는 항상 이 핸들이 클릭을 먼저 받는다.
        self._resize_start = None  # (global_x, start_width, start_x) — 드래그 시작 시점 기록
        self.resize_handle_left = ResizeHandle("left", self._on_resize_start, self._on_resize_drag, self._on_resize_end, self)
        self.resize_handle_right = ResizeHandle("right", self._on_resize_start, self._on_resize_drag, self._on_resize_end, self)

    def _base_height(self) -> int:
        # self.folder_row.isVisible()는 창이 숨겨진 동안(hide 직후 등) 항상 False로
        # 나오므로 쓸 수 없다 — 명시적으로 관리하는 플래그를 사용한다. 입력창 자신의
        # 높이는 이제 이 값 하나뿐이다(결과/상태/배너는 전부 별도 팝업 창 몫) — 폴더
        # 칩 줄이 있고 없고에 따라서만 드물게 바뀌고, 검색 중에는 절대 안 바뀐다.
        #
        # 고정 상수가 아니라 실제 내용이 필요로 하는 높이(sizeHint)를 그때그때
        # 물어본다 — 고정 숫자를 썼을 때 안의 글자 상자가 그보다 더 필요로 하면
        # 카드 프레임 밖으로 튀어나와 둥근 모서리가 깨지는 문제가 있었다.
        height = self.input_row.sizeHint().height()
        if self._folder_row_shown:
            height += self.folder_row.sizeHint().height()
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
        # 결과 팝업의 너비/가로 위치도 입력창을 따라 실시간으로 맞춘다(높이는 그대로).
        self._sync_popup_geometry()

    def _on_resize_end(self):
        if self._resize_start is None:
            return
        self._resize_start = None
        screen = QGuiApplication.screenAt(self.pos()) or QGuiApplication.primaryScreen()
        screen_w = screen.availableGeometry().width()
        self.settings.width_percent = round(self.width() / screen_w, 4)
        self.settings.save()
        # 칩 줄은 가로 스크롤 스트립이라 새 너비에 맞춰 알아서 따라온다 —
        # 예전 페이지 방식처럼 다시 나눠 그릴 필요가 없다.
        self._resize_to_fit()

    def _rebuild_folder_chips(self):
        """옵션에서 폴더 목록이 바뀔 때마다(혹은 창을 다시 열 때) 하단 토글 칩을
        다시 만든다. 칩이 창 너비를 넘치면 가로 스크롤로 본다(휠 또는 스크롤바 —
        예전의 페이지 나누기 + ▶ 순환 방식은 폐기)."""
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
        self._folder_chip_specs = [(f, self._chip_label(f)) for f in folders]
        self._render_folder_chips()

    def _chip_label(self, folder: str) -> str:
        return self._folder_display_name(folder)

    def _chip_height(self) -> int:
        # 버튼을 실제로 만들어 sizeHint()를 읽으면(아직 화면에 붙기 전이라) QSS의
        # font-size가 반영 안 된 값이 나올 수 있어(전에 겪었던 문제), 직접 폰트를
        # 지정해 높이를 추정한다 — QSS 상하 padding(4+4)에 여유 1px.
        font = QFont()
        font.setPixelSize(11)
        return QFontMetrics(font).height() + 9

    def _render_folder_chips(self, resize: bool = True):
        while self.folder_row_layout.count():
            item = self.folder_row_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # 버튼이 매번 새로 만들어지므로(위 while에서 이전 버튼은 지운다),
        # 로딩 스피너를 돌리는 타이머가 참조할 버튼 목록도 그때그때 다시 채운다.
        self._spinner_buttons = {}

        if not self._folder_chip_specs:
            return

        chip_h = self._chip_height()
        # 스트립 높이 = 칩 높이로 고정한다(스크롤바는 안 보이니 여유 공간이 필요
        # 없다) — 레이아웃 sizeHint에 맡기면 재렌더 직후 잠깐 엉뚱한 값이 잡혀
        # 창이 들썩이는 문제가 있었다(예전 _base_height 관련 주석 참고).
        self.folder_scroll.setFixedHeight(chip_h)

        for f, label in self._folder_chip_specs:
            btn = FolderChipButton(f, self.folder_row_layout, self.folder_scroll,
                                   self._on_chips_reordered, self.chips_host)
            btn.setText(label)
            btn.setObjectName("folderChip")
            btn.setFixedHeight(chip_h)
            # 체크 가능한 버튼이 네이티브(Windows) 스타일의 체크박스 표시를 글자
            # 앞에 그려 넣는데, 전역 스타일시트의 "::indicator" 규칙만으로는 이
            # 네이티브 표시가 안 사라지는 경우가 있었다(실측으로 확인함) — 버튼
            # 자신에게 직접 걸어야 확실히 먹는다. 켜짐/꺼짐은 :checked 배경색으로
            # 이미 보여주고 있어서 이 표시 자체가 필요 없다.
            btn.setStyleSheet("QPushButton::indicator { width: 0px; height: 0px; }")
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setChecked(self.settings.folder_enabled.get(f, True))
            btn.setCursor(Qt.PointingHandCursor)
            if self._folder_is_indexing(f):
                # 기본은 아이콘이 글자 왼쪽에 붙는데, 폴더명 뒤(오른쪽)에 오게
                # 하려면 이 버튼만 레이아웃 방향을 뒤집는다 — 텍스트 자체가
                # 좌우 방향 문자는 아니라서 글자 순서는 안 바뀌고 아이콘 위치만
                # 뒤로 간다.
                btn.setLayoutDirection(Qt.RightToLeft)
                btn.setIcon(_spinner_icon(self._spinner_frame))
                btn.setIconSize(QSize(13, 13))
                self._spinner_buttons[f] = btn
                pct = self._folder_progress_percent(f)
                btn.setToolTip(f"색인 중… {pct}%" if pct is not None else "색인 중…")
            else:
                btn.setToolTip("")
            btn.toggled.connect(lambda checked, folder=f: self._on_chip_toggled(folder, checked))
            self.folder_row_layout.addWidget(btn)
        self.folder_row_layout.addStretch(1)

        # 색인 진행 상황이 연달아 들어오면(set_indexing_paths) 이 함수가 짧은
        # 시간 안에 여러 번 다시 불리는데, 그때 sizeHint()를 다시 재면 방금 새로
        # 채운 위젯들이 레이아웃에 "아직 안 보이는" 걸로 잠깐 잡혀서 실제보다
        # 훨씬 작은 값(예: 31 대신 10)이 나올 때가 있었다(activate()를 걸어도
        # 안 잡힘 — 실측으로 확인함). 그러면 _base_height()가 줄어들어서
        # 검색창이 그만큼 아래로 내려가 보인다. 색인 아이콘만 바뀌는 경우엔
        # 칩 구성이 그대로라 줄 높이도 원래 안 바뀌어야 정상이니, 그 경로
        # (resize=False)에서는 아예 다시 배치를 안 한다.
        if resize and self.isVisible():
            self._resize_to_fit()

    def _on_chips_reordered(self):
        """칩을 끌어놓아 순서가 바뀌었을 때 — 레이아웃의 현재 순서를 설정에
        저장한다. 결과 묶음 순서 = 칩 순서이므로, 결과가 떠 있으면 그것도 새
        순서로 다시 그린다."""
        order = []
        for i in range(self.folder_row_layout.count()):
            w = self.folder_row_layout.itemAt(i).widget()
            if isinstance(w, FolderChipButton):
                order.append(w.folder_key)
        if order == self.settings.folders:
            return
        self.settings.folders = order
        self.settings.save()
        self._folder_chip_specs = [(f, self._chip_label(f)) for f in order]
        if self._results:
            self._render_results()

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
        # 멤버 1개짜리 그룹은 진짜 그룹이 아니라 "같은 폴더를 다른 검색 방식으로
        # 한 번 더 추가한" 항목이다(설정 화면과 같은 내부 표현) — 그 키 자체는
        # "GROUP::<uuid>" 같은 내부 식별자라서 그대로 보여주면 안 되고, 실제
        # 폴더 이름으로 보여줘야 한다(실제로 칩에 그 내부 키가 그대로 뜨는 걸
        # 확인함).
        members = self.settings.folder_groups.get(folder)
        if members and len(members) == 1:
            return os.path.basename(members[0].rstrip("\\/")) or members[0]
        return os.path.basename(folder.rstrip("\\/")) or folder

    def _owning_folders(self, path: str, filename_only: bool = None) -> list:
        """path 가 속한, 지금 활성화된 대상 폴더(또는 그 폴더가 속한 그룹) 키를
        전부 찾는다(보통 하나지만, 같은 경로를 중복 등록했으면 여럿일 수 있다).
        예전엔 첫 번째 하나만 반환해서, 완전히 같은 경로+방식으로 중복 등록해도
        결과가 먼저 등록된 쪽 칩에만 몰리고 나머지 칩은 검색 결과가 있어도 안
        보이는 것처럼 남았다 — 등록한 만큼 각자 별도 폴더로 취급해서 전부 보여
        달라는 요청으로 전부 반환하게 바꿨다. 그룹으로 묶인 폴더 안의 결과는
        그룹 자체로 매핑되어, 결과 목록에도 그룹 이름으로 묶여 보인다(그 안에서
        실제 어느 폴더인지는 _member_folder_for로 또 구분다).

        filename_only가 주어지면, 그 값과 검색 방식(파일명/내용)이 같은 등록만
        후보로 친다 — 안 그러면 같은 경로를 "내용"과 "파일명" 두 가지로 중복
        등록했을 때, 실제로는 파일명만 매칭된 결과(예: "파일명 일치")가 아무
        관련 없는 "내용" 폴더 쪽에도 얹혀 보이는 문제가 있었다(실제로 발견됨 —
        "중복 폴더도 전부 표시" 기능을 넣으면서 생긴 부작용)."""
        p = os.path.normcase(os.path.normpath(path))
        owners = []
        for f in self.settings.folders:
            if not self.settings.folder_enabled.get(f, True):
                continue
            if filename_only is not None and self.settings.folder_filename_only.get(f, False) != filename_only:
                continue
            members = self.settings.folder_groups.get(f)
            candidates = members if members else [f]
            for c in candidates:
                if _path_is_under(p, os.path.normcase(os.path.normpath(c))):
                    owners.append(f)
                    break
        return owners

    def _member_folder_for(self, group_key: str, path: str):
        """그룹으로 묶인 결과가 실제로는 그룹 안의 어느 폴더에서 왔는지 찾는다."""
        p = os.path.normcase(os.path.normpath(path))
        for m in self.settings.folder_groups.get(group_key, []):
            if _path_is_under(p, os.path.normcase(os.path.normpath(m))):
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
            # 같은 파일이 중복 등록된 폴더 여러 개에 동시에 걸리면, 그 폴더들 각각의
            # 결과 목록에 전부 나온다(등록한 만큼 별도 폴더로 취급) — 단, 이 결과가
            # 실제로 어느 검색 방식으로 찾아졌는지(파일명 일치/폴더명 일치 vs
            # 내용 일치)와 등록 방식이 같은 폴더만 후보로 친다.
            wants_fo = bool(r.get("is_dir")) or r.get("location") == "파일명 일치"
            owners = self._owning_folders(r["path"], filename_only=wants_fo) or [None]
            for folder in owners:
                member_label = None
                # 멤버 1개짜리 "그룹"은 진짜 그룹이 아니라 같은 폴더를 다른 검색
                # 방식으로 중복 등록한 것뿐이다(설정 화면과 같은 내부 표현) —
                # 이걸 진짜 그룹처럼 취급해서 멤버 서브헤더를 만들면, 그 멤버의
                # 표시 이름은 실제 경로를 키로 찾다 보니 "다른(원래) 등록"에 붙여둔
                # 표시 이름을 그대로 빌려와서 보여주게 되어(예: "내용" 폴더 밑에
                # "파일명"이라는 멤버가 있는 것처럼 보임) 혼란스러웠다(실제로 겪음).
                # 진짜 여러 폴더를 묶은 그룹(멤버 2개 이상)일 때만 서브헤더를 만든다.
                if folder and len(self.settings.folder_groups.get(folder, [])) >= 2:
                    member = self._member_folder_for(folder, r["path"])
                    if member:
                        # 그 폴더에 표시 이름이 지정돼 있으면 그걸로, 없으면 폴더명으로.
                        member_label = self._folder_display_name(member)
                cat = _categorize_result(r)
                groups.setdefault(folder, {}).setdefault(member_label, {}).setdefault(cat, []).append((r, idx))

        def label_of(folder):
            return self._folder_display_name(folder) if folder else "기타"

        # 결과 묶음 순서 = 검색창 아래 폴더 칩 순서(= 설정 표의 순서). 예전엔 표시
        # 이름 ㄱㄴㄷ순이라, 설정에서 순서를 바꿔도 결과 순서는 그대로여서 둘이
        # 따로 놀았다. 어느 등록에도 안 속한 "기타"는 항상 맨 뒤로 보낸다.
        chip_order = {f: i for i, f in enumerate(self.settings.folders)}
        ordered_folders = sorted(
            groups.keys(),
            key=lambda f: (chip_order.get(f, len(chip_order)), label_of(f)),
        )

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
        flags = Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint
        if self.settings.always_on_top:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        # setWindowFlags()는 내부적으로 네이티브 창을 다시 만들 수 있는데, 그러면
        # WA_TranslucentBackground가 새 네이티브 창에 다시 안 실려서 창이 통째로
        # 불투명한 사각형으로 그려질 수 있다(사용자가 실제로 "둥근 모서리 안에
        # 사각형 배경이 겹쳐 보인다"고 보고해서 확인함) — 플래그를 바꿀 때마다
        # 매번 다시 걸어준다.
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.results_popup.apply_flags(self.settings.always_on_top)

    def apply_settings(self):
        """옵션 변경 후 호출: 항상위/투명도/폴더 목록/텍스트 크기/줄 수를 즉시 반영."""
        was_visible = self.isVisible()
        self._apply_window_flags()
        self.setWindowOpacity(self.settings.opacity)
        self.results_popup.setWindowOpacity(self.settings.opacity)
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
        base = self._base_height()  # bottom_y 보정 계산에만 쓰고, 실제 창 높이는 아래서 다시 구한다
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
        # 칩 줄은 가로 스크롤 스트립이라 창 너비와 무관하게 이미 그려져 있다 —
        # 예전 페이지 방식처럼 열 때마다 다시 나눠 그릴 필요가 없다.
        # base는 위에서 한 번 구했는데, 그건
        # 화면 안으로 bottom_y를 보정하는 용도로만 쓴 것이고, 실제 창 위치를
        # 정할 땐 폴더 칩 줄이 최종적으로 어떻게 그려졌는지(_folder_row_shown이
        # 색인 중 표시 등으로 이 함수 안에서 바뀔 수 있다) 반영해서 다시 구해야
        # 한다 — 안 그러면 창이 원래 자리보다 살짝 아래에 뜨는 문제가 있었다
        # (실제로 겪음 — 닫았다 열면 이번엔 base가 이미 최신이라 제자리에 뜸).
        base = self._base_height()
        # 입력창은 항상 이 고정 크기(base)로만 뜬다 — 결과 팝업은 별도 창이라
        # 여기서 안 건드리고, 아래 _resize_to_fit()이 이전 검색 결과가 남아있으면
        # (results_list를 안 비웠으므로) 그 크기로 따로 띄운다.
        self.setGeometry(x, self._bottom_y - base, width, base)

        self.show()
        self.raise_()
        win_focus.force_foreground(int(self.winId()))
        self.activateWindow()
        self.line_edit.setFocus(Qt.ActiveWindowFocusReason)
        self._resize_to_fit()
        self.line_edit.selectAll()

    def hide_window(self):
        self.hide()
        self.results_popup.hide()

    def _on_drag_start(self):
        pass

    def _on_dragged(self):
        """드래그로 이동한 뒤에는, 그 위치의 하단을 새 기준점으로 삼아
        이후 검색 결과가 그 자리에서 위로 펼쳐지도록 한다. 결과 팝업이 떠 있으면
        입력창을 따라 같이 움직여야, 드래그 중에 둘이 떨어져 보이지 않는다."""
        self._bottom_y = self.y() + self.height()
        self._sync_popup_geometry()

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
            if key in (Qt.Key_Left, Qt.Key_Right):
                self._set_current_category_collapsed(key == Qt.Key_Left)
                return True
        return super().eventFilter(obj, event)

    def event(self, e):
        if e.type() == QEvent.WindowDeactivate:
            # 결과 우클릭 메뉴(QMenu)가 뜨는 순간에도 이 창이 "비활성화"된 걸로
            # 잡혀서, 메뉴에서 뭘 누르기도 전에 검색창 전체가 닫혀버리는 문제가
            # 있었다 — 메뉴가 떠 있는 동안은 무시한다.
            if self._context_menu_open:
                return super().event(e)
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
            self._resize_to_fit()  # 결과 영역이 없어졌으니 창 높이도 다시 줄여야 함(빠뜨리면 빈 공간이 남음)
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
        #
        # 단, self._search_worker 를 곧바로 새 걸로 덮어쓰면 안 된다 — 이전
        # SearchWorker(QThread)가 아직 돌고 있는데 그걸 가리키던 유일한 파이썬
        # 참조가 사라지면 GC가 "아직 실행 중인 QThread"를 수거하려 들어서 앱 전체가
        # 조용히(파이썬 예외 없이) 죽을 수 있다 — 실측으로 확인함(짧은 한글 검색어
        # 입력 중 조합이 스페이스로 끝나며 연달아 검색이 걸릴 때 재현됨). 끝난
        # 워커만 목록에서 지운다.
        worker = SearchWorker(self.indexer, query, self.settings.search_display_limit, self._folder_modes())
        worker.finished_ok.connect(self._on_search_finished)
        worker.finished.connect(lambda w=worker: self._retire_search_worker(w))
        self._search_workers.append(worker)
        self._search_worker = worker
        self._refresh_status_label()
        worker.start()

    def _retire_search_worker(self, worker):
        if worker in self._search_workers:
            self._search_workers.remove(worker)
        if self._search_worker is worker:
            # self._search_worker는 "가장 최근에 시작한 워커"를 가리키는 별도 참조라서
            # (진행 표시줄에서 isRunning()을 물어보는 용도), _search_workers 리스트에서
            # 빼는 것만으론 안 지워진다 — deleteLater()로 곧 C++ 객체가 실제로 지워지는데,
            # 그 뒤에도 이 참조가 남아있으면 다음 빈 검색어 처리 때
            # _refresh_status_label()이 이미 지워진 객체의 isRunning()을 불러서
            # "RuntimeError: Internal C++ object already deleted"가 난다 — 그 예외가
            # _execute_search 중간(결과를 지우기 전)에 터지면서 뒤의 _clear_results()가
            # 아예 실행이 안 돼, 검색어를 지워도 결과가 그대로 남아있는 것처럼 보였다
            # (실측으로 확인함).
            self._search_worker = None
        worker.deleteLater()

    def _on_search_finished(self, query: str, results: list):
        if query != self._last_query:
            return  # 그사이 검색어가 바뀌어서 이제 필요 없어진 결과
        self._refresh_status_label()
        self._results = results
        self._truncated = len(self._results) >= self.settings.search_display_limit
        self._render_results()

    def _refresh_status_label(self):
        """검색 중일 때만 loading_label 을 쓴다 — 색인 중임은 폴더 칩 쪽 로딩
        표시로 따로 보여주므로 여기서 다룰 필요가 없다."""
        if self._search_worker is not None and self._search_worker.isRunning():
            self.loading_label.setText("검색 중…")
            self.loading_label.setVisible(True)
        else:
            self.loading_label.setVisible(False)

    # ---------- 색인 진행 상황 (main.App 이 IndexWorker 시그널을 여기로 연결) ----------
    def on_index_started(self):
        self._indexing = True

    def on_index_progress(self, text: str):
        pass  # 진행 표시는 폴더 칩 쪽(set_indexing_paths)에서 한다

    def on_index_finished(self, _count: int, changed: bool = True):
        self._indexing = False
        # 지금 보고 있는 검색 결과가 방금 끝난 재색인으로 바뀌었을 수 있다 — 화면을
        # 조용히 덮어써버리면(스크롤 위치·선택 항목이 날아감) 당황스러우니, 직접
        # 눌러야 갱신되는 배너로만 알려준다. 다만 이번 재색인에서 실제로 새로
        # 생기거나 바뀌거나 지워진 게 하나도 없었다면(changed=False) 배너를 띄울
        # 이유가 없다 — 다시 검색해봐야 결과가 똑같다.
        if self._last_query and changed:
            self.stale_banner.setVisible(True)
            self._resize_to_fit()

    def set_indexing_paths(self, path_norms: set):
        """지금 색인이 진행 중인 등록 집합 — {(path_norm, 파일명만검색여부), ...}.
        해당 등록에 걸리는 폴더 칩에 로딩 표시를 붙인다(새로 추가한 폴더처럼 아직
        최초 색인이 안 끝난 폴더를 검색창에서 바로 알아볼 수 있게).

        경로만이 아니라 검색 방식까지 묶어서 보는 이유: 같은 폴더를 파일명용·
        내용용으로 두 번 등록할 수 있는데, 경로만 보면 두 칩이 한 덩어리가 돼서
        느린 쪽(내용)이 끝날 때까지 빠른 쪽(파일명) 칩도 계속 돌았다."""
        if path_norms == self._indexing_paths:
            return
        self._indexing_paths = path_norms
        if not path_norms:
            self._folder_progress.clear()
        # 아이콘만 바뀌는 거라 칩 구성 자체(개수)는 그대로다 — 창을 다시
        # 배치할 필요가 없다(resize=False, 관련 버그는 _render_folder_chips
        # 주석 참고).
        self._render_folder_chips(resize=False)
        if path_norms:
            if not self._spinner_timer.isActive():
                self._spinner_timer.start()
        else:
            self._spinner_timer.stop()

    def _advance_spinner(self):
        if not self._spinner_buttons:
            return
        self._spinner_frame = (self._spinner_frame + 1) % _SPINNER_STEPS
        icon = _spinner_icon(self._spinner_frame)
        for btn in self._spinner_buttons.values():
            btn.setIcon(icon)

    def _folder_is_indexing(self, folder: str) -> bool:
        if not self._indexing_paths:
            return False
        # 이 칩 자신의 검색 방식으로 등록된 패스가 아직 안 끝났을 때만 로딩 표시.
        filename_only = bool(self.settings.folder_filename_only.get(folder, False))
        members = self.settings.folder_groups.get(folder)
        paths = members if members else [folder]
        return any((os.path.normcase(os.path.normpath(p)), filename_only) in self._indexing_paths
                    for p in paths)

    def set_folder_progress(self, path_norm: str, found: int, total: int):
        """path_norm 폴더 하나의 진행 상황(찾은 개수/기준 총량 — 지난번 색인 때
        그 폴더 밑에 있던 파일 수를 어림값으로 씀). 툴팁의 퍼센트 표시에만 쓴다.
        칩 구성(개수)은 안 바뀌니 재배치도 필요 없다(set_indexing_paths와
        같은 이유 — 재배치를 시도하면 창이 뜬 직후 진행률 신호가 들어오면서
        아래로 내려가 보이는 문제가 있었다)."""
        self._folder_progress[path_norm] = (found, total)
        if self.isVisible():
            self._render_folder_chips(resize=False)

    def _folder_progress_percent(self, folder: str):
        members = self.settings.folder_groups.get(folder)
        paths = members if members else [folder]
        found_sum = total_sum = 0
        hit = False
        for p in paths:
            entry = self._folder_progress.get(os.path.normcase(os.path.normpath(p)))
            if entry is None:
                continue
            hit = True
            found_sum += entry[0]
            total_sum += entry[1]
        if not hit or total_sum <= 0:
            return None
        return min(99, round(found_sum / total_sum * 100))

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
            # 검색어가 아주 길면(글자 그대로 박아 넣으니) 라벨이 창 너비보다
            # 넓어지길 요구해서 창 자체가 옆으로 늘어져 버렸다 — 검색어 자체를
            # "..."으로 줄인다.
            query = self._last_query or ""
            MAX_QUERY_DISPLAY = 20
            display_query = query if len(query) <= MAX_QUERY_DISPLAY else query[:MAX_QUERY_DISPLAY] + "..."
            status_text = f"'{display_query}' 에 대한 검색 결과가 없습니다"
            metrics = QFontMetrics(self.status_label.font())
            available_width = max(self.width() - 40, 0)
            self.status_label.setText(metrics.elidedText(status_text, Qt.ElideRight, available_width))
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
                    "text": f"결과가 많아 상위 {self.settings.search_display_limit:,}개만 표시 — "
                            f"검색어를 더 구체적으로 입력해보세요.  ",
                    "link": "표시 개수 바꾸기",
                })
                notice.setSizeHint(QSize(width, self._delegate.notice_height()))
                self.results_list.addItem(notice)
                self._row_result_index.append(None)

            for entry in self._group_results(self._results):
                item = QListWidgetItem()
                if entry[0] == "folder":
                    _, label, count, collapsed = entry
                    arrow = "▸" if collapsed else "▾"
                    # 카테고리와 마찬가지로 폴더 헤더도 선택 가능해야 한다(키보드로
                    # 올려서 좌우로 접고 펼 수 있게). 멤버 헤더는 그대로 선택 불가.
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
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
                    # 카테고리는 결과처럼 선택도 가능해야(키보드로 올려서 좌우로
                    # 접고 펼 수 있게) 한다 — 폴더/멤버 헤더는 그대로 선택 불가.
                    item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
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
        elif payload["kind"] == "notice":
            # "표시 개수 바꾸기" 링크 — 옵션 창을 그냥 여는 게 아니라 해당 설정이
            # 있는 페이지로 보내고 그 칸을 잠깐 강조한다.
            self.open_settings_requested.emit("display_limit")
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

    def _popup_target_height(self) -> int:
        """지금 결과/상태/배너 표시 상태에 맞는 결과 팝업의 높이(0이면 보여줄 게
        없다는 뜻 — 팝업 자체를 숨긴다). 입력창(self) 높이는 여기 포함되지 않는다
        — 입력창은 절대 스스로 안 바뀌고, 이 팝업만 커졌다 줄었다 한다.

        isVisible() 대신 isVisibleTo(self.results_popup)를 쓴다 — results_list 등은
        이제 self(입력창)가 아니라 self.results_popup(별도 최상위 창)의 자식이라,
        isVisible()은 그 팝업 자신이 아직 안 보이는 동안(예: 다시 열기 전) 조상까지
        다 보여야 참이 되는 정의상 무조건 False를 준다 — isVisibleTo(results_popup)는
        팝업 자신은 보인다고 가정하고 그 밑의 자식 위젯 상태만 본다."""
        base = self._base_height()
        popup = self.results_popup
        # ensurePolished()가 없으면, 방금 막 보이게 된 라벨의 QSS(padding 등)가
        # 아직 적용되기 전 sizeHint()를 읽어서 실제보다 낮은 높이가 나올 때가
        # 있었다 — 팝업이 그만큼 짧게 떠서 안의 글자가 입력창 쪽으로 겹쳐 보이는
        # 버그로 이어짐(가끔만 재현되던 것도 이 타이밍 문제라 그랬을 가능성이 큼).
        self.stale_banner.ensurePolished()
        self.status_label.ensurePolished()
        banner_h = self.stale_banner.sizeHint().height() if self.stale_banner.isVisibleTo(popup) else 0
        available = max(0, self._max_total_height - base - banner_h)

        body = 0
        if self.results_list.isVisibleTo(popup):
            count = self.results_list.count()
            content_height = sum(self.results_list.item(i).sizeHint().height() for i in range(count)) + 12
            # 다 들어가면 딱 맞게, 넘치면 남은 공간을 최대한 써서 안에서 스크롤
            body = content_height if content_height <= available else available
        elif self.status_label.isVisibleTo(popup):
            # status_label.sizeHint()는 라벨이 막 보이게 된 시점엔 QSS padding이
            # 아직 안 반영된 값을 줄 때가 있었다(ensurePolished()로도 100% 안
            # 잡힘 — 실측으로 확인함). 폰트 메트릭에서 직접 계산하면 위젯의 폴리시
            # 상태와 무관하게 항상 같은 값이 나와서 안전하다. #statusLabel의
            # "padding: 6px 20px"(위+아래 12px)와 맞춘다.
            metrics = QFontMetrics(self.status_label.font())
            body = metrics.height() + 12 + 4

        return banner_h + body

    def _sync_popup_geometry(self):
        """팝업이 떠 있으면, 높이는 그대로 두고 x/너비/y(입력창 바로 위, SEAM_OVERLAP
        만큼 겹치게)만 입력창의 지금 위치·너비에 맞춘다 — 드래그 중이나 좌우
        리사이즈 중처럼 높이는 안 바뀌었는데 입력창 위치/너비만 바뀐 경우에 쓴다."""
        if not self.results_popup.isVisible():
            return
        h = self.results_popup.height()
        self.results_popup.setGeometry(self.x(), self.y() + SEAM_OVERLAP - h, self.width(), h)

    def _resize_to_fit(self):
        """입력창(self)과 결과 팝업을 지금 상태에 맞춰 다시 배치한다. 입력창은 폭/
        폴더 칩 줄 유무로만 정해지는 자기 높이로 필요할 때만 맞추고(검색 결과와는
        무관해서 거의 안 바뀐다), 결과 팝업은 매번 다시 계산해 그 위에 붙인다."""
        if self._bottom_y is None:
            return
        if not self.isVisible():
            # 입력창을 이미 닫은 뒤에도, 그사이 진행 중이던(디바운스) 검색이 뒤늦게
            # 끝나면서 이 함수가 다시 불릴 수 있다 — 그때 팝업만 되살아나면 입력창
            # 없이 결과 팝업만 화면에 남아있는 것처럼 보인다(실제로 겪음). 입력창이
            # 안 보이면 팝업도 무조건 숨긴 채로 아무것도 다시 띄우지 않는다.
            if self.results_popup.isVisible():
                self.results_popup.hide()
            return
        base = self._base_height()
        own_target = QRect(self.x(), self._bottom_y - base, self.width(), base)
        if self.geometry() != own_target:
            self.setGeometry(own_target)

        popup_height = self._popup_target_height()
        if popup_height <= 0:
            if self.results_popup.isVisible():
                self.results_popup.hide()
                self._set_input_card_connected(False)
            return
        popup_target = QRect(
            self.x(),
            self._bottom_y - base - popup_height,
            self.width(),
            popup_height + SEAM_OVERLAP,
        )
        need_show = not self.results_popup.isVisible()
        # 그냥 setGeometry()만 부르면(심지어 두 번 불러도) 안 먹힐 때가 있었다 —
        # card_layout이 "마지막으로 실제 반영됐을 때"의(예: 결과 649px짜리)
        # 최소 크기를 계속 고집해서 엉뚱한 중간값(예: 66)으로 되돌아가는 걸
        # 실측으로 확인함(setGeometry 재시도로도 안 풀림). setFixedHeight로
        # 레이아웃의 의견과 무관하게 팝업 창 자체의 높이를 못박는다.
        self.results_popup.setFixedHeight(popup_target.height())
        if need_show or self.results_popup.geometry() != popup_target:
            self.results_popup.setGeometry(popup_target)
        if need_show:
            self.results_popup.show()
        if self.results_popup.geometry() != popup_target:
            self.results_popup.setGeometry(popup_target)
        self.results_popup.raise_()
        if need_show:
            self._set_input_card_connected(True)

    def _set_input_card_connected(self, connected: bool):
        """결과 팝업이 입력창 바로 위에 떠 있을 때는 입력창 위쪽 모서리를 각지게
        해서 팝업과 하나로 이어진 모양처럼 보이게 하고, 팝업이 없을 때는(평소 알약
        모양) 다시 네 모서리 다 둥글게 되돌린다."""
        if connected:
            _set_card_corners(self.card, 0, 0, _CARD_RADIUS, _CARD_RADIUS)
        else:
            _set_card_corners(self.card, _CARD_RADIUS, _CARD_RADIUS, _CARD_RADIUS, _CARD_RADIUS)

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
            # 카테고리/폴더 헤더도(멤버 헤더는 빼고) 선택 대상이다 — 키보드로
            # 올려서 거기 멈출 수 있어야 좌우로 접고 펼 수 있다.
            payload = self.results_list.item(r).data(Qt.UserRole) or {}
            if payload.get("kind") in ("category", "folder"):
                self.results_list.setCurrentRow(r)
                return

    def _current_header_payload(self):
        """지금 선택된 행이 폴더/카테고리 헤더면 그 자신의 payload, 결과 행이면
        그 결과가 속한 카테고리 헤더의 payload. 결과 행은 항상(접혀 있지 않아야
        보이므로) 자기 카테고리 헤더 바로 아래에 있다 — 위로 올라가면서 처음
        만나는 category/folder 헤더가 그것이다(결과 행에서 시작하면 항상
        category가 먼저 걸린다)."""
        row = self.results_list.currentRow()
        if row < 0:
            return None
        for r in range(row, -1, -1):
            payload = self.results_list.item(r).data(Qt.UserRole) or {}
            if payload.get("kind") in ("category", "folder"):
                return payload
        return None

    def _select_header_row(self, kind, match_fn):
        for r in range(self.results_list.count()):
            payload = self.results_list.item(r).data(Qt.UserRole) or {}
            if payload.get("kind") == kind and match_fn(payload):
                self.results_list.setCurrentRow(r)
                return

    def _set_current_category_collapsed(self, collapsed: bool):
        """왼쪽/오른쪽 화살표로 지금 선택된 폴더/카테고리(또는 선택된 결과가
        속한 카테고리)를 접거나 편다(토글이 아니라 방향대로 — 왼쪽은 항상 접기,
        오른쪽은 항상 펴기)."""
        payload = self._current_header_payload()
        if payload is None:
            return
        if payload["kind"] == "folder":
            label = payload["label"]
            target = self._collapsed_folders
            key = label
            select = lambda: self._select_header_row("folder", lambda p: p.get("label") == label)
        else:
            key = payload["key"]
            target = self._collapsed_categories
            select = lambda: self._select_header_row("category", lambda p: p.get("key") == key)
        if collapsed:
            target.add(key)
        else:
            target.discard(key)
        self._render_results()
        # 접으면 그 안의 행이 사라지므로, 선택을 헤더 자신으로 옮겨서 방금
        # 어디를 접었다 폈다 했는지 계속 눈에 보이게 한다.
        select()

    def _open_selected(self, *_):
        row = self.results_list.currentRow()
        if row < 0 or row >= len(self._row_result_index):
            return
        idx = self._row_result_index[row]
        if idx is None or idx >= len(self._results):
            return
        open_result(self._results[idx])
        self.hide_window()

    def _show_result_context_menu(self, pos):
        row = self.results_list.indexAt(pos).row()
        if row < 0 or row >= len(self._row_result_index):
            return
        idx = self._row_result_index[row]
        if idx is None or idx >= len(self._results):
            return  # 폴더/카테고리 같은 헤더 행은 메뉴 없음
        self.results_list.setCurrentRow(row)
        result = self._results[idx]
        path = result["path"]

        menu = QMenu(self.results_list)
        open_action = menu.addAction("열기")
        open_folder_action = menu.addAction("파일 위치 열기")
        menu.addSeparator()
        copy_path_action = menu.addAction("경로 복사")

        self._context_menu_open = True
        try:
            chosen = menu.exec(self.results_list.viewport().mapToGlobal(pos))
        finally:
            self._context_menu_open = False

        if chosen == open_action:
            open_result(result)
            self.hide_window()
        elif chosen == open_folder_action:
            open_containing_folder(path)
            self.hide_window()
        elif chosen == copy_path_action:
            QApplication.clipboard().setText(path)
