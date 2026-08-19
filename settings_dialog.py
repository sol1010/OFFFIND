"""옵션 창: 항상 위, 투명도, 전역 단축키, 검색 폴더, 윈도우 시작프로그램."""
import os
import uuid
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QIcon, QPalette
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QCheckBox, QSlider,
    QLabel, QLineEdit, QPushButton, QFileDialog, QGroupBox, QFrame, QWidget,
    QMessageBox, QTableWidget, QTableWidgetItem, QComboBox, QHeaderView,
    QInputDialog, QSpinBox, QSizePolicy, QStackedWidget, QListWidget,
    QListWidgetItem,
)

from icons import ICON_PATH

GROUP_PREFIX = "GROUP::"

# 표의 각 행에 붙는 부가정보: 행이 실제 폴더/그룹인지("primary"), 아니면 펼쳐진
# 그룹 안에서 보여주는 멤버 폴더 한 줄인지("member")를 구분한다.
ROLE_KEY = Qt.UserRole
ROLE_KIND = Qt.UserRole + 1
ROLE_PARENT = Qt.UserRole + 2

SEARCH_MODE_OPTIONS = ["내용", "파일명"]

MODIFIER_KEYS = {
    Qt.Key_Control: "ctrl", Qt.Key_Shift: "shift",
    Qt.Key_Alt: "alt", Qt.Key_Meta: "windows",
}

DIALOG_STYLE = """
QDialog {
    background-color: #202124;
}
QLabel {
    color: #e8eaed;
    font-size: 12px;
}
QGroupBox {
    color: #e8eaed;
    font-size: 12px;
    font-weight: 600;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 10px;
    margin-top: 16px;
    padding: 16px 12px 12px 12px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 6px;
    color: #8ab4f8;
}
QCheckBox {
    color: #e8eaed;
    font-size: 12px;
    spacing: 8px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid rgba(255, 255, 255, 70);
    background: rgba(255, 255, 255, 10);
}
QCheckBox::indicator:checked {
    background-color: #8ab4f8;
    border: 1px solid #8ab4f8;
}
QLineEdit, QComboBox, QSpinBox {
    background-color: rgba(255, 255, 255, 12);
    color: #e8eaed;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 8px;
    padding: 5px 8px;
    font-size: 12px;
    selection-background-color: rgba(138, 180, 248, 90);
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #8ab4f8;
}
QComboBox::drop-down {
    border: none;
    width: 20px;
}
/* 위/아래 버튼에 배경을 깔면 입력칸의 둥근 테두리와 각진 회색 덩어리가 겹쳐서
   어색해 보인다 — 평소엔 화살표만 두고, 올렸을 때만 은은하게 표시한다. */
QSpinBox {
    padding-right: 20px;  /* 숫자가 화살표에 닿지 않도록 */
}
QSpinBox::up-button, QSpinBox::down-button {
    background: transparent;
    border: none;
    width: 16px;
    subcontrol-origin: border;
}
QSpinBox::up-button {
    subcontrol-position: top right;
    margin: 2px 3px 0 0;
}
QSpinBox::down-button {
    subcontrol-position: bottom right;
    margin: 0 3px 2px 0;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {
    background: rgba(255, 255, 255, 22);
    border-radius: 3px;
}
/* 표 안(표시 이름/검색 방식) 셀은 행 높이가 낮아 둥근 테두리 박스가 위아래로
   잘려 보이므로, 배경/테두리 없이 텍스트만 보이도록 한다. */
QLineEdit#cellLineEdit, QComboBox#cellCombo {
    background: transparent;
    border: none;
    border-radius: 0;
    padding: 2px 4px;
}
QLineEdit#cellLineEdit:focus, QComboBox#cellCombo:focus {
    border: none;
    background: rgba(255, 255, 255, 14);
}
QComboBox QAbstractItemView {
    background-color: #2a2b2e;
    color: #e8eaed;
    selection-background-color: rgba(138, 180, 248, 60);
    border: 1px solid rgba(255, 255, 255, 30);
    outline: none;
}
QPushButton {
    background-color: rgba(255, 255, 255, 14);
    color: #e8eaed;
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 8px;
    padding: 6px 14px;
    font-size: 12px;
}
QPushButton:hover {
    background-color: rgba(255, 255, 255, 24);
}
QPushButton:pressed {
    background-color: rgba(255, 255, 255, 32);
}
QPushButton:disabled {
    color: rgba(232, 234, 237, 80);
    background-color: rgba(255, 255, 255, 6);
}
QPushButton#primaryBtn {
    background-color: #8ab4f8;
    color: #1c1d1f;
    font-weight: 700;
    border: none;
}
QPushButton#primaryBtn:hover {
    background-color: #aecbfa;
}
QSlider::groove:horizontal {
    height: 4px;
    background: rgba(255, 255, 255, 24);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 14px;
    height: 14px;
    margin: -5px 0;
    background: #8ab4f8;
    border-radius: 7px;
}
QSlider::sub-page:horizontal {
    background: #8ab4f8;
    border-radius: 2px;
}
QTableWidget {
    background-color: rgba(255, 255, 255, 6);
    color: #e8eaed;
    border: 1px solid rgba(255, 255, 255, 24);
    border-radius: 8px;
    gridline-color: rgba(255, 255, 255, 14);
    font-size: 12px;
    selection-background-color: rgba(138, 180, 248, 45);
    selection-color: #e8eaed;
}
QTableWidget::item {
    padding: 4px;
}
QHeaderView::section {
    background-color: rgba(255, 255, 255, 10);
    color: #9aa0a6;
    padding: 6px;
    border: none;
    border-bottom: 1px solid rgba(255, 255, 255, 24);
    font-size: 11px;
    font-weight: 600;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
}
QScrollBar::handle:vertical {
    background: rgba(255, 255, 255, 45);
    border-radius: 5px;
    min-height: 24px;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
}
QScrollBar::handle:horizontal {
    background: rgba(255, 255, 255, 45);
    border-radius: 5px;
    min-width: 24px;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0px;
    height: 0px;
}
#previewPanel {
    background-color: rgba(255, 255, 255, 6);
    border: 1px solid rgba(255, 255, 255, 24);
    border-radius: 10px;
}
#previewTitle {
    color: #8ab4f8;
    font-size: 11px;
    font-weight: 700;
}
/* ---- 왼쪽 카테고리 사이드바 (디스코드 설정 형태) ---- */
#settingsNav {
    background-color: #17181b;
    border: none;
    outline: none;
    padding: 12px 6px;
}
#settingsNav::item {
    color: #b5bac1;
    border-radius: 6px;
    padding: 7px 10px;
    margin: 1px 4px;
}
#settingsNav::item:hover {
    background-color: rgba(255, 255, 255, 16);
    color: #e8eaed;
}
#settingsNav::item:selected {
    background-color: rgba(255, 255, 255, 30);
    color: #ffffff;
}
#settingsContent {
    background-color: #202124;
}
#pageTitle {
    color: #e8eaed;
    font-size: 16px;
    font-weight: 700;
}
#sectionLabel {
    color: #9aa0a6;
    font-size: 11px;
    font-weight: 700;
}
#sectionRule {
    background-color: rgba(255, 255, 255, 20);
    border: none;
}
"""


