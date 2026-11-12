"""Docs mode, Graph: retrieve the closest chunks, then answer from them (a LangGraph graph).

The optional third node, `check`, asks the model whether the answer is supported by the
retrieved context and stores a yes/no flag.
"""

from typing import TypedDict

from langchain_core.documents import Document
from langgraph.graph import END, START, StateGraph

from vesper.prompts import DOCS_CHECK_PROMPT, DOCS_PROMPT

# The prompts live in prompts.py with all the others.
PROMPT = DOCS_PROMPT
CHECK_PROMPT = DOCS_CHECK_PROMPT


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
        # A refusal claims nothing, so it is supported. Small models often answer "no" here.
        if "don't know" in state["answer"].lower().replace("’", "'"):
            return {"supported": True}
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
