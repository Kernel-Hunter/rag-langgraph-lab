# RAG Help Desk

A desktop app that answers questions from your own documents, built on the LangChain and
LangGraph code from the Chapter 5B lab (Prompt Engineering, MedTech). Every answer shows
the pages it came from. It works with most AI model providers, or fully offline with Ollama.

![RAG Help Desk](packaging/icon.png)

## Install

One command, no "unidentified developer" or SmartScreen warning.

macOS (Apple Silicon) and Linux (x86_64):

```bash
curl -fsSL https://raw.githubusercontent.com/Kernel-Hunter/rag-langgraph-lab/main/install.sh | bash
```

Windows 10/11 (PowerShell):

```powershell
irm https://raw.githubusercontent.com/Kernel-Hunter/rag-langgraph-lab/main/install.ps1 | iex
```

The scripts download the app from the [latest release](https://github.com/Kernel-Hunter/rag-langgraph-lab/releases/latest)
and install it for your user only. Browsers tag the files they download, and that tag is
what triggers the macOS and Windows warnings; `curl` and PowerShell do not add it. If you
download the `.dmg` or `Setup.exe` by hand instead, the first launch shows the warning
(macOS: right-click the app and choose Open; Windows: More info, then Run anyway).

## What it does

- Chat window with streaming answers, citations as page chips, and source cards you can expand.
- Model picker in the composer. It lists the models your provider actually serves, or you type any model id.
- Providers: OpenAI, Anthropic Claude, Google Gemini, DeepSeek, Mistral, Groq, OpenRouter, xAI Grok,
  Together, Fireworks, Cerebras, NVIDIA NIM, Hugging Face, Moonshot Kimi, Ollama, LM Studio, and any
  OpenAI-compatible endpoint.
- Built-in embeddings that run on your computer (no key, no cost). The installed app ships the model.
- Graph mode (retrieve, then generate, from slides 6-7) and Agent mode (the model decides when to search, slide 9).
- "Verify" adds the check node: the model says whether its answer is supported by the sources.
- Drag PDF, Markdown or text files into Documents. Chunk size, k and embeddings live under Settings, Retrieval.
- Chats are kept on your computer. Keys stay in `~/.rag-lab/.env` and are never sent back to the page.

Start with Settings, Models: paste a key for the provider you use, then pick a model from
the menu at the bottom left of the composer. With [Ollama](https://ollama.com) installed you
need no key at all.

## Run from source

Python 3.11 or newer.

```bash
git clone https://github.com/Kernel-Hunter/rag-langgraph-lab.git
cd rag-langgraph-lab
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
rag-lab-app
```

On Windows use `py -m venv .venv` and `.venv\Scripts\Activate.ps1`. `rag-lab-app --browser`
opens the UI in your browser instead of a window.

## Command line and eval harness

The lab code is also available without the window:

```bash
rag-lab ask "How many absences are allowed before I fail?"
rag-lab ask "Can I bring my dog?" --check
rag-lab agent "I was 20 minutes late twice. Absences?"
rag-lab eval --chunk-size 150 -k 1 --label small-chunks
```

`--llm` and `--embeddings` pick providers (for example `--llm groq --embeddings local`).
`rag-lab eval` scores `eval/questions.yaml` so you can compare settings with numbers.

## Project layout

| Path | What it is |
|---|---|
| `src/rag_lab/indexing.py` | Steps 1-4: load, split, embed, store |
| `src/rag_lab/graph.py` | Steps 5-7 as a LangGraph graph, plus the `check` node |
| `src/rag_lab/agent.py` | Agentic RAG with `create_agent` |
| `src/rag_lab/providers.py` | Every model provider and the local embeddings |
| `src/rag_lab/server.py` | Local API the window talks to (127.0.0.1 only, token-protected) |
| `src/rag_lab/web/` | The interface: HTML, CSS, JavaScript |
| `src/rag_lab/desktop.py` | Opens the native window |
| `packaging/` | PyInstaller build, Windows installer script, icon |

## Lab tasks

Slide 10 tasks are GitHub issues. Assign yourself one, work on a branch, open a pull request.
`data/handbook.md` is a sample; put the real `handbook.pdf` in the app's Documents.

## Releases

Push a tag and GitHub builds, tests and publishes all three platforms:

```bash
git tag v0.3.0 && git push origin v0.3.0
```

## Tests

```bash
pytest -q
```

They use fake models and need no key or network.
