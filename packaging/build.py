"""Build the desktop app for the current OS with PyInstaller.

    pip install -e ".[ollama]" pyinstaller
    python packaging/build.py

Output: dist/RAG-Help-Desk/ (Windows, Linux) or dist/RAG-Help-Desk.app (macOS).
The release workflow then wraps it in a setup.exe, a .dmg or a .tar.gz.
"""

import os
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
NAME = "RAG-Help-Desk"

# Imported lazily inside providers.py, so PyInstaller cannot see them on its own.
SUBMODULES = ["langchain_openai", "langchain_deepseek", "langchain_ollama", "tiktoken_ext"]
# Packages that read their own version through importlib.metadata at import time.
METADATA = ["langchain", "langchain-core", "langchain-openai", "langchain-deepseek",
            "langchain-ollama", "langchain-text-splitters", "langgraph", "langsmith", "openai", "tiktoken"]
# Heavy optional extras the app does not ship.
EXCLUDE = ["torch", "sentence_transformers", "langchain_huggingface", "langchain_chroma", "chromadb",
           "pytest", "matplotlib", "IPython"]

args = [
    str(ROOT / "packaging" / "entry.py"),
    "--name", NAME,
    "--windowed",
    "--noconfirm",
    "--clean",
    "--paths", str(ROOT / "src"),
    "--collect-data", "customtkinter",
    "--add-data", f"{ROOT / 'data' / 'handbook.md'}{os.pathsep}data",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
]
for module in SUBMODULES:
    args += ["--collect-submodules", module]
for package in METADATA:
    args += ["--copy-metadata", package]
for module in EXCLUDE:
    args += ["--exclude-module", module]
if sys.platform == "darwin":
    args += ["--osx-bundle-identifier", "io.github.kernel-hunter.rag-help-desk"]

PyInstaller.__main__.run(args)
