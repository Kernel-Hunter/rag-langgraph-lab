"""Chat models for many providers.

Every other module asks this file for a chat model, so a provider
is added here and nowhere else. Most providers speak the OpenAI API, so one client with a
different base URL covers them. Anthropic and Ollama get their own clients.
"""

from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values

APP_NAME = "Vesper"


def resolve_home(user_home: Path | None = None) -> Path:
    """Per-user folder: settings, keys and the session database. VESPER_HOME overrides ~/.vesper."""
    override = os.getenv("VESPER_HOME")
    if override:
        return Path(override)
    return (Path(user_home) if user_home is not None else Path.home()) / ".vesper"


HOME = resolve_home()
USER_ENV = HOME / ".env"

# Only the app's own key file. A .env in whatever folder Vesper runs in belongs to that project.
# Variables set in the shell win over the file. The terminal and the desktop app both write
# this file, so it is read again whenever it changes (see refresh_keys).
_FROM_SHELL = frozenset(os.environ)
_env_state: dict = {"mtime": None, "keys": set()}


def refresh_keys() -> None:
    """Pick up keys another Vesper process saved (the terminal app or the desktop app)."""
    try:
        mtime = USER_ENV.stat().st_mtime_ns
    except OSError:
        mtime = None
    if mtime == _env_state["mtime"]:
        return
    values = {k: v for k, v in (dotenv_values(USER_ENV) if mtime is not None else {}).items() if v}
    for key in _env_state["keys"] - set(values):  # removed from the file since the last read
        if key not in _FROM_SHELL:
            os.environ.pop(key, None)
    for key, value in values.items():
        if key not in _FROM_SHELL:
            os.environ[key] = value
    _env_state.update(mtime=mtime, keys=set(values))


refresh_keys()


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str  # openai | anthropic
    base_url: str | None
    key_env: str | None
    chat_model: str
    key_url: str = ""
    suggestions: tuple[str, ...] = field(default_factory=tuple)
    local: bool = False


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in [
        Provider("openai", "OpenAI", "openai", None, "OPENAI_API_KEY", "gpt-4.1-nano",
                 "https://platform.openai.com/api-keys", ("gpt-4.1-nano", "gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini")),
        Provider("anthropic", "Anthropic Claude", "anthropic", None, "ANTHROPIC_API_KEY", "claude-haiku-5-5",
                 "https://console.anthropic.com/settings/keys", ("claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5")),
        Provider("google", "Google Gemini", "openai", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", "gemini-2.5-flash",
                 "https://aistudio.google.com/apikey", ("gemini-2.5-flash", "gemini-2.5-pro")),
        Provider("deepseek", "DeepSeek", "openai", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat",
                 "https://platform.deepseek.com/api_keys", ("deepseek-chat", "deepseek-flash", "deepseek-v4-pro", "deepseek-reasoner")),
        Provider("mistral", "Mistral", "openai", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistral-small-latest",
                 "https://console.mistral.ai/api-keys", ("mistral-small-latest", "mistral-medium-latest", "mistral-large-latest")),
        Provider("groq", "Groq", "openai", "https://api.groq.com/openai/v1", "GROQ_API_KEY", "llama-3.3-70b-versatile",
                 "https://console.groq.com/keys", ("llama-3.3-70b-versatile", "llama-3.1-8b-instant")),
        Provider("openrouter", "OpenRouter", "openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "openrouter/auto",
                 "https://openrouter.ai/keys", ("openrouter/auto", "deepseek/deepseek-chat", "meta-llama/llama-3.3-70b-instruct")),
        Provider("xai", "xAI Grok", "openai", "https://api.x.ai/v1", "XAI_API_KEY", "grok-3-mini",
                 "https://console.x.ai", ("grok-3-mini", "grok-3")),
        Provider("together", "Together AI", "openai", "https://api.together.xyz/v1", "TOGETHER_API_KEY", "meta-llama/Llama-3.3-70B-Instruct-Turbo",
                 "https://api.together.ai/settings/api-keys"),
        Provider("fireworks", "Fireworks AI", "openai", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", "accounts/fireworks/models/llama-v3p3-70b-instruct",
                 "https://fireworks.ai/account/api-keys"),
        Provider("cerebras", "Cerebras", "openai", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "llama-3.3-70b",
                 "https://cloud.cerebras.ai"),
        Provider("nvidia", "NVIDIA NIM", "openai", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", "meta/llama-3.3-70b-instruct",
                 "https://build.nvidia.com"),
        Provider("huggingface", "Hugging Face", "openai", "https://router.huggingface.co/v1", "HF_TOKEN", "meta-llama/Llama-3.3-70B-Instruct",
                 "https://huggingface.co/settings/tokens"),
        Provider("moonshot", "Moonshot Kimi", "openai", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY", "kimi-k2-0905-preview",
                 "https://platform.moonshot.ai"),
        Provider("ollama", "Ollama (local)", "openai", "http://localhost:11434/v1", None, "qwen2.5:7b-instruct",
                 "https://ollama.com/download", ("qwen2.5:7b-instruct", "llama3.2", "gemma3"), local=True),
        Provider("lmstudio", "LM Studio (local)", "openai", "http://localhost:1234/v1", None, "local-model",
                 "https://lmstudio.ai", local=True),
        Provider("custom", "Custom (OpenAI-compatible)", "openai", "", "CUSTOM_API_KEY", "",
                 ""),
    ]
}



