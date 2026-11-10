"""One object the desktop app talks to: build the index once, then ask questions."""

from dataclasses import dataclass

from vesper.indexing import build_vector_store, load_documents, split_documents
from vesper.providers import get_embeddings, get_llm


@dataclass
class Settings:
    docs: str = "data"
    llm_provider: str | None = None  # None = read .env
    llm_model: str | None = None
    embeddings_provider: str | None = None
    embeddings_model: str | None = None
    chunk_size: int = 600
    chunk_overlap: int = 100
    k: int = 3
    mode: str = "graph"  # graph | agent
    check: bool = False


class HelpDesk:
    def __init__(self, settings: Settings, llm=None, embeddings=None):
        self.settings = settings
        embeddings = embeddings or get_embeddings(settings.embeddings_provider, settings.embeddings_model)
        docs = load_documents(settings.docs)
        chunks = split_documents(docs, settings.chunk_size, settings.chunk_overlap)
        self.page_count = len(docs)
        self.chunk_count = len(chunks)
        self.vector_store = build_vector_store(chunks, embeddings)
        self.llm = llm or get_llm(settings.llm_provider, settings.llm_model)
        self._graph = None
        self._agent = None

    def ask(self, question: str) -> dict:
        s = self.settings
        if s.mode == "agent":
            from vesper.agent import ask_agent, build_agent

            self._agent = self._agent or build_agent(self.vector_store, self.llm, k=s.k)
            result = ask_agent(self._agent, question)
            return {"answer": result["answer"], "sources": [], "searches": result["searches"], "supported": None}

        from vesper.graph import build_graph

        self._graph = self._graph or build_graph(self.vector_store, self.llm, k=s.k, check=s.check)
        state = self._graph.invoke({"question": question})
        return {
            "answer": state["answer"],
            "sources": [{"page": d.metadata.get("page"), "text": d.page_content} for d in state["context"]],
            "searches": [],
            "supported": state.get("supported"),
        }
