# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], ["musicbrainzngs", "discid"]
for pkg in ("webview", "clr_loader", "pythonnet"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# CD 읽기용 libdiscid (워크플로가 vendor/에 내려받음)
if os.path.exists("vendor/discid.dll"):
    binaries.append(("vendor/discid.dll", "."))

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CDShelf",
    console=False,
    icon="assets/cdshelf.ico",
)
coll = COLLECT(exe, a.binaries, a.datas, name="CDShelf")
