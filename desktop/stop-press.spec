# PyInstaller build for the desktop app. From the repo root:
#   pip install -r server/requirements.txt -r desktop/requirements.txt pyinstaller
#   pyinstaller --noconfirm desktop/stop-press.spec
# Output: dist/StopPress (Windows: StopPress.exe). The release workflow does this on each platform.
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
SERVER = os.path.join(ROOT, "server")

hidden = (
    collect_submodules("uvicorn")  # loops, protocols and lifespan are picked by name at run time
    + collect_submodules("paper")  # the server imports its pages lazily
    + ["pystray._win32", "pystray._darwin", "pystray._xorg"]
)

# Fonts the server reads at run time. The generated static/ cuts are rebuilt on first use, so leave them out.
datas = []
for root, dirs, files in os.walk(os.path.join(SERVER, "fonts")):
    dirs[:] = [d for d in dirs if d != "static"]
    dest = os.path.join("fonts", os.path.relpath(root, os.path.join(SERVER, "fonts")))
    datas += [(os.path.join(root, f), dest) for f in files]
datas += [(os.path.join(SERVER, "paper", "view.html"), "paper")]  # the browser view, read beside paper/app.py
for pkg in ("tzdata", "trafilatura", "courlan", "htmldate", "justext", "fpdf"):  # tzdata: zoneinfo has no system zones on Windows
    datas += collect_data_files(pkg)

a = Analysis(
    [os.path.join(ROOT, "desktop", "launcher.py")],
    pathex=[SERVER],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide6"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="StopPress",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed DLLs trip antivirus scanners more often
    runtime_tmpdir=None,
    console=False,  # a tray app: no console window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# On a Mac the same spec also makes an .app bundle for the zip the release workflow uploads.
if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name="StopPress.app",
        bundle_identifier="org.stop-press.app",
        info_plist={"LSUIElement": True, "NSHighResolutionCapable": True},
    )