# Ollama loads a model with a 4096-token window unless told otherwise. That is too small for
# an agent (the system prompt, the tool list and one file fill it), so Vesper asks for more.
OLLAMA_CONTEXT_TOKENS = 16_384
DEFAULT_CONTEXT_TOKENS = 120_000  # assumed for hosted models


def ollama_context_tokens() -> int:
    """Context window requested from Ollama. VESPER_OLLAMA_CTX overrides it."""
    try:
        return max(2048, int(os.getenv("VESPER_OLLAMA_CTX", "")))
    except ValueError:
        return OLLAMA_CONTEXT_TOKENS


def context_tokens(provider: str | None) -> int:
    """How many tokens a conversation with this provider may hold."""
    return ollama_context_tokens() if provider == "ollama" else DEFAULT_CONTEXT_TOKENS


def _key(provider: Provider) -> str | None:
    refresh_keys()
    return os.getenv(provider.key_env) if provider.key_env else None


def _base_url(provider: Provider) -> str | None:
    refresh_keys()
    if provider.id == "custom":
        return os.getenv("CUSTOM_BASE_URL") or None
    if provider.id == "ollama":
        return os.getenv("OLLAMA_BASE_URL", provider.base_url)
    return provider.base_url


FAKE_ANSWER = "Hello from the Vesper demo model."


def fake_llm_enabled() -> bool:
    """VESPER_FAKE_LLM=1: offline fake chat model, for UI end-to-end tests."""
    return os.getenv("VESPER_FAKE_LLM", "").strip().lower() in {"1", "true", "yes"}


