"""End-to-end CLI runs with fake models. CI runs these on Linux, Windows and macOS."""

from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag_lab import cli

ROOT = Path(__file__).resolve().parents[1]


def _fake_llm(monkeypatch, responses):
    monkeypatch.setattr(cli, "get_llm", lambda provider=None: FakeListChatModel(responses=responses))


def test_cli_ask_prints_answer_and_pages(monkeypatch, capsys):
    _fake_llm(monkeypatch, ["Two absences are allowed [page 3] — café ✓"])
    cli.main(["ask", "How many absences?", "--embeddings", "fake", "--docs", str(ROOT / "data")])
    out = capsys.readouterr().out
    assert "Indexed" in out
    assert "Two absences are allowed" in out
    assert "page " in out


def test_cli_eval_writes_report(monkeypatch, tmp_path, capsys):
    _fake_llm(monkeypatch, ["two"])
    cli.main(
        [
            "eval",
            "--embeddings", "fake",
            "--docs", str(ROOT / "data"),
            "--questions", str(ROOT / "eval" / "questions.yaml"),
            "--out", str(tmp_path),
            "--label", "ci: run/1",  # characters Windows forbids in file names
        ]
    )
    assert "passed" in capsys.readouterr().out
    assert len(list(tmp_path.glob("*.json"))) == 1
