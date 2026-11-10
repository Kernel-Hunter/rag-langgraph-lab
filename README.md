<div align="center">

<img src="packaging/icon.png" alt="Vesper" width="112">

# Vesper

**An AI agent that works inside your folders, in the terminal or as a desktop app.**

Vesper reads, searches and edits the files in any folder, runs commands when you allow it,
and answers questions about your PDFs with the page each answer came from.
Use it with 17 model providers, or offline with Ollama.

[![tests](https://github.com/Kernel-Hunter/vesper/actions/workflows/ci.yml/badge.svg)](https://github.com/Kernel-Hunter/vesper/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/Kernel-Hunter/vesper?color=8b7cff)](https://github.com/Kernel-Hunter/vesper/releases/latest)
[![license: MIT](https://img.shields.io/badge/license-MIT-2dd4bf)](LICENSE)
![platforms](https://img.shields.io/badge/macOS%20%C2%B7%20Linux%20%C2%B7%20Windows-555)

[Install](#install) · [Quick start](#quick-start) · [Desktop app](#desktop-app) · [Models](#models) · [Safety](#safety) · [Contributing](#contributing)

<img src="docs/terminal.svg" alt="Vesper in a terminal: it runs the tests, finds the bug, shows a diff, waits for approval, then runs the tests again" width="760">

</div>

## Install

macOS and Linux:

```bash
curl -fsSL https://raw.githubusercontent.com/Kernel-Hunter/vesper/main/install.sh | bash
```

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/Kernel-Hunter/vesper/main/install.ps1 | iex
```

That gives you the `vesper` command. The script installs [uv](https://docs.astral.sh/uv/)
if you don't have it, and uv brings its own Python, so there is nothing else to set up.
Nothing needs admin rights.

<details>
<summary>Also install the desktop app, or install with pip</summary>

Desktop app as well as the command (Apple Silicon Mac, x86_64 Linux, Windows):

```bash
curl -fsSL https://raw.githubusercontent.com/Kernel-Hunter/vesper/main/install.sh | bash -s -- --app
```

```powershell
$env:VESPER_APP = '1'; irm https://raw.githubusercontent.com/Kernel-Hunter/vesper/main/install.ps1 | iex
```

The app download is checked against the release's `SHA256SUMS.txt` before anything is
installed. Files fetched by `curl` or PowerShell are not tagged as coming from the internet,
so macOS Gatekeeper and Windows SmartScreen do not block the app. If you download the
`.dmg` or `.exe` from the [releases page](https://github.com/Kernel-Hunter/vesper/releases/latest)
in a browser instead, the first launch shows a warning (macOS: right-click the app, then
Open; Windows: More info, then Run anyway).

With pip or uv, if you prefer to manage Python yourself (3.11 or newer):

```bash
uv tool install "vesper-agent @ https://github.com/Kernel-Hunter/vesper/archive/main.tar.gz"
# or
pip install "vesper-agent @ https://github.com/Kernel-Hunter/vesper/archive/main.tar.gz"
```

To update, run the install command again. To remove the command: `uv tool uninstall vesper-agent`.

</details>

## Quick start

```bash
cd ~/projects/my-app
vesper
```

The first run asks which model to use and, for cloud providers, your API key (typed
hidden and stored only on your computer). Then describe what you want:

```text
❯ explain how this project is organized
❯ the login test fails, find out why and fix it
❯ summarize every PDF in ./papers and cite the pages
❯ add a --verbose flag to cli.py and update the README
```

Vesper looks around before it answers: it lists folders, reads files, searches the code,
and asks you before it changes a file or runs a command.

| In a session | |
|---|---|
| `@path` | attach a file to your message (Tab completes paths) |
| `!command` | run a shell command yourself |
| `/model` | switch provider and model |
| `/key` | set the API key for the current provider |
| `/mode` or `Shift+Tab` | cycle `ask`, `auto-edit`, `yolo` |
| `/clear` | start a new conversation |
| `/docs` | re-read the documents in the folder |
| `/app` | open the desktop app |
| `/help`, `/exit` | help, quit (`Ctrl+D` works too) |

One-shot mode prints the answer and exits, which is handy in scripts:

```bash
vesper "list the TODO comments in src/ with file and line"
git diff | vesper "write a commit message for this diff"
vesper -C ~/notes --model ollama/llama3.2 "what did I write about the budget?"
```

In one-shot mode nobody can answer an approval prompt, so `ask` mode refuses edits and
commands there. Pass `--mode auto-edit` or `--mode yolo` when you want them.

## Desktop app

<img src="docs/desktop.jpg" alt="The Vesper desktop app showing a file edit with its diff" width="720">

The desktop app does the same work with a window around it. Open a folder from the
top bar, and every tool call shows up inline: what the agent read, what it searched, and
a colored diff with Allow, Always allow and Deny before any change.

It also has a Docs mode for a fixed set of documents: drop in PDFs, Markdown or text files
and get streamed answers with clickable page citations. Docs mode is where Vesper started,
as the Chapter 5B lab of a prompt engineering course, and its retrieval code still follows
the course slides (load, split, embed, store, retrieve, generate).

Themes: system, dark, light, and a terminal theme with five phosphor colors and optional
background effects that pause when the window is hidden.

Start it with `vesper app`, from Launchpad or the Start menu, or with `vesper-app`.

## Models

| Cloud | Local |
|---|---|
| OpenAI, Anthropic Claude, Google Gemini, DeepSeek, Mistral, Groq, OpenRouter, xAI Grok, Together AI, Fireworks AI, Cerebras, NVIDIA NIM, Hugging Face, Moonshot Kimi | Ollama, LM Studio, any OpenAI-compatible server |

Pick one with `/model`, or once with `--model provider/model`, for example
`--model anthropic/claude-sonnet-4-5` or `--model ollama/qwen2.5:7b-instruct`.
The model needs tool calling. With Ollama, `qwen2.5:7b-instruct` and `llama3.1:8b` work well.

Document search uses embeddings that run on your computer, so indexing PDFs needs no key
and sends nothing anywhere.

## Safety

An agent that can edit files and run commands should be boring about permissions. Vesper is:

- **It stays in the folder.** Every path is resolved and checked against the folder you
  opened. `..`, absolute paths and symlinks that point outside are refused.
- **It asks first.** In `ask` mode (the default) every edit shows a diff and every command
  shows the exact command line, and nothing happens until you say yes. `auto-edit` lets it
  edit files but still asks before commands. `yolo` never asks; use it only in a folder you
  can afford to lose, such as a fresh git checkout.
- **It asks before trusting a folder.** The first time you run `vesper` somewhere, it asks
  whether you trust that folder.
- **File contents are data.** The system prompt tells the model to treat what it reads as
  data, never as instructions. That lowers the risk of prompt injection from a hostile
  file, but no model is immune, which is why the approval prompt exists.
- **Keys stay local.** API keys live in `~/.vesper/.env`, readable only by your user.
- **The desktop engine listens on 127.0.0.1 only**, with a per-launch token, host and
  origin checks and a strict Content Security Policy. The Electron window is sandboxed
  with Node.js disabled.

Found a security problem? Please report it privately, see [SECURITY.md](SECURITY.md).

## How it works

```
 vesper (terminal)          Vesper.app (Electron)
   rich + prompt_toolkit      React UI ── HTTP/SSE on 127.0.0.1 ── FastAPI engine
          │                                                            │
          └──────────────── coder.Session ─────────────────────────────┘
                                 │  LangGraph create_agent loop
                                 ▼
               workspace.Workspace: list_files, read_file, glob, grep,
               search_docs, write_file, edit_file, run_command
```

| Path | What it is |
|---|---|
| `src/vesper/workspace.py` | The folder tools, path checks and approval gate |
| `src/vesper/coder.py` | The agent loop and its event stream, shared by both front ends |
| `src/vesper/tui.py` | The `vesper` terminal app |
| `src/vesper/server.py` | The desktop engine API |
| `src/vesper/providers.py` | Every model provider and the local embeddings |
| `src/vesper/indexing.py`, `graph.py`, `agent.py` | Docs mode: the course's RAG pipeline in LangChain and LangGraph |
| `apps/web/` | React interface (Vite + TypeScript) |
| `apps/desktop/` | Electron shell and packaging |

The desktop layout follows [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
(MIT). Code adapted from it is credited in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Develop

```bash
git clone https://github.com/Kernel-Hunter/vesper.git && cd vesper
uv venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
pytest -q                                   # offline, no API key needed
vesper                                      # the terminal app from your checkout
```

Desktop app from source (Node 22):

```bash
npm --prefix apps/web install && npm --prefix apps/web run build
npm --prefix apps/desktop install && npm --prefix apps/desktop start
```

For UI work, run the engine on a fixed port and the Vite dev server next to it:

```bash
VESPER_PORT=8765 VESPER_TOKEN=devtoken vesper-app --serve
npm --prefix apps/web run dev               # open http://localhost:5173/#token=devtoken
```

The course commands still work: `vesper ask`, `vesper agent`, `vesper chat` and
`vesper eval` (also available as `rag-lab`). `vesper eval` scores `eval/questions.yaml`,
so you can compare chunk sizes, `k` and models with numbers.

Releases: push a tag such as `v0.4.0` and GitHub Actions builds, smoke-tests and publishes
macOS, Linux and Windows builds with checksums.

## Contributing

Bug reports, ideas and pull requests are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md);
issues labeled `good first issue` are a good way in. If you use a coding agent,
[AGENTS.md](AGENTS.md) tells it how the project is laid out.

## License

[MIT](LICENSE)