def _fake_chat_model():
    import itertools

    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class FakeChat(GenericFakeChatModel):
        """Streams FAKE_ANSWER word by word. With the folder tools bound it plays a short script
        instead (list the folder, write a note, answer) so the tool and approval UI can be tested."""

        folder: bool = False

        def bind_tools(self, tools, **kwargs):
            names = {getattr(t, "name", "") for t in tools}
            if "write_file" in names:
                return FakeChat(messages=self.messages, folder=True, disable_streaming=True)
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if not self.folder:
                return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
            last = messages[-1] if messages else None
            if not isinstance(last, ToolMessage) and "task list" in str(getattr(last, "content", "")).lower():
                msg = AIMessage(content="", tool_calls=[{"name": "update_tasks", "id": "fake-tasks", "args": {
                    "tasks": "[x] Understand the request\n[>] Look at the folder\n[ ] Write the note"}}])
            elif not isinstance(last, ToolMessage) or last.name == "update_tasks":
                msg = AIMessage(content="<think>The user wants something done in this folder. "
                                        "I will list it, then write a short note.</think>Let me look at the folder first.",
                                tool_calls=[{"name": "list_files", "args": {"path": "."}, "id": "fake-list"}])
            elif last.name == "list_files":
                msg = AIMessage(content="", tool_calls=[{"name": "write_file", "id": "fake-write", "args": {
                    "path": "vesper-notes.md", "content": "# Notes\n\nWritten by the Vesper demo model.\n"}}])
            else:
                msg = AIMessage(content=f"Done. The write said: {last.content}")
            return ChatResult(generations=[ChatGeneration(message=msg)])

    return FakeChat(messages=itertools.repeat(AIMessage(content=FAKE_ANSWER)))


THINKING = ("off", "low", "medium", "high")
# Claude models from before adaptive thinking: they take a fixed thinking budget instead.
OLD_CLAUDE = ("claude-3", "claude-haiku-4", "claude-sonnet-4-0", "claude-sonnet-4-5", "claude-opus-4-0",
              "claude-opus-4-1", "claude-opus-4-5", "claude-sonnet-4-2", "claude-opus-4-2")


def get_llm(provider: str | None = None, model: str | None = None, temperature: float = 0,
            thinking: str = "off"):
    """The chat model. `thinking` (low | medium | high) asks reasoning models to think and to
    show it; "off" sends nothing extra, so each model does what it does by default."""
    thinking = thinking if thinking in THINKING else "off"
    if fake_llm_enabled():
        return _fake_chat_model()
    pid = (provider or os.getenv("LLM_PROVIDER", "openai")).lower()
    if pid not in PROVIDERS:
        raise ValueError(f"Unknown LLM_PROVIDER {pid!r}. Use one of {sorted(PROVIDERS)}.")
    p = PROVIDERS[pid]
    model = model or os.getenv("LLM_MODEL") or p.chat_model
    if p.kind == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # No temperature: current Claude models reject any value but their default.
        extra: dict = {}
        if thinking != "off" and model.startswith(OLD_CLAUDE):
            extra = {"thinking": {"type": "enabled", "budget_tokens": 4096}, "max_tokens": 16_000}
        elif thinking != "off":
            extra = {"thinking": {"type": "adaptive", "display": "summarized"}, "output_config": {"effort": thinking}}
        return ChatAnthropic(model=model, api_key=_key(p), **extra)
    if p.id == "ollama":
        from langchain_ollama import ChatOllama

        # The native API, not the OpenAI-compatible one: only it accepts a context size.
        base = (_base_url(p) or "").rstrip("/").removesuffix("/v1")
        return ChatOllama(model=model, temperature=temperature, base_url=base, num_ctx=ollama_context_tokens(),
                          reasoning=True if thinking != "off" else None)
    from langchain_openai import ChatOpenAI

    if p.id == "openai":
        # The Responses API, with no temperature: OpenAI's newer models refuse tools on the
        # older chat endpoint and refuse a temperature, and every chat model accepts this.
        extra = {"reasoning": {"effort": thinking, "summary": "auto"}} if thinking != "off" else {}
        return ChatOpenAI(model=model, api_key=_key(p), use_responses_api=True, **extra)
    if thinking != "off":  # reasoning models take an effort level and no temperature
        return ChatOpenAI(model=model, base_url=_base_url(p), api_key=_key(p) or "not-needed",
                          reasoning_effort=thinking)
    return ChatOpenAI(
        model=model,
        temperature=temperature,
        base_url=_base_url(p),
        api_key=_key(p) or "not-needed",  # local servers ignore it
    )


REFUSED = ("unsupported", "not supported", "does not support", "doesn't support", "not allowed", "only the default",
           "invalid", "unknown parameter", "unrecognized", "extra inputs")