class NoScrollComboBox(QComboBox):
    """표 안에서 마우스 휠로 표를 스크롤하다가 콤보박스 위를 지나가면 Qt 기본
    동작상 스크롤이 아니라 '선택값이 바뀌는' 사고가 난다(검색 방식이 실수로
    뒤바뀜). 휠 이벤트를 무시해서 반드시 클릭해서 고르게 하고, 대신 휠은
    부모(표)로 넘어가 정상적으로 스크롤되게 한다."""

    def wheelEvent(self, event):
        event.ignore()


class NoScrollSlider(QSlider):
    """옵션 창을 마우스 휠로 스크롤하며 훑어보다가 슬라이더 위를 지나가면 값이
    실수로 바뀐다(투명도/너비 등). 휠 이벤트를 무시해서 반드시 드래그로만
    바꾸게 하고, 휠은 부모(스크롤 영역)로 넘겨 정상적으로 스크롤되게 한다."""

    def wheelEvent(self, event):
        event.ignore()


class NoScrollSpinBox(QSpinBox):
    """스핀박스도 마찬가지로 휠 스크롤 중 값이 실수로 바뀌는 걸 막는다."""

    def wheelEvent(self, event):
        event.ignore()


class HotkeyCaptureEdit(QLineEdit):
    """클릭 후 원하는 키 조합을 누르면 'ctrl+alt+space' 형식으로 기록."""

    def __init__(self, initial: str):
        super().__init__()
        self.setReadOnly(True)
        self.setPlaceholderText("클릭 후 단축키 입력…")
        self.value = initial
        self.setText(initial)

    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Backspace, Qt.Key_Delete):
            self.value = ""
            self.setText("")
            return
        if key in MODIFIER_KEYS:
            return  # 조합키 단독 입력은 무시, 다른 키와 함께 눌러야 확정

        parts = []
        mods = event.modifiers()
        if mods & Qt.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.AltModifier:
            parts.append("alt")
        if mods & Qt.ShiftModifier:
            parts.append("shift")
        if mods & Qt.MetaModifier:
            parts.append("windows")

        key_name = QKeySequenceName(key)
        if key_name:
            parts.append(key_name)
            self.value = "+".join(parts)
            self.setText(self.value)


def QKeySequenceName(key: int) -> str:
    from PySide6.QtGui import QKeySequence
    seq = QKeySequence(key)
    text = seq.toString(QKeySequence.NativeText)
    if not text:
        return ""
    return text.lower()


