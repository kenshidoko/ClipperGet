# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import imageio_ffmpeg
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

datas = [
    ("frontend", "frontend"),
    ("assets", "assets"),
]
binaries = []
hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "pythonnet",
    "clr",
    "clr_loader",
    "bottle",
    "proxy_tools",
]

for pkg in ("faster_whisper", "ctranslate2", "onnxruntime", "webview", "certifi", "yt_dlp_ejs", "curl_cffi"):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

hiddenimports += collect_submodules("yt_dlp")
hiddenimports += collect_submodules("backend")

# Stage a single copy of ffmpeg.exe from imageio_ffmpeg into bin/ffmpeg.exe
import shutil
staging_bin = Path("build") / "ffmpeg_bin"
staging_bin.mkdir(parents=True, exist_ok=True)
ffmpeg_copy = staging_bin / "ffmpeg.exe"
shutil.copy2(imageio_ffmpeg.get_ffmpeg_exe(), ffmpeg_copy)
normalized_binaries = list(binaries)
normalized_binaries.append((str(ffmpeg_copy), "bin"))

excludes = [
    "torch",
    "torchaudio",
    "torchvision",
    "hf_xet",
    "PySide6",
    "shiboken6",
    "customtkinter",
    "tkinter",
    "scipy",
    "pandas",
    "sklearn",
    "cv2",
    "matplotlib",
    "sympy",
    "numba",
    "llvmlite",
    "Cython",
    "IPython",
    "pytest",
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=normalized_binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

def _keep_toc_entry(entry):
    dest = str(entry[0]).replace("\\", "/").lower()
    src = str(entry[1]).replace("\\", "/").lower()
    # Keep only our staged bin/ffmpeg.exe; strip duplicate imageio_ffmpeg binary and optional hf_xet/pdb bloat
    if "ffmpeg-win" in dest or "ffmpeg-win" in src or dest.startswith("imageio_ffmpeg/binaries/"):
        return False
    if dest.startswith("hf_xet/") or dest.endswith(".pdb"):
        return False
    return True

a.binaries = [item for item in a.binaries if _keep_toc_entry(item)]
a.datas = [item for item in a.datas if _keep_toc_entry(item)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ClipGetter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version="assets/version_info.txt",
    manifest="assets/app.manifest",
)

exe_dir = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ClipGetterStudio",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="assets/icon.ico",
    version="assets/version_info.txt",
    manifest="assets/app.manifest",
)

coll = COLLECT(
    exe_dir,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ClipGetterStudio",
)
