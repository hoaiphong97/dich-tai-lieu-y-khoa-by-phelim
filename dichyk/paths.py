"""Vị trí tài nguyên đi kèm app và thư mục dữ liệu của người dùng.

Khi cài bằng bộ cài, thư mục app là chỉ-đọc, nên mọi thứ app ghi ra (cài đặt,
cache, job, thuật ngữ người dùng thêm) nằm trong thư mục dữ liệu riêng:
- Windows: %APPDATA%\\DichYKhoa
- macOS:   ~/Library/Application Support/DichYKhoa
- Linux:   ~/.local/share/dichykhoa
Có thể đổi bằng biến môi trường DICHYK_DATA (dùng khi chạy test).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "DichYKhoa"
APP_TITLE = "Dịch Tài Liệu Y Khoa"
APP_VERSION = "0.2.4"


def resource_root() -> Path:
    """Thư mục chứa dichyk/web và kit/ (trong gói PyInstaller hoặc trong repo)."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def web_dir() -> Path:
    return resource_root() / "dichyk" / "web"


def kit_dir() -> Path:
    return resource_root() / "kit"


def data_dir() -> Path:
    override = os.environ.get("DICHYK_DATA")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / APP_NAME
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / APP_NAME
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / APP_NAME.lower()
    base.mkdir(parents=True, exist_ok=True)
    return base


def sub(name: str) -> Path:
    path = data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    docs = Path.home() / "Documents"
    return (docs if docs.is_dir() else Path.home()) / APP_NAME
