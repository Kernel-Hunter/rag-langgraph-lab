"""Swap a block without touching the rest (slide 8).

Every other module asks this file for a chat model and an embedding model.
Change LLM_PROVIDER / EMBEDDINGS_PROVIDER in .env and nothing else moves.
"""

import os

from dotenv import load_dotenv

load_dotenv()

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
