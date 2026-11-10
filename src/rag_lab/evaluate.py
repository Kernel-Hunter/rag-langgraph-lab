"""Eval harness: run a fixed question set and score the answers.

Use it to compare settings (chunk_size, k, provider, graph vs agent) with numbers
instead of impressions. Questions live in eval/questions.yaml.
"""

import json
import re
import time
from pathlib import Path

import yaml

IDK_MARKERS = ("i don't know", "i do not know", "i don’t know")


def load_questions(path: str | Path) -> list[dict]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))["questions"]


def score(item: dict, answer: str) -> bool:
    text = answer.lower()
    if item.get("expect_idk"):
        return any(m in text for m in IDK_MARKERS)
    expected = [e.lower() for e in item.get("expect_any", [])]
    return any(e in text for e in expected) and not any(m in text for m in IDK_MARKERS)


def run_eval(ask, questions: list[dict], out_dir: str | Path | None = None, label: str = "run") -> dict:
    """`ask(question) -> dict` must return at least {"answer": str}."""
    rows = []
    for item in questions:
        started = time.perf_counter()
        result = ask(item["question"])
        rows.append(
            {
                "id": item.get("id", item["question"][:30]),
                "question": item["question"],
                "answer": result["answer"],
                "pages": result.get("pages", []),
                "passed": score(item, result["answer"]),
                "seconds": round(time.perf_counter() - started, 2),
            }
        )
    passed = sum(r["passed"] for r in rows)
    report = {"label": label, "passed": passed, "total": len(rows), "rows": rows}

    for r in rows:
        mark = "PASS" if r["passed"] else "FAIL"
        pages = f" pages={r['pages']}" if r["pages"] else ""
        print(f"[{mark}] {r['id']}{pages}\n       {r['answer'][:160].replace(chr(10), ' ')}")
    print(f"\n{label}: {passed}/{len(rows)} passed")

    if out_dir:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^A-Za-z0-9._-]+", "_", label)  # valid file name on Windows too
        path = out / f"{time.strftime('%Y%m%d-%H%M%S')}-{safe_label}.json"
        path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Saved {path}")
    return report
