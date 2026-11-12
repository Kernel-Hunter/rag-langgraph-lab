"""Docs mode, Agent: the model decides when to search the documents, and with which query.

Needs a chat model with tool calling.
"""

from langchain.agents import create_agent
from langchain.tools import tool

from vesper.graph import format_context
from vesper.prompts import DOCS_AGENT_PROMPT

SYSTEM_PROMPT = DOCS_AGENT_PROMPT


def build_agent(vector_store, llm, k: int = 3, recorder: list | None = None):
    """`recorder`, if given, receives (query, docs) for every search so a UI can show sources."""

    @tool
    def search_documents(query: str) -> str:
        """Search the user's documents and return the most relevant passages with their page numbers. Use it for any question the documents might answer."""
        found = vector_store.similarity_search(query, k=k)
        if recorder is not None:
            recorder.append((query, found))
        return format_context(found)

    return create_agent(llm, tools=[search_documents], system_prompt=SYSTEM_PROMPT)


def ask_agent(agent, question: str) -> dict:
    result = agent.invoke({"messages": [{"role": "user", "content": question}]})
    searches = [
        call["args"].get("query", "")
        for m in result["messages"]
        for call in (getattr(m, "tool_calls", None) or [])
    ]
    return {"answer": result["messages"][-1].content, "searches": searches}
