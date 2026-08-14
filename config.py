"""설정 로드/저장 (%APPDATA%\\OFFFIND\\settings.json)"""
import json
import os
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "OFFFIND")
_OLD_APP_DIRS = [
    os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "KS-Finder"),
    os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "FileSearcher"),
]
if not os.path.exists(APP_DIR):
    # 프로그램 이름이 FileSearcher -> KS-Finder -> OFFFIND로 바뀌면서 저장 폴더명도
    # 맞췄는데, 그냥 두면 예전 이름 폴더에 있던 설정/색인 캐시를 못 찾아서 전부 새로
    # 설정해야 하는 것처럼 보인다 — 새 폴더가 없고 예전 폴더가 있으면 그대로 옮겨준다.
    for _old_dir in _OLD_APP_DIRS:
        if os.path.exists(_old_dir):
            try:
                os.rename(_old_dir, APP_DIR)
            except OSError:
                pass
            break
SETTINGS_PATH = os.path.join(APP_DIR, "settings.json")
CACHE_PATH = os.path.join(APP_DIR, "index_cache.json")

DEFAULTS = {
    # 폴더 목록 관련 항목은 사용자마다 다르므로 비워둔 채로 유지한다(첫 실행 시
    # 옵션 창이 자동으로 열려 직접 추가하게 됨). 그 외 나머지는 실제 사용하며
    # 맞춰온 값을 새 기본값으로 삼는다.
    "folders": [],
    "hotkey": "ctrl+space",
    "always_on_top": True,
    "opacity": 0.97,
    "width_percent": 0.4,
    "max_height_percent": 0.7,
    "start_with_windows": False,
    "pos_x": 2462,
    "pos_bottom_y": 1030,
    "folder_enabled": {},
    "folder_filename_only": {},
    "folder_display_name": {},
    "options_width": 909,
    "options_height": 739,
    "folder_column_widths": [],
    "folder_groups": {},
    "filename_font_px": 11,
    "content_font_px": 12,
    "snippet_max_lines": 3,
}


@dataclass
class Settings:
    folders: List[str] = field(default_factory=list)
    hotkey: str = DEFAULTS["hotkey"]
    always_on_top: bool = DEFAULTS["always_on_top"]
    opacity: float = DEFAULTS["opacity"]
    width_percent: float = DEFAULTS["width_percent"]
    max_height_percent: float = DEFAULTS["max_height_percent"]
    start_with_windows: bool = DEFAULTS["start_with_windows"]
    pos_x: Optional[int] = None
    pos_bottom_y: Optional[int] = None
    folder_enabled: Dict[str, bool] = field(default_factory=dict)
    folder_filename_only: Dict[str, bool] = field(default_factory=dict)
    folder_display_name: Dict[str, str] = field(default_factory=dict)
    options_width: Optional[int] = None
    options_height: Optional[int] = None
    folder_column_widths: List[int] = field(default_factory=list)
    folder_groups: Dict[str, List[str]] = field(default_factory=dict)  # 그룹키 -> 실제 폴더 경로 목록
    filename_font_px: int = DEFAULTS["filename_font_px"]
    content_font_px: int = DEFAULTS["content_font_px"]
    snippet_max_lines: int = DEFAULTS["snippet_max_lines"]

    def __post_init__(self):
        # 실제 설정 파일 경로. dataclass 필드가 아니라서 JSON에는 저장되지 않는다.
        # 일부러 기본값을 SETTINGS_PATH로 두지 않는다 — Settings()를 맨손으로 만들면
        # (테스트 스크립트 등) save()가 예외를 던지도록 해서, 실제 사용자 설정 파일을
        # 실수로 덮어쓰는 사고를 코드 차원에서 막는다. 오직 Settings.load()만 이
        # 경로를 채운다. (이 안전장치를 추가하기 전, 테스트 스크립트가 Settings()를
        # 새로 만들어 쓰다가 실제 설정을 두 번이나 덮어쓴 적이 있다.)
        self._path = None

    @classmethod
    def load(cls, path: Optional[str] = None) -> "Settings":
        target = path or SETTINGS_PATH
        os.makedirs(os.path.dirname(target), exist_ok=True)
        is_new = not os.path.exists(target)
        if is_new:
            data = {}
        else:
            try:
                with open(target, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                data = {}
        # DEFAULTS 안의 리스트/딕셔너리는 모듈 전체에서 공유되는 객체라서, 그대로
        # 인스턴스에 넘기면(특히 파일이 없어 데이터 없이 DEFAULTS만 쓸 때) 나중에
        # settings.folder_enabled[x] = ... 처럼 제자리에서 고치는 코드가 DEFAULTS
        # 자체를 오염시킬 수 있다 — 항상 새 리스트/딕셔너리로 복사해서 넣는다.
        merged = {**DEFAULTS, **data}
        merged["folder_enabled"] = dict(merged.get("folder_enabled") or {})
        merged["folder_filename_only"] = dict(merged.get("folder_filename_only") or {})
        merged["folder_display_name"] = dict(merged.get("folder_display_name") or {})
        merged["folder_column_widths"] = list(merged.get("folder_column_widths") or [])
        merged["folder_groups"] = {k: list(v) for k, v in (merged.get("folder_groups") or {}).items()}
        merged["folders"] = list(merged.get("folders") or [])
        s = cls(**{k: merged[k] for k in DEFAULTS})
        s._path = target
        if is_new:
            s.save()
        return s

    def save(self) -> None:
        target = getattr(self, "_path", None)
        if target is None:
            raise RuntimeError(
                "Settings.save(): 저장 경로가 없습니다. Settings.load()로 만든 "
                "객체가 아니면 저장할 수 없습니다(실제 설정 파일을 실수로 "
                "덮어쓰지 않도록 하는 안전장치). 테스트라면 "
                "Settings.load(path='temp.json')처럼 경로를 명시하세요."
            )
        os.makedirs(os.path.dirname(target), exist_ok=True)
        # 쓰다가 중간에 죽어도(강제 종료 등) 기존 파일이 깨지지 않도록 임시파일에
        # 먼저 쓰고 원자적으로 교체한다. 교체 직전에는 이전 내용을 .bak 으로 남겨서,
        # 실수로 엉뚱한 값이 저장돼도 한 단계는 되돌릴 수 있게 한다.
        tmp_path = target + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2)
        if os.path.exists(target):
            try:
                import shutil
                shutil.copyfile(target, target + ".bak")
            except OSError:
                pass
        os.replace(tmp_path, target)
