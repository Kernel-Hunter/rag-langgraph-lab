"""Drive the desktop window with fake models. Skipped when there is no display."""

import time
import tkinter

import pytest
from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag_lab import gui
from rag_lab.service import HelpDesk, Settings


@pytest.fixture
def app(monkeypatch):
    def fake_desk(settings: Settings):
        return HelpDesk(
            settings,
            llm=FakeListChatModel(responses=["The final exam is in week 13 [page 2]."]),
            embeddings=DeterministicFakeEmbedding(size=32),
        )

    monkeypatch.setattr(gui, "HelpDesk", fake_desk)
    try:
        window = gui.App()
    except tkinter.TclError as exc:
        pytest.skip(f"no display: {exc}")
    window.emb_var.set("fake")
    window.llm_var.set("ollama")  # no key needed; the fake model replaces it anyway
    yield window
    window.destroy()


def _pump(window, until, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        window.update()
        if until():
            return
        time.sleep(0.02)
    raise AssertionError("timed out waiting for the window")


def _texts(window):
    return [w.cget("text") for w in window.chat.winfo_children()]


def test_window_indexes_then_answers(app):
    app.desk = None
    app.rebuild()
    _pump(app, lambda: app.desk is not None)
    assert any("Indexed" in t for t in _texts(app))

    app.entry.insert(0, "When is the final exam?")
    app.send()
    _pump(app, lambda: any("week 13" in t for t in _texts(app)))
    texts = _texts(app)
    assert "When is the final exam?" in texts
    assert any(t.startswith("page ") for t in texts)  # sources shown under the answer
    assert app.send_btn.cget("state") == "normal"
