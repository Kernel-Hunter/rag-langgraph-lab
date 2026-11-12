# Instructions for coding agents

This file is for whatever coding agent a teammate uses (Codex, Cursor, Claude Code,
Aider, ...). Point your harness at this file if it does not pick it up on its own.

## What this project is

Vesper: a general AI coding agent, as a terminal app (`vesper`) and a desktop app. It reads,
searches, edits and runs commands, starting in the folder where the user opened it and
moving to other folders when asked. It asks before edits and commands, saves every
conversation in SQLite, and keeps a log of what it did. Docs mode (answers from documents
with page citations) is a retrieval pipeline built with LangChain 1.x and LangGraph 1.x.

## Map

- `src/vesper/prompts.py`: every prompt sent to a model. The agent's system prompt is general on purpose: no topic, course or company in it. Project-specific guidance comes from the user's `VESPER.md` / `AGENTS.md` file, which `agent_system_prompt` appends.
- `src/vesper/workspace.py`: the tools (`list_files`, `read_file`, `glob`, `grep`, `search_docs`, `change_directory`, `write_file`, `edit_file`, `run_command`). `Workspace.root` is the working directory and can move; paths are not confined to it. Every write or command goes through `_gate`.
- `src/vesper/coder.py`: `Session`, the `create_agent` loop over those tools. Saves history to the store, logs events, trims long histories (`fit_history`), repairs interrupted ones (`repair_history`). Yields token, reasoning, tool_draft, tool_start, tool_end, sources, cwd, notice, usage and done events. `transcript()` turns saved messages into turns both front ends draw.
- `src/vesper/store.py`: `Store`, the SQLite database at `~/.vesper/vesper.db` (tables `sessions`, `messages`, `events`).
- `src/vesper/tui.py`: the `vesper` terminal app: session loop, slash commands, one-shot mode, `vesper sessions`, `vesper logs`. The agent runs in a worker thread; the main thread draws and asks.
- `src/vesper/ui.py`: how the terminal app looks (colors, banner, tool lines, streaming Markdown, session and log tables). No model or file access.
- `src/vesper/onboarding.py`: `config.json`, the model and key pickers, the first-run and trust prompts.
- `src/vesper/providers.py`: picks the chat model and the embedding model. The only place that imports a provider package.
- `src/vesper/indexing.py`: load, split, embed, store.
- `src/vesper/graph.py`: retrieve and generate as a LangGraph graph, plus the optional `check` node.
- `src/vesper/agent.py`: agentic retrieval with `create_agent` and a `search_documents` tool.
- `src/vesper/evaluate.py`: eval harness that scores answers against `eval/questions.yaml`.
- `src/vesper/cli.py`: `vesper ask | agent | chat | eval` (also `rag-lab`), document question answering from the command line.
- `src/vesper/server.py`: FastAPI app on 127.0.0.1 with a per-launch token; streams answers as server-sent events. Folder mode runs a `Session` per chat in a worker thread and turns approvals into `approval` events answered via `POST /api/approve`. `GET /api/sessions` and `/api/sessions/{id}` serve the session database to the app; a chat request with `session_id` continues a saved conversation.
- `apps/web/`: the React UI (Vite + TypeScript). `npm run build` writes it to `src/vesper/web/` (generated, do not edit there). Model output goes through `renderMarkdown` (marked + DOMPurify); never insert it any other way.
- `apps/desktop/`: the Electron shell. It starts the engine (`--engine`), reads its `VESPER_READY` line and loads the UI. `preload.ts` exposes only a native folder picker.
- `src/vesper/desktop.py`: engine entry point. `--engine` for Electron, `--serve` for UI development, `--smoke` tests a build, no flag opens a browser app window.
- `data/`: sample documents (`.pdf`, `.md`, `.txt`).
- `tests/`: offline tests with fake embeddings and a fake chat model.

## Commands

```bash
pip install -e ".[dev]"   # add ,chroma or ,local as needed
pytest -q                 # must pass before every PR, needs no API key
npm --prefix apps/web test && npm --prefix apps/web run typecheck   # when you touch the UI
rag-lab ask "How many absences are allowed?"
rag-lab eval --label my-change
```

## Rules

- Never commit `.env` or any API key. Keys go in `.env`, which git ignores.
- Use current APIs: `langchain.agents.create_agent`, `langgraph.graph.StateGraph`. Do not use `RetrievalQA`, `hub.pull` or other retired LangChain APIs.
- Nodes return only the keys they changed.
- A new provider goes in `providers.py` only. Other modules must not change.
- When a change can affect answer quality (prompt, chunk size, k, model), run `rag-lab eval` before and after and paste both scores in the PR.
- A tool must never write a file or run a command without `_gate`. Dangerous commands and credential folders are `strict` (they ask in every mode); secret files are never read. Add a test for any new tool.
- Keep prompts general. Nothing about a specific subject, school or company goes into `prompts.py`.
- Tests must not touch the real home folder. `conftest.py` points `HOME` and `VESPER_HOME` at temporary folders; use absolute paths under `tmp_path`.
- Keep tests offline. Use `DeterministicFakeEmbedding` and `FakeListChatModel`, never a real API in `tests/`.
- One task per branch, one PR per task. Small PRs.