# Optional settings a provider may refuse, by the word its error message uses for them.
OPTIONAL_SETTINGS = {
    "temperature": ("temperature",),
    "reasoning": ("reasoning_effort", "reasoning"),
    "thinking": ("thinking", "output_config", "reasoning"),
    "think": ("reasoning",),
}


def adapt(llm, exc: BaseException):
    """A copy of `llm` without the optional setting the provider just refused, or None.

    Providers disagree about which models take a temperature or a thinking level, and the
    list changes with every model release. So the request is the test: when a model answers
    "temperature is not supported", the next try goes without one.
    """
    text = str(exc).lower()
    if not any(word in text for word in REFUSED):
        return None
    update = {}
    for word, fields in OPTIONAL_SETTINGS.items():
        if word in text:
            update.update({f: None for f in fields if getattr(llm, f, None) is not None})
    if not update or not hasattr(llm, "model_copy"):
        return None
    return llm.model_copy(update=update)


# Model ids that are not chat models, so they are left out of the model picker.
NOT_CHAT = ("embedding", "embed-", "tts", "whisper", "image", "audio", "realtime", "moderation", "transcribe",
            "dall-e", "sora", "rerank", "guard")


def agent_middleware(llm) -> list:
    """Provider-specific helpers for the agent loop. For Claude: prompt caching, so the system
    prompt, the tool list and the earlier conversation are billed at a fraction on every step."""
    if type(llm).__name__ != "ChatAnthropic":
        return []
    from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

    return [AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore")]


def list_models(provider: str, timeout: float = 6.0) -> list[str]:
    """Ask the provider which models it serves. Falls back to the built-in suggestions."""
    p = PROVIDERS[provider]
    fallback = list(p.suggestions) or ([p.chat_model] if p.chat_model else [])
    if p.kind == "anthropic":
        url, headers = "https://api.anthropic.com/v1/models", {"x-api-key": _key(p) or "", "anthropic-version": "2023-06-01"}
    else:
        base = _base_url(p) or "https://api.openai.com/v1"
        url, headers = base.rstrip("/") + "/models", {"Authorization": f"Bearer {_key(p) or 'not-needed'}"}
    custom_endpoint = p.id == "custom" and bool(_base_url(p))  # may not need a key
    if p.id == "custom" and not custom_endpoint:
        return fallback
    if not p.local and not _key(p) and not custom_endpoint:
        return fallback
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            data = json.load(resp)
    except Exception:
        return fallback
    ids = sorted({m.get("id", "").removeprefix("models/") for m in data.get("data", data.get("models", []))} - {""})
    ids = [m for m in ids if not any(word in m.lower() for word in NOT_CHAT)] or ids
    return ids or fallback


def _restrict(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:  # Windows ignores POSIX modes
        pass


def save_user_settings(values: dict[str, str]) -> Path:
    """Merge values into HOME/.env (readable only by the current user).

    Values with a line break are refused: they would inject extra variables into the file.
    The file is replaced atomically, so a crash never leaves it half written.
    """
    import tempfile

    for key, value in values.items():
        if not isinstance(key, str) or not key or any(c in key for c in "\r\n=#") or key != key.strip():
            raise ValueError(f"invalid setting name {key!r}")
        if value is not None and ("\n" in str(value) or "\r" in str(value)):
            raise ValueError(f"{key} must not contain a line break")
    HOME.mkdir(parents=True, exist_ok=True)
    _restrict(HOME, 0o700)
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
            os.environ.pop(key, None)
    fd, tmp = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=HOME)  # created 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write("".join(f"{k}={v}\n" for k, v in current.items()))
            f.flush()
            os.fsync(f.fileno())
        _restrict(Path(tmp), 0o600)
        os.replace(tmp, USER_ENV)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _restrict(USER_ENV, 0o600)
    return USER_ENV
