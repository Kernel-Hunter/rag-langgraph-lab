# Instructions for coding agents

This file is for whatever coding agent a teammate uses (Codex, Cursor, Claude Code,
Aider, ...). Point your harness at this file if it does not pick it up on its own.

## What this project is

Vesper: an AI agent that works inside a folder, as a terminal app (`vesper`) and a desktop
app. It reads, searches, edits and runs commands in the folder the user opens, asking before
edits and commands. It started as the Chapter 5B lab of the Prompt Engineering course
(MedTech, SMU), and Docs mode (answers from documents with page citations) is still that lab,
built with LangChain 1.x and LangGraph 1.x. The Docs mode code follows the slides on purpose. Keep the same names (`State`, `retrieve`,
`generate`, `PROMPT`, `search_handbook`, `similarity_search`) so the code still reads
like the course material.

## Map

- `src/vesper/workspace.py`: the folder tools (`list_files`, `read_file`, `glob`, `grep`, `search_docs`, `write_file`, `edit_file`, `run_command`). Every path goes through `resolve`, every write or command through `_gate`.
- `src/vesper/coder.py`: `Session`, the `create_agent` loop over those tools. Yields token, tool_start, tool_end, sources and done events.
- `src/vesper/tui.py`: the `vesper` terminal app (rich + prompt_toolkit) and one-shot mode.
- `src/vesper/providers.py`: picks the chat model and the embedding model from `.env`. The only place that imports a provider package.
- `src/vesper/indexing.py`: steps 1-4 (load, split, embed, store).
- `src/vesper/graph.py`: steps 5-7 as a LangGraph graph, plus the optional `check` node.
- `src/vesper/agent.py`: agentic RAG with `create_agent` and a `search_handbook` tool.
- `src/vesper/evaluate.py`: eval harness that scores answers against `eval/questions.yaml`.
- `src/vesper/cli.py`: `vesper ask | agent | chat | eval` (also `rag-lab`), the lab commands.
- `src/vesper/server.py`: FastAPI app on 127.0.0.1 with a per-launch token; streams answers as server-sent events. Folder mode runs a `Session` per chat in a worker thread and turns approvals into `approval` events answered via `POST /api/approve`.
- `apps/web/`: the React UI (Vite + TypeScript). `npm run build` writes it to `src/vesper/web/` (generated, do not edit there). Model output goes through `renderMarkdown` (marked + DOMPurify); never insert it any other way.
- `apps/desktop/`: the Electron shell. It starts the engine (`--engine`), reads its `VESPER_READY` line and loads the UI. `preload.ts` exposes only a native folder picker.
- `src/vesper/desktop.py`: engine entry point. `--engine` for Electron, `--serve` for UI development, `--smoke` tests a build, no flag opens a browser app window.
- `src/vesper/service.py`: `HelpDesk`, a small wrapper used by older code and tests.
- `data/`: documents to index (`.pdf`, `.md`, `.txt`).
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
- Folder tools must never touch anything outside the workspace root, and must never write or run without `_gate`. Add a test for any new tool.
- Keep tests offline. Use `DeterministicFakeEmbedding` and `FakeListChatModel`, never a real API in `tests/`.
- One task per branch, one PR per task. Small PRs.
