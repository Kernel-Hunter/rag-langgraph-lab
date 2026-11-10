"""Offline tests: fake embeddings and a fake chat model, so no API key is needed."""

from pathlib import Path

from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from rag_lab.evaluate import run_eval, score
from rag_lab.graph import build_graph
from rag_lab.indexing import build_vector_store, load_documents, split_documents

ROOT = Path(__file__).resolve().parents[1]
HANDBOOK = ROOT / "data" / "handbook.md"


def _store(chunk_size=600):
    chunks = split_documents(load_documents(HANDBOOK), chunk_size=chunk_size, chunk_overlap=50)
    return build_vector_store(chunks, DeterministicFakeEmbedding(size=64)), chunks


def test_markdown_pages_become_documents():
    docs = load_documents(HANDBOOK)
    assert [d.metadata["page"] for d in docs] == [1, 2, 3, 4]
    assert "Two absences are allowed" in docs[2].page_content


def test_smaller_chunks_mean_more_chunks():
    _, big = _store(chunk_size=3000)
    _, small = _store(chunk_size=150)
    assert len(small) > len(big)
    assert all(c.metadata["page"] in {1, 2, 3, 4} for c in small)


def test_graph_returns_answer_and_context():
    store, _ = _store()
    llm = FakeListChatModel(responses=["Two absences are allowed [page 3]."])
    result = build_graph(store, llm, k=2).invoke({"question": "How many absences?"})
    assert result["answer"].startswith("Two absences")
    assert len(result["context"]) == 2
    assert "supported" not in result


def test_check_node_sets_flag():
    store, _ = _store()
    llm = FakeListChatModel(responses=["Two absences [page 3].", "yes"])
    result = build_graph(store, llm, check=True).invoke({"question": "How many absences?"})
    assert result["supported"] is True


def test_check_node_accepts_refusal_without_asking():
    store, _ = _store()
    llm = FakeListChatModel(responses=["I don't know.", "no"])  # "no" must never be read
    result = build_graph(store, llm, check=True).invoke({"question": "Can I bring my dog?"})
    assert result["supported"] is True


def test_score_rules():
    assert score({"expect_any": ["week 13"]}, "The exam is in Week 13 [page 2].")
    assert not score({"expect_any": ["week 13"]}, "I don't know.")
    assert score({"expect_idk": True}, "I don't know.")
    assert not score({"expect_idk": True}, "Yes, dogs are welcome.")


def test_run_eval_counts(tmp_path):
    answers = {"q1": "two", "q2": "I don't know"}
    report = run_eval(
        lambda q: {"answer": answers[q]},
        [{"id": "a", "question": "q1", "expect_any": ["two"]}, {"id": "b", "question": "q2", "expect_idk": True}],
        out_dir=tmp_path,
        label="test",
    )
    assert report["passed"] == 2
    assert len(list(tmp_path.glob("*.json"))) == 1
