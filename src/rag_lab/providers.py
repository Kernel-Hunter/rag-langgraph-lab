"""Swap a block without touching the rest (slide 8), for many providers.

Every other module asks this file for a chat model and an embedding model.
Most providers speak the OpenAI API, so one client with a different base URL
covers them; Anthropic gets its own client.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings

# Per-user folder: settings, keys, downloaded embedding model, index cache, documents.
HOME = Path(os.getenv("RAG_LAB_HOME", Path.home() / ".rag-lab"))
USER_ENV = HOME / ".env"

load_dotenv()          # project .env wins
load_dotenv(USER_ENV)  # then the app's saved keys


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str  # openai | anthropic
    base_url: str | None
    key_env: str | None
    chat_model: str
    embed_model: str | None = None
    key_url: str = ""
    suggestions: tuple[str, ...] = field(default_factory=tuple)
    local: bool = False


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in [
        Provider("openai", "OpenAI", "openai", None, "OPENAI_API_KEY", "gpt-4.1-nano",
                 "text-embedding-3-small", "https://platform.openai.com/api-keys",
                 ("gpt-4.1-nano", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini")),
        Provider("anthropic", "Anthropic Claude", "anthropic", None, "ANTHROPIC_API_KEY", "claude-haiku-5-5",
                 None, "https://console.anthropic.com/settings/keys",
                 ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")),
        Provider("google", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai/",
                 "GEMINI_API_KEY", "gemini-2.5-flash", "gemini-embedding-001",
                 "https://aistudio.google.com/apikey", ("gemini-2.5-flash", "gemini-2.5-pro")),
        Provider("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY",
                 "deepseek-chat", None, "https://platform.deepseek.com/api_keys",
                 ("deepseek-chat", "deepseek-flash", "deepseek-v4-pro", "deepseek-reasoner")),
        Provider("mistral", "Mistral", "openai", "https://api.mistral.ai/v1", "MISTRAL_API_KEY",
                 "mistral-small-latest", "mistral-embed", "https://console.mistral.ai/api-keys",
                 ("mistral-small-latest", "mistral-medium-latest", "mistral-large-latest")),
        Provider("groq", "Groq", "openai", "https://api.groq.com/openai/v1", "GROQ_API_KEY",
                 "llama-3.3-70b-versatile", None, "https://console.groq.com/keys",
                 ("llama-3.3-70b-versatile", "llama-3.1-8b-instant")),
        Provider("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1",
                 "OPENROUTER_API_KEY", "openrouter/auto", None, "https://openrouter.ai/keys",
                 ("openrouter/auto", "deepseek/deepseek-chat", "meta-llama/llama-3.3-70b-instruct")),
        Provider("xai", "xAI Grok", "openai", "https://api.x.ai/v1", "XAI_API_KEY", "grok-3-mini",
                 None, "https://console.x.ai", ("grok-3-mini", "grok-3")),
        Provider("together", "Together AI", "openai", "https://api.together.xyz/v1", "TOGETHER_API_KEY",
                 "meta-llama/Llama-3.3-70B-Instruct-Turbo", None, "https://api.together.ai/settings/api-keys"),
        Provider("fireworks", "Fireworks AI", "openai", "https://api.fireworks.ai/inference/v1",
                 "FIREWORKS_API_KEY", "accounts/fireworks/models/llama-v3p3-70b-instruct", None,
                 "https://fireworks.ai/account/api-keys"),
        Provider("cerebras", "Cerebras", "openai", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY",
                 "llama-3.3-70b", None, "https://cloud.cerebras.ai"),
        Provider("nvidia", "NVIDIA NIM", "openai", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY",
                 "meta/llama-3.3-70b-instruct", None, "https://build.nvidia.com"),
        Provider("huggingface", "Hugging Face", "openai", "https://router.huggingface.co/v1", "HF_TOKEN",
                 "meta-llama/Llama-3.3-70B-Instruct", None, "https://huggingface.co/settings/tokens"),
        Provider("moonshot", "Moonshot Kimi", "openai", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY",
                 "kimi-k2-0905-preview", None, "https://platform.moonshot.ai"),
        Provider("ollama", "Ollama (local)", "openai", "http://localhost:11434/v1", None, "qwen2.5:7b-instruct",
                 "nomic-embed-text", "https://ollama.com/download", ("qwen2.5:7b-instruct", "llama3.2", "gemma3"),
                 local=True),
        Provider("lmstudio", "LM Studio (local)", "openai", "http://localhost:1234/v1", None, "local-model",
                 None, "https://lmstudio.ai", local=True),
        Provider("custom", "Custom (OpenAI-compatible)", "openai", "", "CUSTOM_API_KEY", "",
                 None, ""),
    ]
}

LOCAL_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
EMBEDDING_PROVIDERS = ["local"] + [p.id for p in PROVIDERS.values() if p.embed_model] + ["fake"]

# Kept for the CLI and older code.
LLM_DEFAULTS = {pid: p.chat_model for pid, p in PROVIDERS.items()}
EMBEDDING_DEFAULTS = {"local": LOCAL_EMBED_MODEL, "fake": "deterministic-256",
                      **{p.id: p.embed_model for p in PROVIDERS.values() if p.embed_model}}


def _key(provider: Provider) -> str | None:
    return os.getenv(provider.key_env) if provider.key_env else None


def _base_url(provider: Provider) -> str | None:
    if provider.id == "custom":
        return os.getenv("CUSTOM_BASE_URL") or None
    if provider.id == "ollama":
        return os.getenv("OLLAMA_BASE_URL", provider.base_url)
    return provider.base_url


def get_llm(provider: str | None = None, model: str | None = None, temperature: float = 0):
    pid = (provider or os.getenv("LLM_PROVIDER", "openai")).lower()
    if pid not in PROVIDERS:
        raise ValueError(f"Unknown LLM_PROVIDER {pid!r}. Use one of {sorted(PROVIDERS)}.")
    p = PROVIDERS[pid]
    model = model or os.getenv("LLM_MODEL") or p.chat_model
    if p.kind == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=model, temperature=temperature, api_key=_key(p))
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model,
        temperature=temperature,
        base_url=_base_url(p),
        api_key=_key(p) or "not-needed",  # local servers ignore it
    )


class LocalEmbeddings(Embeddings):
    """Free on-device embeddings (ONNX, no PyTorch). Downloads ~65 MB once."""

    def __init__(self, model: str = LOCAL_EMBED_MODEL):
        import shutil
        import sys

        from fastembed import TextEmbedding

        cache = HOME / "models"
        bundled = Path(getattr(sys, "_MEIPASS", "")) / "models"
        if getattr(sys, "frozen", False) and bundled.is_dir() and not any(cache.glob("*")):
            shutil.copytree(bundled, cache, dirs_exist_ok=True)  # installed app ships the model: no download
        self._model = TextEmbedding(model_name=model, cache_dir=str(cache))

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self._model.query_embed(text))).tolist()


def get_embeddings(provider: str | None = None, model: str | None = None):
    pid = (provider or os.getenv("EMBEDDINGS_PROVIDER", "local")).lower()
    if pid == "fake":
        from langchain_core.embeddings import DeterministicFakeEmbedding

        return DeterministicFakeEmbedding(size=256)
    if pid == "local":
        return LocalEmbeddings(model or os.getenv("EMBEDDINGS_MODEL") or LOCAL_EMBED_MODEL)
    if pid == "huggingface":  # pip install -e ".[local]" (PyTorch)
        from langchain_huggingface import HuggingFaceEmbeddings

        return HuggingFaceEmbeddings(model_name=model or "sentence-transformers/all-MiniLM-L6-v2")
    if pid not in PROVIDERS or not PROVIDERS[pid].embed_model:
        raise ValueError(f"Unknown EMBEDDINGS_PROVIDER {pid!r}. Use one of {EMBEDDING_PROVIDERS}.")
    p = PROVIDERS[pid]
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(
        model=model or os.getenv("EMBEDDINGS_MODEL") or p.embed_model,
        base_url=_base_url(p),
        api_key=_key(p) or "not-needed",
        check_embedding_ctx_length=p.id == "openai",  # tiktoken pre-split only makes sense for OpenAI
    )


def list_models(provider: str, timeout: float = 6.0) -> list[str]:
    """Ask the provider which models it serves. Falls back to the built-in suggestions."""
    p = PROVIDERS[provider]
    fallback = list(p.suggestions) or ([p.chat_model] if p.chat_model else [])
    if p.kind == "anthropic":
        url, headers = "https://api.anthropic.com/v1/models", {"x-api-key": _key(p) or "", "anthropic-version": "2023-06-01"}
    else:
        base = _base_url(p) or "https://api.openai.com/v1"
        url, headers = base.rstrip("/") + "/models", {"Authorization": f"Bearer {_key(p) or 'not-needed'}"}
    if not p.local and not _key(p):
        return fallback
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return fallback
    ids = sorted({m.get("id", "").removeprefix("models/") for m in data.get("data", data.get("models", []))} - {""})
    return ids or fallback


def save_user_settings(values: dict[str, str]) -> Path:
    """Merge values into ~/.rag-lab/.env (readable only by the current user)."""
    HOME.mkdir(parents=True, exist_ok=True)
    current: dict[str, str] = {}
    if USER_ENV.exists():
        for line in USER_ENV.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, _, value = line.partition("=")
                current[key.strip()] = value.strip()
    for key, value in values.items():
        if value:
            current[key] = value
            os.environ[key] = value
        else:
            current.pop(key, None)
    USER_ENV.write_text("".join(f"{k}={v}\n" for k, v in current.items()), encoding="utf-8")
    try:
        USER_ENV.chmod(0o600)
    except OSError:  # Windows ignores POSIX modes
        pass
    return USER_ENV
