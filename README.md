# RAG help desk with LangChain and LangGraph

Team repo for the Chapter 5B lab (Prompt Engineering, MedTech). It takes the code from the
slides (load, split, embed, store, retrieve, augment, generate) and wraps it in a small
project you can run, test and measure:

- the fixed 2-step graph (`retrieve` then `generate`) from slides 6-7
- an optional third `check` node that flags answers not supported by the context
- the agentic version from slide 9 (`create_agent` with a `search_handbook` tool)
- swappable providers: OpenAI, DeepSeek, Ollama, local HuggingFace embeddings
- an eval harness that scores a fixed question set, so you compare settings with numbers
- offline tests that run on every push and pull request

## Setup

Works on Windows, Linux and macOS with Python 3.11 or newer. CI tests all three.

Linux / macOS:

```bash
git clone https://github.com/Kernel-Hunter/rag-langgraph-lab.git
cd rag-langgraph-lab
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,local]"
cp .env.example .env
```

Windows (PowerShell):

```powershell
git clone https://github.com/Kernel-Hunter/rag-langgraph-lab.git
cd rag-langgraph-lab
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev,local]"
copy .env.example .env
```

If PowerShell refuses to run `Activate.ps1`, run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or use `cmd` and
`.venv\Scripts\activate.bat`. Then open `.env` and put in your keys.

Pick a provider pair in `.env`:

| You have | `LLM_PROVIDER` | `EMBEDDINGS_PROVIDER` | Extra to install |
|---|---|---|---|
| OpenAI key (as in the slides) | `openai` | `openai` | none |
| DeepSeek key only | `deepseek` | `huggingface` | `local` |
| No key, everything on your laptop | `ollama` | `ollama` | `ollama` |

DeepSeek has no embeddings endpoint, so pair it with local HuggingFace embeddings
(free, about 90 MB download the first time) or with OpenAI embeddings.

For Ollama: `ollama pull gemma3` and `ollama pull nomic-embed-text` first.

## Run

```bash
rag-lab ask "How many absences are allowed before I fail?"
rag-lab ask "Can I bring my dog?" --check          # adds the check node
rag-lab ask "When is the final exam?" --chunk-size 150 -k 1
rag-lab agent "I was 20 minutes late twice. Absences?"
rag-lab chat --mode agent                          # interactive
rag-lab eval                                       # score eval/questions.yaml
rag-lab eval --chunk-size 3000 --label big-chunks  # compare a setting
```

Every command prints the retrieved chunks or the agent's searches under the answer.
When an answer is wrong, read those first.

`--store chroma` (needs the `chroma` extra) keeps the index in `./chroma_db` between runs.
Delete that folder after you change documents, chunk size or embedding model.

## The documents

`data/handbook.md` is a made-up sample so the code runs before you have the real file. Put the real
`handbook.pdf` from the chapter's `rag_demo` folder in `data/`, delete the sample, and
update `eval/questions.yaml` to match. Any `.pdf`, `.md` or `.txt` in `data/` is indexed.

## Lab tasks

From slide 10. Each one is a GitHub issue; assign yourself before you start.

Lab:
1. Run the pipeline and print the retrieved chunks for "When is the final exam?"
2. Ask "Can I bring my dog?". Does the model say "I don't know"? Fix the prompt if not.
3. Set `chunk_size` to 150, then 3000. Watch the chunks and the answer change.
4. Set `k=1`. Find a question that needs two chunks and see it fail.

Going further:
1. Index a document of your choice.
2. Add a `check` node (done in `graph.py`, `--check`). Measure it on the eval set.
3. Use the agent version on a question that needs two searches.
4. Run everything locally with Ollama and compare with the cloud model.

## Working together

- Work on a branch: `git checkout -b task/chunk-size`.
- Open a pull request, let the tests pass, ask one teammate to review, then merge.
- If your change can move answer quality, paste the `rag-lab eval` score before and after.
- Never commit `.env`. If a key leaks, revoke it the same day.

## Using a coding agent

Check the course rules on AI tools and say in your report which ones you used. `AGENTS.md`
describes the project, commands and rules for any coding-agent harness, for example
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (`npx @deepseek-ai/dsh web`),
Claude Code or Codex. Start your agent in the repo root and tell it to read `AGENTS.md`.

## Tests

```bash
pytest -q
```

Tests use fake embeddings and a fake chat model. They need no key and no network.
