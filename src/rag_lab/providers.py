"""Swap a block without touching the rest (slide 8).

Every other module asks this file for a chat model and an embedding model.
Change LLM_PROVIDER / EMBEDDINGS_PROVIDER in .env and nothing else moves.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Per-user settings file. The installed desktop app has no project folder, so it keeps
# keys and choices here. Project .env (loaded first) wins over it.
USER_ENV = Path.home() / ".rag-lab" / ".env"

load_dotenv()
load_dotenv(USER_ENV)


def save_user_settings(values: dict[str, str]) -> Path:
    """Merge values into ~/.rag-lab/.env (readable only by the current user)."""
    USER_ENV.parent.mkdir(parents=True, exist_ok=True)
    current: dict[str, str] = {}
    if USER_ENV.exists():
        for line in USER_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                current[key.strip()] = value.strip()
    current.update({k: v for k, v in values.items() if v})
    USER_ENV.write_text("".join(f"{k}={v}\n" for k, v in current.items()), encoding="utf-8")
    try:
        USER_ENV.chmod(0o600)
    except OSError:  # Windows ignores POSIX modes
        pass
    return USER_ENV

LLM_DEFAULTS = {
    "openai": "gpt-4.1-nano",
    "deepseek": "deepseek-chat",
    "ollama": "gemma3",
}

EMBEDDING_DEFAULTS = {
    "openai": "text-embedding-3-small",
    "ollama": "nomic-embed-text",
    "huggingface": "sentence-transformers/all-MiniLM-L6-v2",
    "fake": "deterministic-256",
}


def get_llm(provider: str | None = None, model: str | None = None):
    provider = (provider or os.getenv("LLM_PROVIDER", "openai")).lower()
    if provider not in LLM_DEFAULTS:
        raise ValueError(f"Unknown LLM_PROVIDER {provider!r}. Use one of {sorted(LLM_DEFAULTS)}.")
    model = model or os.getenv("LLM_MODEL") or LLM_DEFAULTS[provider]

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=model, temperature=0)
    if provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(model=model, temperature=0)
    from langchain_ollama import ChatOllama  # pip install -e ".[ollama]"

    return ChatOllama(model=model, temperature=0)


def get_embeddings(provider: str | None = None, model: str | None = None):
    provider = (provider or os.getenv("EMBEDDINGS_PROVIDER", "openai")).lower()
    if provider not in EMBEDDING_DEFAULTS:
        raise ValueError(
            f"Unknown EMBEDDINGS_PROVIDER {provider!r}. Use one of {sorted(EMBEDDING_DEFAULTS)}."
        )
    model = model or os.getenv("EMBEDDINGS_MODEL") or EMBEDDING_DEFAULTS[provider]

    if provider == "openai":
        from langchain_openai import OpenAIEmbeddings

        return OpenAIEmbeddings(model=model)
    if provider == "ollama":
        from langchain_ollama import OllamaEmbeddings  # pip install -e ".[ollama]"

        return OllamaEmbeddings(model=model)
    if provider == "huggingface":
        from langchain_huggingface import HuggingFaceEmbeddings  # pip install -e ".[local]"

        return HuggingFaceEmbeddings(model_name=model)
    from langchain_core.embeddings import DeterministicFakeEmbedding

    return DeterministicFakeEmbedding(size=256)
