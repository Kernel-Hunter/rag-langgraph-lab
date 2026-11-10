# Contributing to Vesper

Thanks for helping. Small, focused pull requests get merged fastest.

## Set up

```bash
git clone https://github.com/Kernel-Hunter/vesper.git && cd vesper
uv venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"
pytest -q
```

The tests run offline with fake models, so you need no API key. If you touch the
interface, also run:

```bash
npm --prefix apps/web install
npm --prefix apps/web run typecheck && npm --prefix apps/web test && npm --prefix apps/web run build
```

Commit the rebuilt `src/vesper/web/` together with your change; CI checks that it matches.

## Where things are

[AGENTS.md](AGENTS.md) has the map of the code and the rules the project follows. The short
version:

- Folder tools and their safety checks: `src/vesper/workspace.py`. Any new tool that writes or
  runs something must go through `_gate`, and any path must go through `resolve`.
- The agent loop: `src/vesper/coder.py`. The terminal app (`tui.py`) and the desktop engine
  (`server.py`) both draw its events, so a new event type needs both.
- A new model provider goes in `src/vesper/providers.py` only.

## Pull requests

1. One change per branch and per pull request.
2. Add or update a test for what you changed.
3. If the change can affect answer quality in Docs mode (prompt, chunk size, `k`, model),
   run `vesper eval` before and after and put both scores in the pull request.
4. Describe what you changed and how you checked it. Screenshots help for UI changes.

## Reporting bugs

Open an issue with what you ran, what you expected and what happened. Include
`vesper --version`, your operating system and the model provider. Leave API keys out of logs.

Security problems go through [SECURITY.md](SECURITY.md), not public issues.
