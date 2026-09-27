# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller packaging/dichyk.spec --noconfirm
# Kết quả: dist/DichYKhoa/ (Windows, Linux) hoặc dist/DichYKhoa.app (macOS)

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
import re
APP_VERSION = re.search(r'APP_VERSION = "(.+?)"', (ROOT / "dichyk" / "paths.py").read_text(encoding="utf-8")).group(1)
ICON_WIN = str(ROOT / "packaging" / "icon.ico")
ICON_MAC = str(ROOT / "packaging" / "icon.icns")

hidden = collect_submodules("uvicorn") + ["webview", "multipart"]
if sys.platform == "win32":
    hidden += ["clr", "webview.platforms.edgechromium", "webview.platforms.winforms"]
elif sys.platform == "darwin":
    hidden += ["webview.platforms.cocoa"]

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "dichyk" / "web"), "dichyk/web"),
        (str(ROOT / "kit"), "kit"),
    ],
    hiddenimports=hidden,
    excludes=[
        "tkinter", "matplotlib", "scipy", "pandas", "IPython", "notebook", "pytest", "sympy",
        "numpy.tests", "PIL.ImageQt", "PyQt5", "PyQt6", "PySide6",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="DichYKhoa",
    console=False,
    icon=ICON_WIN if sys.platform == "win32" and Path(ICON_WIN).exists() else None,
    upx=False,
)

coll = COLLECT(exe, a.binaries, a.datas, name="DichYKhoa", upx=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="DichYKhoa.app",
        icon=ICON_MAC if Path(ICON_MAC).exists() else None,
        bundle_identifier="vn.phelim.dichykhoa",
        version=APP_VERSION,
        info_plist={
            "CFBundleDisplayName": "Dịch Y Khoa",
            "CFBundleName": "DichYKhoa",
            "CFBundleShortVersionString": APP_VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "11.0",
        },
    )
