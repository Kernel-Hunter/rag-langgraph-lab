# Instructions for coding agents

This file is for whatever coding agent a teammate uses (DeepSeek Harness, Claude Code,
Codex, Cursor, ...). Point your harness at this file if it does not pick it up on its own.

## What this project is

Chapter 5B lab of the Prompt Engineering course (MedTech, SMU), shipped as the Pagewise
desktop app: it answers questions about a course handbook (or any documents), built with LangChain 1.x and LangGraph 1.x.
The code follows the slides on purpose. Keep the same names (`State`, `retrieve`,
`generate`, `PROMPT`, `search_handbook`, `similarity_search`) so the code still reads
like the course material.

## Map

- `src/rag_lab/providers.py`: picks the chat model and the embedding model from `.env`. The only place that imports a provider package.
- `src/rag_lab/indexing.py`: steps 1-4 (load, split, embed, store).
- `src/rag_lab/graph.py`: steps 5-7 as a LangGraph graph, plus the optional `check` node.
- `src/rag_lab/agent.py`: agentic RAG with `create_agent` and a `search_handbook` tool.
- `src/rag_lab/evaluate.py`: eval harness that scores answers against `eval/questions.yaml`.
- `src/rag_lab/cli.py`: `rag-lab ask | agent | chat | eval`.
- `src/rag_lab/server.py`: FastAPI app on 127.0.0.1 with a per-launch token; streams answers as server-sent events.
- `apps/web/`: the React UI (Vite + TypeScript). `npm run build` writes it to `src/rag_lab/web/` (generated, do not edit there). Model output goes through `renderMarkdown` (marked + DOMPurify); never insert it any other way.
- `apps/desktop/`: the Electron shell. It starts the engine (`--engine`), reads its `PAGEWISE_READY` line and loads the UI.
- `src/rag_lab/desktop.py`: engine entry point. `--engine` for Electron, `--serve` for UI development, `--smoke` tests a build, no flag opens a browser app window.
- `src/rag_lab/service.py`: `HelpDesk`, a small wrapper used by older code and tests.
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
- Keep tests offline. Use `DeterministicFakeEmbedding` and `FakeListChatModel`, never a real API in `tests/`.
- One task per branch, one PR per task. Small PRs.
