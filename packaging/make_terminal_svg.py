"""Draw docs/terminal.svg, the terminal screenshot in the README.

It uses the real drawing code from src/vesper/ui.py (banner, tool lines, approval panel)
with a scripted session, so the picture stays true to what `vesper` prints.
Run after changing the terminal UI: python packaging/make_terminal_svg.py
"""

from pathlib import Path

from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

from vesper import ui

OUT = Path(__file__).resolve().parents[1] / "docs" / "terminal.svg"

DIFF = """--- a/src/budget.py
+++ b/src/budget.py
@@ -12,7 +12,7 @@ def monthly_total(rows):
     total = 0
     for row in rows:
-        total =+ row.amount
+        total += row.amount
     return total
"""


def main() -> None:
    con = Console(record=True, width=92, force_terminal=True, color_system="truecolor", highlight=False)
    ui.console = con
    say = con.print

    ui.banner(Path.home() / "projects" / "budget-app", {"provider": "anthropic", "model": "claude-sonnet-5-5"}, "ask")
    say()
    say(Text.assemble(("❯ ", f"bold {ui.ACCENT}"), "the monthly total test fails, fix it"))
    say()
    steps = [
        ("run_command", {"command": "pytest -q"}, "exit code 1\n1 failed, 11 passed in 0.31s"),
        ("grep", {"pattern": "def monthly_total"}, "src/budget.py:10: def monthly_total(rows):"),
        ("read_file", {"path": "src/budget.py"}, "\n".join(f"{i:6}\tx" for i in range(1, 41))),
    ]
    for name, args, out in steps:
        say(Text.assemble(("● ", ui.ACCENT), (ui.TOOL_LABELS.get(name, name), "bold"),
                          ("  " + ui.summarize_args(name, args), ui.MUTED)))
        say(Text("  ⎿  ", style=ui.MUTED) + ui.summarize_output(name, out, True))
    say(Markdown("Line 14 uses `=+`, which assigns instead of adding. Fixing it:"))
    say(Text.assemble(("● ", ui.ACCENT), ("Edit", "bold"), ("  src/budget.py", ui.MUTED)))
    say(Panel(Syntax(DIFF, "diff", theme="ansi_dark", word_wrap=True), title="Edit src/budget.py",
              title_align="left", border_style=ui.WARN, box=box.ROUNDED))
    say(Text.assemble(("  Allow?  ", "bold"), ("y", f"bold {ui.OK}"), (" yes  ", ui.MUTED),
                      ("a", f"bold {ui.ACCENT}"), (" always allow edits this session  ", ui.MUTED),
                      ("n", f"bold {ui.ERR}"), (" no  ", ui.MUTED), ("[y] ", ui.MUTED), "y"))
    say(Text("  ⎿  ", style=ui.MUTED) + Text("Edited src/budget.py", style=ui.MUTED))
    say(Text.assemble(("● ", ui.ACCENT), ("Run", "bold"), ("  pytest -q", ui.MUTED)))
    say(Text("  ⎿  ", style=ui.MUTED) + ui.summarize_output("run_command", "exit code 0\n12 passed in 0.29s", True))
    say(Markdown("Fixed. `monthly_total` added with `=+`, so it kept only the last row. "
                 "All 12 tests pass now."))
    say(ui.turn_footer({"seconds": 14.2, "tools": 5, "input": 6200, "output": 410, "context": 9800}, 200_000))

    OUT.parent.mkdir(exist_ok=True)
    con.save_svg(str(OUT), title="vesper")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    main()
