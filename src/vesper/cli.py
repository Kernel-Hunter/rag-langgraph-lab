"""Command line entry point: `rag-lab --help`."""

import argparse
import sys

from vesper.indexing import index
from vesper.providers import get_embeddings, get_llm


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--docs", default="data", help="file or folder to index (default: data)")
    p.add_argument("--chunk-size", type=int, default=600)
    p.add_argument("--chunk-overlap", type=int, default=100)
    p.add_argument("-k", type=int, default=3, help="chunks to retrieve")
    p.add_argument("--store", choices=["memory", "chroma"], default="memory")
    p.add_argument("--llm", help="override LLM_PROVIDER, e.g. openai, anthropic, google, deepseek, groq, openrouter, ollama")
    p.add_argument("--embeddings", help="override EMBEDDINGS_PROVIDER: local (free, default), openai, google, mistral, ollama, fake")


def _setup(args):
    vector_store = index(
        args.docs,
        get_embeddings(args.embeddings),
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        store=args.store,
    )
    return vector_store, get_llm(args.llm)


def _graph_asker(args, vector_store, llm):
    from vesper.graph import build_graph

    graph = build_graph(vector_store, llm, k=args.k, check=getattr(args, "check", False))

    def ask(question: str) -> dict:
        state = graph.invoke({"question": question})
        state["pages"] = [d.metadata.get("page") for d in state["context"]]
        return state

    return ask


def _agent_asker(args, vector_store, llm):
    from vesper.agent import ask_agent, build_agent

    agent = build_agent(vector_store, llm, k=args.k)
    return lambda question: ask_agent(agent, question)


def _print_result(result: dict) -> None:
    print(result["answer"])
    if "supported" in result:
        print(f"\nsupported by context: {'yes' if result['supported'] else 'no'}")
    for d in result.get("context", []):  # always look!
        print("page", d.metadata.get("page"), d.page_content[:80].replace("\n", " "))
    for q in result.get("searches", []):
        print("searched:", q)


def main(argv: list[str] | None = None) -> None:
    # Windows consoles and redirected output may default to cp1252; answers can hold any Unicode.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    from dotenv import load_dotenv

    load_dotenv()  # the lab commands read keys from the repo's .env

    parser = argparse.ArgumentParser(prog="rag-lab", description="Chapter 5B RAG lab")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ask = sub.add_parser("ask", help="ask one question through the fixed 2-step graph")
    p_ask.add_argument("question")
    p_ask.add_argument("--check", action="store_true", help="add the check node")
    _common(p_ask)

    p_agent = sub.add_parser("agent", help="ask one question through the agentic RAG version")
    p_agent.add_argument("question")
    _common(p_agent)

    p_chat = sub.add_parser("chat", help="interactive loop (graph or agent)")
    p_chat.add_argument("--mode", choices=["graph", "agent"], default="graph")
    p_chat.add_argument("--check", action="store_true")
    _common(p_chat)

    p_eval = sub.add_parser("eval", help="run eval/questions.yaml and score answers")
    p_eval.add_argument("--questions", default="eval/questions.yaml")
    p_eval.add_argument("--mode", choices=["graph", "agent"], default="graph")
    p_eval.add_argument("--check", action="store_true")
    p_eval.add_argument("--out", default="eval/results", help="where to save JSON reports")
    p_eval.add_argument("--label", default=None)
    _common(p_eval)

    args = parser.parse_args(argv)
    vector_store, llm = _setup(args)

    if args.command == "ask":
        _print_result(_graph_asker(args, vector_store, llm)(args.question))
    elif args.command == "agent":
        _print_result(_agent_asker(args, vector_store, llm)(args.question))
    elif args.command == "chat":
        ask = (_agent_asker if args.mode == "agent" else _graph_asker)(args, vector_store, llm)
        while True:
            try:
                question = input("\n> ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if question in {"", "exit", "quit"}:
                break
            _print_result(ask(question))
    elif args.command == "eval":
        from vesper.evaluate import load_questions, run_eval

        ask = (_agent_asker if args.mode == "agent" else _graph_asker)(args, vector_store, llm)
        label = args.label or f"{args.mode}-cs{args.chunk_size}-k{args.k}"
        run_eval(ask, load_questions(args.questions), out_dir=args.out, label=label)


if __name__ == "__main__":
    main()