class SettingsDialog(QDialog):
    """처음 열릴 때 한 번, 화면 중앙에 위치를 잡는다(이후 사용자가 직접
    크기를 바꿔도 다시 옮기지 않는다 — showEvent 참고)."""

    saved = Signal()

    def __init__(self, settings, parent=None, focus: str = ""):
        """focus 가 주어지면 그 설정 칸이 있는 페이지를 열고 잠깐 강조한다
        (검색 결과의 "표시 개수 바꾸기" 링크처럼, 특정 설정으로 바로 보내야
        할 때 쓴다)."""
        super().__init__(parent)
        self.settings = settings
        self._focus_key = focus
        self.setWindowTitle("OFFFIND 옵션")
        self.setMinimumWidth(520)
        # 스타일시트만 걸어두면, 새 네이티브 창이 뜰 때 QSS가 실제로 칠해지기
        # 전(첫 페인트 이전)의 기본 배경(흰색)이 한 프레임 그대로 보이는 경우가
        # 있었다(실제로 겪음) — 팔레트 배경색도 같이 미리 어둡게 맞춰서, 무슨
        # 배경이 먼저 그려지든 흰색이 안 보이게 한다.
        self.setAutoFillBackground(True)
        pal = self.palette()
        pal.setColor(QPalette.Window, QColor("#202124"))
        self.setPalette(pal)
        self.setStyleSheet(DIALOG_STYLE)
        self._anchor_x = None
        self._anchor_bottom_y = None
        self.setWindowIcon(QIcon(ICON_PATH))
        if settings.options_width and settings.options_height:
            self.resize(settings.options_width, settings.options_height)
        # 그룹 멤버는 세션 동안 이 사본으로 편집하고, "저장"을 눌러야만
        # self.settings.folder_groups 로 반영된다(취소하면 그대로 버려짐).
        self._group_members = {k: list(v) for k, v in settings.folder_groups.items()}
        # 그룹 안에 묶여서 표에 따로 안 보이는 폴더들의 표시 이름도 기억해 둔다 —
        # 그룹으로 묶기 전에 지정해 뒀던 이름을 그대로 살려서 결과 소제목에 쓴다.
        self._member_display_names = {
            m: settings.folder_display_name[m]
            for members in settings.folder_groups.values()
            for m in members
            if m in settings.folder_display_name
        }
        self._expanded_groups = set()  # 이번 세션에 펼쳐 놓은 그룹 키들
        self._page_row_by_key = {}  # 'display_limit' 같은 키 -> 사이드바 행 번호
        self._build_ui()
        if focus:
            self._go_to(focus)

    def showEvent(self, event):
        super().showEvent(event)
        # 창을 처음 열 때 한 번만 화면 중앙 기준 위치를 잡는다. 이후 사용자가 직접
        # 창 가장자리를 드래그해서 크기를 바꿀 때는 절대 다시 move() 하지 않는다 —
        # 이전에는 resizeEvent마다 강제로 되돌려서 사용자가 리사이즈 중에 창이
        # 계속 떨리며 순간이동하는 것처럼 보였다.
        if self._anchor_bottom_y is None:
            screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
            geo = screen.availableGeometry()
            self._anchor_x = geo.x() + (geo.width() - self.width()) // 2
            self._anchor_bottom_y = geo.y() + (geo.height() - self.height()) // 2
            self.move(self._anchor_x, self._anchor_bottom_y)

    def done(self, result):
        # 저장/취소/닫기 어느 경로로 닫히든 창 크기·표 칸 너비는 항상 기억해 둔다
        # (폴더/단축키 등 다른 설정과 달리 "저장" 버튼을 눌러야만 남는 값이 아니다).
        self.settings.options_width = self.width()
        self.settings.options_height = self.height()
        self.settings.folder_column_widths = [
            self.folder_table.columnWidth(col) for col in range(self.folder_table.columnCount())
        ]
        self.settings.save()
        super().done(result)

    def _add_nav_header(self, text: str):
        """사이드바의 작은 회색 구분 제목(선택 불가)."""
        item = QListWidgetItem(text)
        item.setFlags(Qt.NoItemFlags)
        font = item.font()
        font.setPointSize(max(7, font.pointSize() - 1))
        font.setBold(True)
        item.setFont(font)
        item.setForeground(QColor("#8a8f98"))
        self.nav.addItem(item)

    def _new_page(self, title: str, key: str = ""):
        """제목만 얹은 빈 페이지를 만들어 스택에 넣고, 사이드바에도 항목을
        추가한다. 반환한 세로 레이아웃에 페이지 내용을 채우면 된다.
        key 를 주면 나중에 _go_to(key) 로 그 페이지를 바로 열 수 있다."""
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(14)

        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        page_layout.addWidget(heading)

        rule = QFrame()
        rule.setObjectName("sectionRule")
        rule.setFixedHeight(1)
        page_layout.addWidget(rule)

        index = self.pages.count()
        self.pages.addWidget(page)
        nav_item = QListWidgetItem(title)
        self.nav.addItem(nav_item)
        row = self.nav.row(nav_item)
        self._nav_page_index[row] = index
        if key:
            self._page_row_by_key[key] = row
        return page_layout

    def _on_nav_changed(self, row: int):
        index = self._nav_page_index.get(row)
        if index is not None:
            self.pages.setCurrentIndex(index)

    def _go_to(self, key: str):
        """그 설정이 있는 페이지를 열고, 해당 입력칸을 잠깐 강조한다."""
        row = self._page_row_by_key.get(key)
        if row is None:
            return
        self.nav.setCurrentRow(row)
        widget = {"display_limit": lambda: self.display_limit_spin}.get(key)
        if widget:
            self._flash(widget())

    def _flash(self, widget, msec: int = 1100):
        """어느 칸을 보러 온 건지 눈에 띄게 잠깐 테두리를 준다.

        전역 스타일시트가 이미 이 위젯을 칠하고 있어서, 인스턴스에 직접 건
        스타일시트로 잠시 덮어썼다가 원래대로(빈 문자열) 돌려놓는다."""
        widget.setFocus()
        widget.setStyleSheet(
            "border: 2px solid #8ab4f8;"
            "background-color: rgba(138, 180, 248, 45);"
        )
        QTimer.singleShot(msec, lambda: widget.setStyleSheet(""))

    def _section(self, parent_layout, title: str) -> QFormLayout:
        """페이지 안의 한 묶음(작은 회색 제목 + 폼)을 만들고 그 폼을 돌려준다.
        항목이 많은 페이지를 성격별로 나눠서 훑어보기 쉽게 하려는 것."""
        label = QLabel(title)
        label.setObjectName("sectionLabel")
        parent_layout.addWidget(label)

        host = QWidget()
        form = QFormLayout(host)
        form.setContentsMargins(2, 0, 0, 0)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(11)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
        parent_layout.addWidget(host)
        return form

    def _form_label(self, text: str) -> QLabel:
        """폼 라벨 칸 폭을 고정한다 — 섹션마다 폼이 따로라서 그냥 두면 각 섹션의
        가장 긴 라벨에 맞춰 입력칸 시작 위치가 제각각이 된다(실제로 그렇게 보였다).
        같은 폭을 쓰면 페이지 전체에서 입력칸이 한 줄로 정렬된다."""
        label = QLabel(text)
        label.setFixedWidth(112)
        return label

    def _build_ui(self):
        self.setMinimumWidth(840)
        self._nav_page_index = {}  # 사이드바 행 번호 -> 페이지 인덱스(구분 제목 행은 없음)

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("settingsNav")
        self.nav.setFixedWidth(184)
        self.nav.setFrameShape(QFrame.NoFrame)
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self.nav)

        content = QWidget()
        content.setObjectName("settingsContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(26, 22, 26, 16)
        content_layout.setSpacing(14)
        root.addWidget(content, 1)

        self.pages = QStackedWidget()
        content_layout.addWidget(self.pages, 1)

        # ---------- 일반 ----------
        self._add_nav_header("검색창 설정")
        general_page = self._new_page("일반")
        form = self._section(general_page, "검색창")

        self.always_on_top_cb = QCheckBox("항상 위에 표시")
        self.always_on_top_cb.setChecked(self.settings.always_on_top)
        form.addRow(self.always_on_top_cb)

        self.keep_visible_cb = QCheckBox("항상 떠있기 (다른 곳을 클릭해도 닫히지 않음)")
        self.keep_visible_cb.setToolTip("검색창이 포커스를 잃거나 결과 파일을 열어도 닫히지 않습니다.\n"
                                        "Esc 또는 전역 단축키로만 숨겨지고, 앱 시작 시에도 바로 떠 있습니다.")
        self.keep_visible_cb.setChecked(self.settings.keep_visible)
        form.addRow(self.keep_visible_cb)

        opacity_row = QHBoxLayout()
        self.opacity_slider = NoScrollSlider(Qt.Horizontal)
        self.opacity_slider.setRange(30, 100)
        self.opacity_slider.setValue(int(self.settings.opacity * 100))
        self.opacity_value_label = QLabel(f"{self.opacity_slider.value()}%")
        self.opacity_slider.valueChanged.connect(
            lambda v: self.opacity_value_label.setText(f"{v}%")
        )
        opacity_row.addWidget(self.opacity_slider, 1)
        opacity_row.addWidget(self.opacity_value_label)
        form.addRow(self._form_label("투명도"), opacity_row)

        width_row = QHBoxLayout()
        self.width_slider = NoScrollSlider(Qt.Horizontal)
        self.width_slider.setRange(20, 90)
        self.width_slider.setValue(int(self.settings.width_percent * 100))
        self.width_value_label = QLabel(f"화면의 {self.width_slider.value()}%")
        self.width_slider.valueChanged.connect(
            lambda v: self.width_value_label.setText(f"화면의 {v}%")
        )
        width_row.addWidget(self.width_slider, 1)
        width_row.addWidget(self.width_value_label)
        form.addRow(self._form_label("검색창 너비"), width_row)

        width_hint = QLabel("검색창 가장자리를 직접 드래그해서 조절할 수도 있습니다(최소 화면의 20%).")
        width_hint.setWordWrap(True)
        width_hint.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        form.addRow("", width_hint)

        general_page.addSpacing(10)
        form = self._section(general_page, "실행")

        self.hotkey_edit = HotkeyCaptureEdit(self.settings.hotkey)
        # 기본 QFormLayout 필드 폭은 텍스트 길이에 딱 맞춰져서(sizeHint 기준)
        # "ctrl+shift+space"처럼 조금만 길어져도 잘려 보였다 — 넉넉히 잡아준다.
        self.hotkey_edit.setFixedWidth(240)
        form.addRow(self._form_label("전역 단축키"), self.hotkey_edit)

        self.startup_cb = QCheckBox("윈도우 시작 시 자동 실행")
        try:
            from startup import is_startup_enabled
            self.startup_cb.setChecked(is_startup_enabled())
        except Exception:
            self.startup_cb.setEnabled(False)
        form.addRow(self.startup_cb)
        general_page.addStretch(1)

        # ---------- 검색 결과 표시 ----------
        display_page = self._new_page("검색 결과 표시", key="display_limit")
        display_row = QHBoxLayout()
        display_row.setSpacing(0)
        display_page.addLayout(display_row)
        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(8)
        display_row.addLayout(left_col, 1)

        form = self._section(left_col, "텍스트 크기")

        self.filename_px_spin = NoScrollSpinBox()
        self.filename_px_spin.setRange(8, 24)
        self.filename_px_spin.setSuffix("px")
        self.filename_px_spin.setFixedWidth(110)
        self.filename_px_spin.setValue(self.settings.filename_font_px)
        self.filename_px_spin.valueChanged.connect(self._update_preview)
        form.addRow(self._form_label("파일명"), self.filename_px_spin)

        self.content_px_spin = NoScrollSpinBox()
        self.content_px_spin.setRange(8, 24)
        self.content_px_spin.setSuffix("px")
        self.content_px_spin.setFixedWidth(110)
        self.content_px_spin.setValue(self.settings.content_font_px)
        self.content_px_spin.valueChanged.connect(self._update_preview)
        form.addRow(self._form_label("내용"), self.content_px_spin)

        self.snippet_lines_spin = NoScrollSpinBox()
        self.snippet_lines_spin.setRange(1, 5)
        self.snippet_lines_spin.setSuffix("줄")
        self.snippet_lines_spin.setFixedWidth(110)
        self.snippet_lines_spin.setValue(self.settings.snippet_max_lines)
        self.snippet_lines_spin.valueChanged.connect(self._update_preview)
        form.addRow(self._form_label("내용 표시 줄 수"), self.snippet_lines_spin)
        # 파일명은 검색창에서 항상 한 줄(잘리면 …로 축약)로만 보여준다 — 여러 줄로
        # 감싸면 결과 목록 하나하나가 세로로 너무 길어져서 오히려 훑어보기 어렵다.

        left_col.addSpacing(10)
        form = self._section(left_col, "결과 개수")

        self.display_limit_spin = NoScrollSpinBox()
        self.display_limit_spin.setRange(100, 20000)
        self.display_limit_spin.setSingleStep(100)
        self.display_limit_spin.setFixedWidth(110)
        self.display_limit_spin.setValue(self.settings.search_display_limit)
        form.addRow(self._form_label("한 번에 표시"), self.display_limit_spin)

        # 예전엔 "많이 올려도 느려지지 않는다"고 써 뒀는데 사실이 아니다 — 그건
        # 화면에 그리는 비용에만 해당하고, 이 값이 SQLite 가 훑는 양(약 4배)과
        # 미리보기를 만드는 양을 그대로 정한다(2글자 검색 기준 1,000이면 약 0.1초,
        # 10,000이면 약 0.6초로 실측됨). 사용자에게는 색인이 어떻게 도는지까지
        # 설명할 필요가 없으니 "속도에 영향이 있다"까지만 알린다.
        limit_hint = QLabel(
            "이 개수를 넘으면 결과 맨 위에 안내가 뜹니다.\n"
            "숫자가 크면 검색 속도에 영향을 줄 수 있습니다. 기본값 2,000"
        )
        limit_hint.setWordWrap(True)
        limit_hint.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        form.addRow("", limit_hint)
        left_col.addStretch(1)

        # 미리보기 패널은 위 스핀박스 값들을 읽어서 그리므로(_update_preview)
        # 반드시 그것들이 다 만들어진 뒤에 붙여야 한다.
        display_row.addSpacing(20)
        display_row.addWidget(self._build_preview_panel())
        display_page.addStretch(1)

        # ---------- 검색 대상 폴더 ----------
        folder_page = self._new_page("검색 대상 폴더")
        fbox_layout = folder_page

        folder_desc = QLabel("여기에 등록한 폴더 안에서만 검색합니다. 폴더별로 표시 이름과 검색 방식을 따로 정할 수 있습니다.")
        folder_desc.setWordWrap(True)
        folder_desc.setStyleSheet("color: #9aa0a6; font-size: 11px;")
        fbox_layout.addWidget(folder_desc)

        self.folder_table = QTableWidget(0, 5)
        self.folder_table.setHorizontalHeaderLabels(["번호", "폴더", "표시 이름", "검색 방식", "비고"])
        self.folder_table.verticalHeader().setVisible(False)
        self.folder_table.verticalHeader().setDefaultSectionSize(32)
        # 칸 전부 Interactive로 둬야 사용자가 자유롭게 폭을 조절할 수 있다.
        # 이전에 폴더 칸이 Stretch였는데, Stretch 칸은 창 폭에 맞춰 항상 강제로
        # 늘어나거나 줄어들어서(직접 드래그로 조절이 안 됨) 긴 경로가 잘리기만
        # 하고, 다른 칸을 조절할 때도 같이 흔들리는 것처럼 보이는 원인이었다.
        # 대신 가로 스크롤을 켜서, 넘치는 내용은 잘리지 않고 옆으로 스크롤해서
        # 볼 수 있게 한다.
        header = self.folder_table.horizontalHeader()
        for col in range(5):
            header.setSectionResizeMode(col, QHeaderView.Interactive)
        header.setStretchLastSection(False)
        saved_widths = self.settings.folder_column_widths
        default_widths = [42, 260, 130, 110, 130]
        widths = saved_widths if len(saved_widths) == 5 else default_widths
        for col, w in enumerate(widths):
            self.folder_table.setColumnWidth(col, w)
        self.folder_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.folder_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.folder_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.folder_table.setAlternatingRowColors(False)
        self.folder_table.setMinimumHeight(160)
        self.folder_table.cellClicked.connect(self._on_cell_clicked)
        for f in self.settings.folders:
            members = self._group_members.get(f)
            # 멤버 1개짜리 그룹은 진짜 그룹이 아니라 "같은 폴더를 다른 검색
            # 방식으로 한 번 더 추가한" 항목이다 — 다시 열었을 때도 "🔗 그룹 ·
            # 1개 폴더"가 아니라 처음 추가할 때처럼 실제 폴더 이름으로 보여야
            # 한다(실측으로 확인 — 저장 후 재실행하면 여기서 안 걸러져서 그룹
            # 표시로 나왔었다).
            if members and len(members) == 1:
                self._add_folder_row(
                    f,
                    self.settings.folder_display_name.get(f, ""),
                    self.settings.folder_filename_only.get(f, False),
                    real_path=members[0],
                )
            else:
                self._add_folder_row(
                    f,
                    self.settings.folder_display_name.get(f, ""),
                    self.settings.folder_filename_only.get(f, False),
                    is_group=bool(members),
                    member_count=len(members or []),
                )
        fbox_layout.addWidget(self.folder_table)

        folder_btn_row = QHBoxLayout()
        folder_btn_row.setSpacing(8)
        add_btn = QPushButton("➕ 폴더 추가")
        add_btn.clicked.connect(self._add_folder)
        remove_btn = QPushButton("🗑 선택 삭제")
        remove_btn.clicked.connect(self._remove_folder)
        up_btn = QPushButton("▲ 위로")
        up_btn.clicked.connect(lambda: self._move_folder(-1))
        down_btn = QPushButton("▼ 아래로")
        down_btn.clicked.connect(lambda: self._move_folder(1))
        group_btn = QPushButton("🔗 그룹으로 묶기")
        group_btn.clicked.connect(self._group_selected)
        ungroup_btn = QPushButton("🔓 그룹 해제")
        ungroup_btn.clicked.connect(self._ungroup_selected)
        folder_btn_row.addWidget(add_btn)
        folder_btn_row.addWidget(remove_btn)
        folder_btn_row.addSpacing(10)
        folder_btn_row.addWidget(up_btn)
        folder_btn_row.addWidget(down_btn)
        folder_btn_row.addSpacing(10)
        folder_btn_row.addWidget(group_btn)
        folder_btn_row.addWidget(ungroup_btn)
        folder_btn_row.addStretch(1)
        fbox_layout.addLayout(folder_btn_row)

        # 예전엔 이 안내가 네 문장짜리 한 덩어리 문단이라 실제로 읽히지 않았다 —
        # 항목별로 끊고 제목을 굵게 해서 필요한 줄만 눈에 들어오게 한다.
        hint = QLabel(
            "<table cellspacing='0' cellpadding='3'>"
            "<tr><td style='color:#c9cdd2;'><b>검색 방식</b></td>"
            "<td style='color:#9aa0a6;'>파일명 = 이름만 검색 (빠름) &nbsp;·&nbsp; "
            "내용 = 문서 안 글자까지 검색 (엑셀·PDF·워드·PPT)</td></tr>"
            "<tr><td style='color:#c9cdd2;'><b>순서</b></td>"
            "<td style='color:#9aa0a6;'>위 표의 순서 그대로 검색창 아래 폴더 칩이 나열됩니다 "
            "(▲ ▼ 로 변경).</td></tr>"
            "<tr><td style='color:#c9cdd2;'><b>같은 폴더 또 추가</b></td>"
            "<td style='color:#9aa0a6;'>한 폴더를 파일명용·내용용으로 따로 둘 수 있습니다. "
            "겹치는 항목은 <b>비고</b>에 표시되고, 색인은 한 번만 돕니다.</td></tr>"
            "<tr><td style='color:#c9cdd2;'><b>그룹으로 묶기</b></td>"
            "<td style='color:#9aa0a6;'>여러 폴더를 검색창에서 칩 하나로 합쳐 보여줍니다.</td></tr>"
            "<tr><td style='color:#c9cdd2;'><b>새 폴더</b></td>"
            "<td style='color:#9aa0a6;'>저장하면 백그라운드로 색인이 시작되고, 끝날 때까지 "
            "폴더 칩에 로딩 표시가 돕니다.</td></tr>"
            "</table>"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("font-size: 11px;")
        fbox_layout.addWidget(hint)

        # 저장/취소는 어느 페이지를 보고 있든 항상 같은 자리에 있어야 하므로
        # 페이지 안이 아니라 오른쪽 영역 아래에 고정한다.
        footer_rule = QFrame()
        footer_rule.setObjectName("sectionRule")
        footer_rule.setFixedHeight(1)
        content_layout.addWidget(footer_rule)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        cancel_btn = QPushButton("취소")
        cancel_btn.clicked.connect(self.reject)
        save_btn = QPushButton("저장")
        save_btn.setObjectName("primaryBtn")
        save_btn.setDefault(True)
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        content_layout.addLayout(btn_row)

        self.nav.currentRowChanged.connect(self._on_nav_changed)
        # 구분 제목(선택 불가)이 0번이라, 실제로 고를 수 있는 첫 항목을 켜 둔다.
        if self._nav_page_index:
            self.nav.setCurrentRow(min(self._nav_page_index))

    def _build_preview_panel(self) -> QFrame:
        """검색 결과가 실제로 어떻게 보일지 옆에서 바로 확인할 수 있는 샘플
        상자. 텍스트 크기/줄 수 값이 바뀔 때마다 _update_preview()가 폰트와
        내용을 다시 계산해서 여기 반영한다."""
        panel = QFrame()
        panel.setObjectName("previewPanel")
        panel.setFixedWidth(200)
        panel.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Expanding)
        v = QVBoxLayout(panel)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        title = QLabel("미리보기")
        title.setObjectName("previewTitle")
        v.addWidget(title)

        self.preview_filename_label = QLabel()
        self.preview_filename_label.setStyleSheet("color: #b0b3b8;")
        v.addWidget(self.preview_filename_label)

        self.preview_content_label = QLabel()
        self.preview_content_label.setWordWrap(True)
        self.preview_content_label.setStyleSheet("color: #d4d6d9;")
        self.preview_content_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        v.addWidget(self.preview_content_label)

        v.addStretch(1)
        self._update_preview()
        return panel

    def _update_preview(self):
        # 전역 QSS의 "QLabel { font-size: 12px; }" 규칙이 setFont()로 지정한
        # 크기를 다음 폴리시 시점에 다시 덮어써버리므로(스타일시트가 위젯 자체
        # 폰트보다 우선), 위젯 각각에 직접 font-size를 박아 넣는 방식으로 우회한다.
        filename_px = self.filename_px_spin.value()
        content_px = self.content_px_spin.value()
        lines = self.snippet_lines_spin.value()

        self.preview_filename_label.setStyleSheet(f"color: #b0b3b8; font-size: {filename_px}px;")
        self.preview_filename_label.setText("📊 견적서_최종_v2.xlsx")

        self.preview_content_label.setStyleSheet(f"color: #d4d6d9; font-size: {content_px}px;")
        sample_line = "검색된 내용 미리보기 텍스트입니다. "
        self.preview_content_label.setText((sample_line * 6).strip())

        content_font = QFont()
        content_font.setPixelSize(content_px)
        line_h = QFontMetrics(content_font).lineSpacing()
        self.preview_content_label.setFixedHeight(line_h * lines)

    def _add_folder_row(self, key: str, display_name: str = "", filename_only: bool = False,
                         is_group: bool = False, member_count: int = 0, row: int = None,
                         real_path: str = None):
        """real_path: 같은 폴더를 다른 검색 방식으로 한 번 더 추가한 항목(멤버 1개짜리
        그룹으로 내부 저장됨)일 때만 넘긴다 — 그룹 UI("🔗 그룹 · N개 폴더") 대신 실제
        폴더 이름을 그대로 보여줘서, 진짜 여러 폴더를 묶은 그룹과 헷갈리지 않게 한다.
        번호(0번 칸)·비고(4번 칸)는 여기서 자리만 잡아두고, 실제 값은 호출부에서
        _renumber_rows()/_recompute_duplicate_notes()가 한 번에 다시 계산해서 채운다."""
        if row is None:
            row = self.folder_table.rowCount()
        self.folder_table.insertRow(row)

        number_item = QTableWidgetItem("")
        number_item.setFlags(Qt.ItemIsEnabled)
        number_item.setTextAlignment(Qt.AlignCenter)
        self.folder_table.setItem(row, 0, number_item)

        if real_path is not None:
            path_text = real_path
        elif is_group:
            path_text = self._group_row_text(key, member_count)
        else:
            path_text = key
        item = QTableWidgetItem(path_text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setData(ROLE_KEY, key)  # 실제 식별자(경로 또는 그룹 키)는 따로 저장
        item.setData(ROLE_KIND, "primary")
        if real_path is not None:
            item.setToolTip(f"{real_path}\n(같은 폴더를 다른 검색 방식으로 한 번 더 추가한 항목)")
        elif is_group:
            item.setToolTip("클릭하면 펼쳐져서 멤버 폴더별 표시 이름을 편집할 수 있습니다.")
        self.folder_table.setItem(row, 1, item)

        name_edit = QLineEdit()
        name_edit.setObjectName("cellLineEdit")
        if real_path is not None:
            placeholder = os.path.basename(real_path.rstrip("\\/")) or real_path
        elif is_group:
            placeholder = "그룹 이름"
        else:
            placeholder = os.path.basename(key.rstrip("\\/")) or key
        name_edit.setPlaceholderText(placeholder)
        name_edit.setText(display_name)
        self.folder_table.setCellWidget(row, 2, name_edit)

        combo = NoScrollComboBox()
        combo.setObjectName("cellCombo")
        combo.addItems(SEARCH_MODE_OPTIONS)
        combo.setCurrentIndex(1 if filename_only else 0)
        # 검색 방식을 바꾸면 "중복" 여부(경로+방식이 둘 다 같아야 중복)도 그 자리에서
        # 다시 계산해야 한다 — 안 그러면 방식을 바꿔서 더는 중복이 아니게 됐는데도
        # 비고 칸이 그대로 남는다.
        combo.currentIndexChanged.connect(lambda _: self._recompute_duplicate_notes())
        self.folder_table.setCellWidget(row, 3, combo)

        note_item = QTableWidgetItem("")
        note_item.setFlags(Qt.ItemIsEnabled)
        note_item.setForeground(QColor("#9aa0a6"))
        self.folder_table.setItem(row, 4, note_item)

        self._renumber_rows()
        self._recompute_duplicate_notes()
        return row

    def _group_row_text(self, group_key: str, member_count: int) -> str:
        arrow = "▾" if group_key in self._expanded_groups else "▸"
        return f"{arrow} 🔗 그룹 · {member_count}개 폴더"

    def _row_key(self, row: int) -> str:
        return self.folder_table.item(row, 1).data(ROLE_KEY)

    def _row_kind(self, row: int) -> str:
        return self.folder_table.item(row, 1).data(ROLE_KIND)

    def _row_real_path(self, row: int) -> Optional[str]:
        """이 행이 가리키는 "실제" 폴더 경로 하나. 멤버 여러 개짜리 진짜 그룹은
        경로 하나로 대표할 수 없어 None(중복 검사 대상에서 제외)."""
        key = self._row_key(row)
        members = self._group_members.get(key)
        if members is None:
            return key
        if len(members) == 1:
            return members[0]
        return None

    def _renumber_rows(self):
        """primary 행에만 1부터 순서대로 번호를 매긴다(멤버 서브행은 비워둠) —
        표시되는 순서 그대로라 위/아래로 옮기면 번호도 자연히 다시 매겨진다."""
        n = 0
        for r in range(self.folder_table.rowCount()):
            item = self.folder_table.item(r, 0)
            if self._row_kind(r) == "primary":
                n += 1
                item.setText(str(n))
            else:
                item.setText("")

    def _recompute_duplicate_notes(self):
        """실제 경로가 같은 primary 행이 둘 이상이면, 서로의 번호를 "비고" 칸에
        "N번과 중복"으로 표시한다. 진짜(멤버 2개 이상) 그룹은 경로 하나로 대표할
        수 없어서 검사 대상에서 뺀다. 검색 방식(내용/파일명)까지 같아야 "중복"으로
        본다 — 같은 폴더라도 방식이 다르면 각자 다른 검색 결과를 내므로 중복이
        아니다."""
        rows_by_key: Dict[tuple, List[int]] = {}
        for r in range(self.folder_table.rowCount()):
            if self._row_kind(r) != "primary":
                continue
            path = self._row_real_path(r)
            if path is None:
                continue
            filename_only = self.folder_table.cellWidget(r, 3).currentIndex() == 1
            rows_by_key.setdefault((path, filename_only), []).append(r)

        for r in range(self.folder_table.rowCount()):
            if self._row_kind(r) != "primary":
                continue
            note_item = self.folder_table.item(r, 4)
            path = self._row_real_path(r)
            filename_only = self.folder_table.cellWidget(r, 3).currentIndex() == 1
            rows = rows_by_key.get((path, filename_only)) if path is not None else None
            if rows and len(rows) > 1:
                others = [self.folder_table.item(other, 0).text() for other in rows if other != r]
                note_item.setText(", ".join(f"{n}번" for n in others) + "과 중복")
            else:
                note_item.setText("")

    def _existing_folders(self):
        """primary 행(실제 폴더/그룹)의 키만. 펼쳐진 그룹의 멤버 서브행은 뺀다."""
        return [self._row_key(r) for r in range(self.folder_table.rowCount())
                if self._row_kind(r) == "primary"]

    def _all_registered_paths(self) -> set:
        """지금 표에 등록된 모든 실제 폴더 경로(일반 폴더 + 모든 그룹의 멤버 폴더 전부).
        그룹 안에 숨어있는 멤버는 최상위 행이 아니라서 _existing_folders() 만으론 못
        잡는다 — 이걸 빼먹으면 그룹 안에 있는 폴더를 다시 골랐을 때 "이미 등록됨"을
        감지 못 하고 조용히 또 다른 최상위 행으로 추가돼버린다."""
        paths = set()
        for key in self._existing_folders():
            members = self._group_members.get(key)
            if members:
                paths.update(members)
            else:
                paths.add(key)
        return paths

    # ---------- 그룹 펼치기/접기 (멤버 표시 이름 인라인 편집) ----------
    def _on_cell_clicked(self, row: int, col: int):
        if col != 1:
            return
        item = self.folder_table.item(row, 1)
        if item is None or item.data(ROLE_KIND) != "primary":
            return
        key = item.data(ROLE_KEY)
        members = self._group_members.get(key)
        if not members or len(members) < 2:
            return  # 멤버 1개짜리("같은 폴더를 다른 방식으로 추가")는 펼칠 게 없다
        if key in self._expanded_groups:
            self._collapse_group(key)
        else:
            self._expand_group(row, key)

    def _expand_group(self, row: int, group_key: str):
        self._expanded_groups.add(group_key)
        self.folder_table.item(row, 1).setText(
            self._group_row_text(group_key, len(self._group_members[group_key]))
        )
        for i, member in enumerate(self._group_members[group_key]):
            self._insert_member_subrow(row + 1 + i, group_key, member)
        self._renumber_rows()
        self._recompute_duplicate_notes()

    def _collapse_group(self, group_key: str):
        self._expanded_groups.discard(group_key)
        group_row = None
        sub_rows = []
        for r in range(self.folder_table.rowCount()):
            item = self.folder_table.item(r, 1)
            if item.data(ROLE_KIND) == "primary" and item.data(ROLE_KEY) == group_key:
                group_row = r
            elif item.data(ROLE_KIND) == "member" and item.data(ROLE_PARENT) == group_key:
                sub_rows.append(r)

        # 지우기 전에 서브행에서 편집 중이던 표시 이름을 저장해 둔다 — 이게 이제
        # 유일한 "진짜" 값이다(따로 동기화해야 하는 그림자 값이 아니라, 접혀서
        # 안 보이는 동안만 대신 들고 있는 것뿐).
        for r in sub_rows:
            item = self.folder_table.item(r, 1)
            member = item.data(ROLE_KEY)
            val = self.folder_table.cellWidget(r, 2).text().strip()
            if val:
                self._member_display_names[member] = val
            else:
                self._member_display_names.pop(member, None)
        for r in reversed(sub_rows):
            self.folder_table.removeRow(r)

        if group_row is not None:
            self.folder_table.item(group_row, 1).setText(
                self._group_row_text(group_key, len(self._group_members.get(group_key, [])))
            )
        self._renumber_rows()
        self._recompute_duplicate_notes()

    def _insert_member_subrow(self, row: int, group_key: str, member: str):
        self.folder_table.insertRow(row)

        number_item = QTableWidgetItem("")
        number_item.setFlags(Qt.ItemIsEnabled)
        self.folder_table.setItem(row, 0, number_item)

        item = QTableWidgetItem("       └ " + member)
        item.setFlags(Qt.ItemIsEnabled)  # 선택/삭제 대상 아님, 그냥 보여주기용
        item.setData(ROLE_KEY, member)
        item.setData(ROLE_KIND, "member")
        item.setData(ROLE_PARENT, group_key)
        item.setToolTip(member)
        self.folder_table.setItem(row, 1, item)

        name_edit = QLineEdit()
        name_edit.setObjectName("cellLineEdit")
        name_edit.setPlaceholderText(os.path.basename(member.rstrip("\\/")) or member)
        # 이번 세션에 편집한 값이 있으면 그걸, 없으면 마지막으로 저장된 값을 보여준다.
        initial = self._member_display_names.get(member)
        if initial is None:
            initial = self.settings.folder_display_name.get(member, "")
        name_edit.setText(initial)
        self.folder_table.setCellWidget(row, 2, name_edit)

        note = QLabel("그룹과 동일")
        note.setStyleSheet("color: #6b7075; font-size: 11px; padding-left: 4px;")
        self.folder_table.setCellWidget(row, 3, note)

        note_item = QTableWidgetItem("")
        note_item.setFlags(Qt.ItemIsEnabled)
        self.folder_table.setItem(row, 4, note_item)

    def _add_folder(self):
        path = QFileDialog.getExistingDirectory(self, "검색할 폴더 선택")
        if not path:
            return
        if path not in self._all_registered_paths():
            # 새로 추가하는 폴더는 기본적으로 파일명만 검색(내용은 안 읽음) —
            # 내용 검색이 필요한 폴더만 표에서 직접 바꿔주면 된다.
            self._add_folder_row(path, filename_only=True)
            return

        # 이미 등록된 폴더(그룹 안에 숨어있는 경우 포함) — 다른 검색 방식으로 한 번
        # 더 추가할 수 있게, 확인 없이 바로 하나 더 등록한다. 멤버 1개짜리 그룹으로
        # 내부 저장하는 건 그대로 두되(같은 실제 경로를 두 항목이 가리키면서도 각자
        # 다른 검색 방식 설정을 가지려면 서로 다른 식별자가 필요하다 — folder_enabled/
        # folder_filename_only가 경로 문자열을 키로 쓰기 때문), _add_folder_row에
        # real_path를 넘겨서 UI에는 "그룹"이 아니라 평범한 폴더 이름으로만 보이게
        # 한다(펼치기 화살표도 없음 — 실제로 진짜 그룹과 헷갈릴 일이 없다). 검색/색인
        # 단계에서 같은 실제 경로 + 같은 검색 방식 조합은 자연히 같은 파일들을
        # 가리키므로 결과가 중복되지 않는다.
        dup_key = GROUP_PREFIX + uuid.uuid4().hex[:12]
        self._group_members[dup_key] = [path]
        self._add_folder_row(dup_key, filename_only=True, real_path=path)

    def _remove_folder(self):
        # 선택 대상은 selectedIndexes()가 항상 primary 행만 준다(member 서브행은
        # 선택 불가로 만들어 뒀다). 지우는 행이 펼쳐진 그룹이면 그 서브행들도 같이 지운다.
        rows = sorted({idx.row() for idx in self.folder_table.selectedIndexes()}, reverse=True)
        for r in rows:
            key = self._row_key(r)
            if key in self._expanded_groups:
                self._collapse_group(key)  # 서브행부터 정리(이름은 보관됨)
            self._group_members.pop(key, None)
            self.folder_table.removeRow(r)
        self._renumber_rows()
        self._recompute_duplicate_notes()

    def _move_folder(self, delta: int):
        # 펼쳐진 그룹이 있으면 행 순서가 primary 행만의 순서와 어긋나서 스왑이
        # 꼬일 수 있으니, 이동 전에 전부 접어 표를 "평평하게" 만든다.
        for key in list(self._expanded_groups):
            self._collapse_group(key)

        row = self.folder_table.currentRow()
        if row < 0 or self._row_kind(row) != "primary":
            return
        new_row = row + delta
        if new_row < 0 or new_row >= self.folder_table.rowCount():
            return
        self._swap_rows(row, new_row)
        self.folder_table.setCurrentCell(new_row, 0)

    def _swap_rows(self, r1: int, r2: int):
        """행을 실제로 옮기는 대신, 두 행의 값을 맞바꾼다(위젯을 다시 만들 필요 없음).
        번호(0번 칸)는 내용이 아니라 위치를 나타내므로 안 바꾸고 그대로 둔다."""
        item1, item2 = self.folder_table.item(r1, 1), self.folder_table.item(r2, 1)
        text1, text2 = item1.text(), item2.text()
        key1, key2 = item1.data(ROLE_KEY), item2.data(ROLE_KEY)
        tip1, tip2 = item1.toolTip(), item2.toolTip()
        item1.setText(text2)
        item1.setData(ROLE_KEY, key2)
        item1.setToolTip(tip2)
        item2.setText(text1)
        item2.setData(ROLE_KEY, key1)
        item2.setToolTip(tip1)

        name1, name2 = self.folder_table.cellWidget(r1, 2), self.folder_table.cellWidget(r2, 2)
        name1_text, name2_text = name1.text(), name2.text()
        name1.setText(name2_text)
        name2.setText(name1_text)
        placeholder1 = "그룹 이름" if key2 in self._group_members else (os.path.basename(key2.rstrip("\\/")) or key2)
        placeholder2 = "그룹 이름" if key1 in self._group_members else (os.path.basename(key1.rstrip("\\/")) or key1)
        name1.setPlaceholderText(placeholder1)
        name2.setPlaceholderText(placeholder2)

        combo1, combo2 = self.folder_table.cellWidget(r1, 3), self.folder_table.cellWidget(r2, 3)
        idx1, idx2 = combo1.currentIndex(), combo2.currentIndex()
        combo1.setCurrentIndex(idx2)
        combo2.setCurrentIndex(idx1)
        self._recompute_duplicate_notes()

    def _group_selected(self):
        rows = sorted({idx.row() for idx in self.folder_table.selectedIndexes()})
        if len(rows) < 2:
            QMessageBox.information(self, "알림", "그룹으로 묶을 폴더를 2개 이상 선택하세요.")
            return
        keys = [self._row_key(r) for r in rows]
        if any(k in self._group_members for k in keys):
            QMessageBox.warning(self, "알림", "이미 그룹인 항목이 포함되어 있습니다. 먼저 그룹을 해제한 뒤 다시 묶어주세요.")
            return

        name, ok = QInputDialog.getText(self, "그룹 이름", "묶은 폴더들을 부를 이름을 입력하세요:")
        if not ok or not name.strip():
            return

        # 그룹으로 묶이면서 표에서 안 보이게 되는 개별 폴더들의 표시 이름을 보관해
        # 둔다 — 그룹 안 결과의 소제목에 쓰고, 그룹을 해제하면 그대로 되돌려준다.
        for r in rows:
            key = self._row_key(r)
            custom = self.folder_table.cellWidget(r, 2).text().strip()
            if custom:
                self._member_display_names[key] = custom
            else:
                self._member_display_names.pop(key, None)

        group_key = GROUP_PREFIX + uuid.uuid4().hex[:12]
        self._group_members[group_key] = keys

        for r in reversed(rows):
            self.folder_table.removeRow(r)

        self._add_folder_row(group_key, display_name=name.strip(), filename_only=True,
                              is_group=True, member_count=len(keys))

    def _ungroup_selected(self):
        row = self.folder_table.currentRow()
        if row < 0 or self._row_kind(row) != "primary":
            return
        key = self._row_key(row)
        members = self._group_members.get(key)
        if not members:
            QMessageBox.information(self, "알림", "선택한 항목은 그룹이 아닙니다.")
            return

        filename_only = self.folder_table.cellWidget(row, 3).currentIndex() == 1
        if key in self._expanded_groups:
            # 서브행부터 정리한다(편집 중이던 이름은 보관됨). 서브행은 그룹 행
            # "다음"에만 있으므로 그룹 행 자신의 인덱스(row)는 바뀌지 않는다.
            self._collapse_group(key)
        self.folder_table.removeRow(row)
        del self._group_members[key]
        existing = self._existing_folders()
        for m in members:
            if m not in existing:
                self._add_folder_row(m, display_name=self._member_display_names.get(m, ""),
                                      filename_only=filename_only)
                existing.append(m)
        self._renumber_rows()
        self._recompute_duplicate_notes()

    def _on_save(self):
        hotkey = self.hotkey_edit.value.strip()
        if not hotkey:
            QMessageBox.warning(self, "알림", "전역 단축키를 입력해주세요.")
            return

        self.settings.always_on_top = self.always_on_top_cb.isChecked()
        self.settings.keep_visible = self.keep_visible_cb.isChecked()
        self.settings.opacity = self.opacity_slider.value() / 100.0
        self.settings.hotkey = hotkey
        self.settings.width_percent = self.width_slider.value() / 100.0
        self.settings.filename_font_px = self.filename_px_spin.value()
        self.settings.content_font_px = self.content_px_spin.value()
        self.settings.snippet_max_lines = self.snippet_lines_spin.value()
        self.settings.search_display_limit = self.display_limit_spin.value()

        folders = []
        folder_filename_only = {}
        folder_display_name = {}
        live_member_names = {}  # 지금 펼쳐져서 위젯에서 바로 읽은 값(가장 최신)
        for r in range(self.folder_table.rowCount()):
            item = self.folder_table.item(r, 1)
            key = item.data(ROLE_KEY)
            name_edit = self.folder_table.cellWidget(r, 2)
            if item.data(ROLE_KIND) == "member":
                live_member_names[key] = name_edit.text().strip()
                continue
            combo = self.folder_table.cellWidget(r, 3)
            folders.append(key)
            folder_filename_only[key] = (combo.currentIndex() == 1)
            custom_name = name_edit.text().strip()
            if custom_name:
                folder_display_name[key] = custom_name

        folder_groups = {k: v for k, v in self._group_members.items() if k in folders}
        # 그룹 안에 숨어서 표에는 안 보이는(또는 펼쳐져서 서브행으로 보이는) 멤버
        # 폴더들의 표시 이름도 같이 저장한다(그룹 결과의 소제목에 쓰인다).
        # 지금 펼쳐진 그룹은 서브행에서 직접 읽은 값이 가장 최신이라 그걸 쓰고,
        # 접혀 있는 그룹은 마지막으로 기억해 둔 값을 쓴다.
        for members in folder_groups.values():
            for m in members:
                if m in live_member_names:
                    if live_member_names[m]:
                        folder_display_name[m] = live_member_names[m]
                elif m in self._member_display_names:
                    folder_display_name[m] = self._member_display_names[m]

        self.settings.folders = folders
        self.settings.folder_filename_only = folder_filename_only
        self.settings.folder_display_name = folder_display_name
        self.settings.folder_groups = folder_groups
        # 실제 저장은 done()에서 창 크기와 함께 한 번에 한다(accept()가 done()을 부른다).

        if self.startup_cb.isEnabled():
            try:
                from startup import set_startup
                set_startup(self.startup_cb.isChecked())
            except Exception:
                pass

        self.saved.emit()
        self.accept()
