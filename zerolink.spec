# PyInstaller build spec for ZeroLink (Windows, one-folder, no console window)
block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    datas=[("assets", "assets")],
    hiddenimports=["PySide6.QtSvg"],
    excludes=["tkinter", "unittest", "pydoc", "PySide6.QtQml", "PySide6.QtQuick",
              "PySide6.QtPdf", "PySide6.QtSvgWidgets", "PySide6.QtOpenGL"],
    noarchive=False,
)
# Never ship ICU: Windows 10 1903+ provides it (a build machine may only have a stub)
a.binaries = [b for b in a.binaries if not b[0].lower().split("\\")[-1].split("/")[-1].startswith(("icuuc", "icuin", "icu."))]
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ZeroLink",
    console=False,
    icon="assets/icon.ico",
    version="version_info.txt",
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ZeroLink")
