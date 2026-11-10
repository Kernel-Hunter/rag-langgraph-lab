# Pagewise

<img src="packaging/icon.png" alt="Pagewise icon" width="96" align="right">

Pagewise answers questions from your own documents and shows the page each answer came
from. It started as the Chapter 5B lab (Prompt Engineering, MedTech), and the retrieval
code still follows the course slides: LangChain for loading and splitting, LangGraph for
the graph and the agent. It works with most AI model providers, or fully offline with Ollama.

## Install

One command, with no "unidentified developer" or SmartScreen warning.

macOS (Apple Silicon) and Linux (x86_64):

```bash
curl -fsSL https://raw.githubusercontent.com/Kernel-Hunter/rag-langgraph-lab/main/install.sh | bash
```

Windows 10/11 (PowerShell):

```powershell
irm https://raw.githubusercontent.com/Kernel-Hunter/rag-langgraph-lab/main/install.ps1 | iex
```

The scripts download the app from the [latest release](https://github.com/Kernel-Hunter/rag-langgraph-lab/releases/latest),
check it against the release's `SHA256SUMS.txt`, and install it for your user only.
Browsers tag the files they download, and that tag is what triggers the macOS and Windows
warnings; `curl` and PowerShell do not add it. If you download the `.dmg` or `.exe` by hand
instead, the first launch shows the warning (macOS: right-click the app and choose Open;
Windows: More info, then Run anyway).

## What it does

- Streaming answers with page citations. Click a `p.3` chip to jump to the matching source card.
- A model picker in the composer that lists the models your provider actually serves. You can also type any model id.
- Providers: OpenAI, Anthropic Claude, Google Gemini, DeepSeek, Mistral, Groq, OpenRouter, xAI Grok,
  Together, Fireworks, Cerebras, NVIDIA NIM, Hugging Face, Moonshot Kimi, Ollama, LM Studio, and any
  OpenAI-compatible server.
- Embeddings that run on your computer, so indexing needs no key and costs nothing. The app ships the model.
- Graph mode (retrieve, then generate, slides 6 and 7) and Agent mode (the model decides when to search, slide 9).
- Verify adds the check node: a second pass says whether the answer is supported by the sources.
- Drop PDF, Markdown or text files anywhere in the window to add them.
- Themes: system, dark, light and a terminal theme with five phosphor colors. Optional
  background effects (aurora, code rain, CRT scanlines, horizon grid, starfield) run at a
  capped frame rate, pause when the window is hidden, and stay off if your system asks for reduced motion.
- Chats stay on your computer. Keys are stored in `~/.pagewise/.env` (readable only by your
  user) and never sent back to the interface.

Start in Settings, Models: paste a key for the provider you use, then pick a model from
the menu under the message box. With [Ollama](https://ollama.com) installed you need no key.

Shortcuts: `Cmd/Ctrl+N` new chat, `Cmd/Ctrl+,` settings, `Cmd/Ctrl+B` sidebar,
`Cmd/Ctrl+K` search chats, `Esc` stops an answer.

## How it is built

The layout follows [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)
(MIT): an Electron shell starts a separate engine process and loads a React interface that
talks to it over a local connection. Some of its desktop code is adapted here, credited in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The engine is Python because the lab is
about LangChain and LangGraph.

| Path | What it is |
|---|---|
| `src/rag_lab/indexing.py` | Steps 1 to 4: load, split, embed, store |
| `src/rag_lab/graph.py` | Steps 5 to 7 as a LangGraph graph, plus the `check` node |
| `src/rag_lab/agent.py` | Agentic RAG with `create_agent` |
| `src/rag_lab/providers.py` | Every model provider and the local embeddings |
| `src/rag_lab/server.py` | The engine's API: 127.0.0.1 only, per-launch token, host and origin checks, strict CSP |
| `src/rag_lab/desktop.py` | Engine entry point (`--engine`), plus a browser mode for development |
| `apps/web/` | React interface (Vite). `npm run build` writes it into `src/rag_lab/web/` |
| `apps/desktop/` | Electron shell: window, engine process, packaging |
| `packaging/` | Freezes the engine with PyInstaller, draws the icon |

## Run from source

Python 3.11 or newer and Node 22.

```bash
git clone https://github.com/Kernel-Hunter/rag-langgraph-lab.git
cd rag-langgraph-lab
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
npm --prefix apps/web install && npm --prefix apps/web run build
npm --prefix apps/desktop install && npm --prefix apps/desktop start
```

On Windows use `py -m venv .venv` and `.venv\Scripts\Activate.ps1`. Without Electron,
`rag-lab-app` opens the same interface in a Chrome or Edge app window.

For interface work, run the engine with a fixed port and token, then the Vite dev server:

```bash
RAG_LAB_PORT=8765 RAG_LAB_TOKEN=devtoken rag-lab-app --serve
npm --prefix apps/web run dev   # open http://localhost:5173/#token=devtoken
```

## Command line and eval harness

The lab code also runs without the app:

```bash
rag-lab ask "How many absences are allowed before I fail?"
rag-lab ask "Can I bring my dog?" --check
rag-lab agent "I was 20 minutes late twice. Absences?"
rag-lab eval --chunk-size 150 -k 1 --label small-chunks
```

`--llm` and `--embeddings` pick providers (for example `--llm groq --embeddings local`).
`rag-lab eval` scores `eval/questions.yaml`, so you can compare settings with numbers.

## Lab tasks

Slide 10 tasks are GitHub issues. Assign yourself one, work on a branch, open a pull request.
`data/handbook.md` is a sample; put the real `handbook.pdf` in the app's Documents.

## Tests

```bash
pytest -q                      # engine, API security, fake models, no key or network
npm --prefix apps/web test     # interface units
```

## Releases

Push a tag and GitHub builds, smoke-tests and publishes all three platforms:

```bash
git tag v0.3.1 && git push origin v0.3.1
```
