"""Code 2 + 3 of 3: the state, the nodes, and the graph. Slides 6 and 7.

Optional third node `check` ("Going further" task 2): asks the model whether
the answer is supported by the retrieved context and stores a yes/no flag.
"""

from typing import TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

PROMPT = (
    "You are the help desk of MEDTECH. Answer ONLY from the "
    "context. If the answer is not there, say 'I don't know'. "
    "Cite the sources you used as [page N].\n\n"
    "Context:\n{context}\n\nQuestion: {question}"
)

CHECK_PROMPT = (
    "Context:\n{context}\n\nAnswer:\n{answer}\n\n"
    "Is every claim in the answer supported by the context? "
    "An answer of 'I don't know' counts as supported. Reply with exactly one word: yes or no."
)


class State(TypedDict, total=False):  # the shared notebook
    question: str
    context: list[Document]
    answer: str
    supported: bool


def format_context(docs: list[Document]) -> str:
    return "\n\n".join(f"[page {d.metadata.get('page', '?')}] {d.page_content}" for d in docs)


def build_graph(vector_store, llm, k: int = 3, check: bool = False):
    def retrieve(state: State) -> dict:  # step 5
        found = vector_store.similarity_search(state["question"], k=k)
        return {"context": found}

    def generate(state: State) -> dict:  # steps 6 + 7
        prompt = PROMPT.format(context=format_context(state["context"]), question=state["question"])
        return {"answer": llm.invoke(prompt).content}

    def check_node(state: State) -> dict:
        prompt = CHECK_PROMPT.format(context=format_context(state["context"]), answer=state["answer"])
        verdict = str(llm.invoke(prompt).content).strip().lower()
        return {"supported": verdict.startswith("yes")}

    builder = StateGraph(State)
    builder.add_node("retrieve", retrieve)
    builder.add_node("generate", generate)
    builder.add_edge(START, "retrieve")
    builder.add_edge("retrieve", "generate")
    if check:
        builder.add_node("check", check_node)
        builder.add_edge("generate", "check")
        builder.add_edge("check", END)
    else:
        builder.add_edge("generate", END)
    return builder.compile()
