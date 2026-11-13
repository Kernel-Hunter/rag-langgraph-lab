"""Build the Vesper engine (the Python backend) for the current OS with PyInstaller.

    pip install -e . pyinstaller pillow
    python packaging/build_engine.py

Output: apps/desktop/engine/ holding vesper-engine (vesper-engine.exe on
Windows) and its _internal/ folder. electron-builder copies that folder into
the app as resources/engine, and the Electron shell starts it with --engine.

It is a console program on purpose: the shell reads its VESPER_READY line
from stdout. On Windows the shell starts it with windowsHide, so no console
window shows.
"""

import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
NAME = "vesper-engine"
SEP = os.pathsep
OUT = ROOT / "apps" / "desktop" / "engine"
WORK = ROOT / "build" / "engine"

# Imported lazily or loaded by name, so PyInstaller cannot see them on its own.
SUBMODULES = ["langchain_openai", "langchain_anthropic", "langchain_ollama", "ollama", "tiktoken_ext", "uvicorn"]
COLLECT_ALL: list[str] = []
METADATA = ["langchain", "langchain-core", "langchain-openai", "langchain-anthropic", "langchain-ollama",
            "ollama", "anthropic",
            "langgraph", "langsmith", "openai", "tiktoken", "fastapi", "uvicorn"]
# The Electron shell draws the window now, so the pywebview stack stays out.
EXCLUDE = ["webview", "pywebview", "objc", "AppKit", "Foundation", "WebKit", "PyObjCTools", "gi", "qtpy",
           "PyQt5", "PyQt6", "PySide2", "PySide6", "clr", "pythonnet",
           "torch", "sentence_transformers", "langchain_huggingface", "langchain_chroma", "chromadb",
           "fastembed", "onnxruntime", "numpy",
           "pytest", "matplotlib", "IPython", "tkinter", "customtkinter"]

args = [
    str(ROOT / "packaging" / "engine_entry.py"),
    "--name", NAME,
    "--onedir",
    "--console",
    "--noconfirm",
    "--clean",
    "--paths", str(ROOT / "src"),
    "--add-data", f"{ROOT / 'src' / 'vesper' / 'web'}{SEP}vesper/web",
    "--distpath", str(WORK / "dist"),
    "--workpath", str(WORK / "work"),
    "--specpath", str(WORK),
]
for module in SUBMODULES:
    args += ["--collect-submodules", module]
for package in COLLECT_ALL:
    args += ["--collect-all", package]
for package in METADATA:
    args += ["--copy-metadata", package]
for module in EXCLUDE:
    args += ["--exclude-module", module]

PyInstaller.__main__.run(args)

# apps/desktop/engine/ is the onedir folder itself: engine/vesper-engine + engine/_internal.
if OUT.exists():
    shutil.rmtree(OUT)
shutil.move(str(WORK / "dist" / NAME), str(OUT))
exe = OUT / (NAME + (".exe" if sys.platform == "win32" else ""))
if not exe.exists():
    sys.exit(f"build_engine: {exe} is missing")
print(f"Engine ready: {exe}")
