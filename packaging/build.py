"""Build the desktop app for the current OS with PyInstaller.

    pip install -e . pyinstaller pillow
    python packaging/build.py

Output: dist/RAG-Help-Desk/ (Windows, Linux) or dist/RAG-Help-Desk.app (macOS).
The release workflow then wraps it in a setup.exe, a .zip/.dmg or a .tar.gz.
"""

import os
import sys
import tempfile
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).resolve().parents[1]
NAME = "RAG-Help-Desk"
SEP = os.pathsep

# Ship the local embedding model so the first launch needs no download.
os.environ["RAG_LAB_HOME"] = MODEL_HOME = tempfile.mkdtemp(prefix="rag-lab-build-")
sys.path.insert(0, str(ROOT / "src"))
from rag_lab.providers import LocalEmbeddings  # noqa: E402

LocalEmbeddings().embed_query("warm up")
MODELS = Path(MODEL_HOME) / "models"

# Imported lazily or loaded by name, so PyInstaller cannot see them on its own.
SUBMODULES = ["langchain_openai", "langchain_anthropic", "tiktoken_ext", "uvicorn", "webview"]
COLLECT_ALL = ["fastembed"]
METADATA = ["langchain", "langchain-core", "langchain-openai", "langchain-anthropic", "anthropic",
            "langchain-text-splitters", "langgraph", "langsmith", "openai", "tiktoken", "fastembed",
            "fastapi", "uvicorn", "pywebview", "huggingface-hub", "tokenizers", "onnxruntime"]
EXCLUDE = ["torch", "sentence_transformers", "langchain_huggingface", "langchain_chroma", "chromadb",
           "pytest", "matplotlib", "IPython", "tkinter", "customtkinter"]

args = [
    str(ROOT / "packaging" / "entry.py"),
    "--name", NAME,
    "--windowed",
    "--noconfirm",
    "--clean",
    "--icon", str(ROOT / "packaging" / "icon.png"),
    "--paths", str(ROOT / "src"),
    "--add-data", f"{ROOT / 'src' / 'rag_lab' / 'web'}{SEP}rag_lab/web",
    "--add-data", f"{ROOT / 'data' / 'handbook.md'}{SEP}data",
    "--add-data", f"{MODELS}{SEP}models",
    "--distpath", str(ROOT / "dist"),
    "--workpath", str(ROOT / "build"),
    "--specpath", str(ROOT / "build"),
]
for module in SUBMODULES:
    args += ["--collect-submodules", module]
for package in COLLECT_ALL:
    args += ["--collect-all", package]
for package in METADATA:
    args += ["--copy-metadata", package]
for module in EXCLUDE:
    args += ["--exclude-module", module]
if sys.platform == "darwin":
    args += ["--osx-bundle-identifier", "io.github.kernel-hunter.rag-help-desk"]

PyInstaller.__main__.run(args)
