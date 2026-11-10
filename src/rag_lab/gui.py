"""Desktop app: `rag-lab-app`. Same pipeline as the CLI, in a window.

Model calls run in a worker thread so the window never freezes; results come
back to the Tk main thread through a queue.
"""

import os
import queue
import threading
import traceback
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from rag_lab.providers import EMBEDDING_DEFAULTS, LLM_DEFAULTS
from rag_lab.service import HelpDesk, Settings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DOCS = PROJECT_ROOT / "data" if (PROJECT_ROOT / "data").is_dir() else Path.cwd() / "data"
KEY_VARS = {"openai": "OPENAI_API_KEY", "deepseek": "DEEPSEEK_API_KEY"}


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("RAG Help Desk")
        self.geometry("1100x720")
        self.minsize(820, 520)
        ctk.set_appearance_mode("system")

        self.desk: HelpDesk | None = None
        self.busy = False
        self.index_info = ""
        self.results: queue.Queue = queue.Queue()

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self._build_chat()
        self.after(100, self._poll)
        self.after(200, self.rebuild)

    # ---------- layout ----------
    def _build_sidebar(self):
        side = ctk.CTkScrollableFrame(self, width=270, corner_radius=0)
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_columnconfigure(0, weight=1)
        pad = {"padx": 16, "sticky": "ew"}
        row = 0

        def place(widget, pady=(0, 2)):
            nonlocal row
            widget.grid(row=row, column=0, pady=pady, **pad)
            row += 1
            return widget

        def label(text):
            place(ctk.CTkLabel(side, text=text, anchor="w"), pady=(10, 2))

        place(ctk.CTkLabel(side, text="RAG Help Desk", anchor="w", font=ctk.CTkFont(size=20, weight="bold")), pady=(18, 0))
        place(ctk.CTkLabel(side, text="LangChain + LangGraph, Chapter 5B", anchor="w", text_color="gray"), pady=(0, 8))

        label("Documents folder")
        self.docs_var = ctk.StringVar(value=str(DEFAULT_DOCS))
        place(ctk.CTkEntry(side, textvariable=self.docs_var))
        place(ctk.CTkButton(side, text="Choose folder…", command=self._pick_folder, fg_color="gray40", hover_color="gray30"))

        label("Chat model")
        self.llm_var = ctk.StringVar(value=os.getenv("LLM_PROVIDER", "openai"))
        place(ctk.CTkOptionMenu(side, variable=self.llm_var, values=list(LLM_DEFAULTS), command=self._on_llm_change))
        self.llm_model = place(ctk.CTkEntry(side, placeholder_text="model (provider default)"))
        if os.getenv("LLM_MODEL"):
            self.llm_model.insert(0, os.getenv("LLM_MODEL"))

        label("API key")
        self.key_entry = place(ctk.CTkEntry(side, show="•", placeholder_text="not needed for ollama"))
        self._on_llm_change(self.llm_var.get())

        label("Embeddings")
        self.emb_var = ctk.StringVar(value=os.getenv("EMBEDDINGS_PROVIDER", "openai"))
        place(ctk.CTkOptionMenu(side, variable=self.emb_var, values=list(EMBEDDING_DEFAULTS)))
        self.emb_model = place(ctk.CTkEntry(side, placeholder_text="model (provider default)"))
        if os.getenv("EMBEDDINGS_MODEL"):
            self.emb_model.insert(0, os.getenv("EMBEDDINGS_MODEL"))

        label("Chunk size  /  k (chunks retrieved)")
        nums = place(ctk.CTkFrame(side, fg_color="transparent"))
        nums.grid_columnconfigure((0, 1), weight=1)
        self.chunk_entry = ctk.CTkEntry(nums)
        self.chunk_entry.insert(0, "600")
        self.chunk_entry.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self.k_entry = ctk.CTkEntry(nums)
        self.k_entry.insert(0, "3")
        self.k_entry.grid(row=0, column=1, sticky="ew", padx=(4, 0))

        label("Mode")
        self.mode_var = ctk.StringVar(value="Graph")
        place(ctk.CTkSegmentedButton(side, values=["Graph", "Agent"], variable=self.mode_var))
        self.check_var = ctk.BooleanVar(value=False)
        place(ctk.CTkSwitch(side, text="Check node (graph mode)", variable=self.check_var), pady=(12, 2))

        self.apply_btn = place(ctk.CTkButton(side, text="Apply and re-index", command=self.rebuild), pady=(18, 6))
        self.status = place(ctk.CTkLabel(side, text="", text_color="gray", wraplength=230, justify="left", anchor="w"))

    def _build_chat(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.grid(row=0, column=1, sticky="nsew", padx=12, pady=12)
        main.grid_columnconfigure(0, weight=1)
        main.grid_rowconfigure(0, weight=1)

        self.chat = ctk.CTkScrollableFrame(main)
        self.chat.grid(row=0, column=0, columnspan=2, sticky="nsew")
        self.chat.grid_columnconfigure(0, weight=1)
        self.chat_row = 0

        self.entry = ctk.CTkEntry(main, placeholder_text="Ask about the course…", height=40)
        self.entry.grid(row=1, column=0, sticky="ew", pady=(10, 0), padx=(0, 8))
        self.entry.bind("<Return>", lambda _e: self.send())
        self.send_btn = ctk.CTkButton(main, text="Send", width=90, height=40, command=self.send)
        self.send_btn.grid(row=1, column=1, pady=(10, 0))

    # ---------- chat bubbles ----------
    def _bubble(self, text, who):
        fg = {
            "user": ("#2563eb", "#1d4ed8"),
            "bot": ("gray88", "gray22"),
            "info": "transparent",
            "error": ("#fde2e2", "#5c1d1d"),
        }[who]
        text_color = {"user": "white", "info": "gray"}.get(who, ("gray10", "gray92"))
        bubble = ctk.CTkLabel(
            self.chat, text=text, fg_color=fg, text_color=text_color, corner_radius=12,
            wraplength=620, justify="left", anchor="w", padx=12, pady=8,
        )
        bubble.grid(row=self.chat_row, column=0, sticky="e" if who == "user" else "w", padx=8, pady=4)
        self.chat_row += 1
        self.after(60, lambda: self.chat._parent_canvas.yview_moveto(1.0))
        return bubble

    def _sources(self, result):
        lines = []
        if result.get("supported") is not None:
            lines.append("✓ supported by the context" if result["supported"] else "⚠ not supported by the context")
        for s in result.get("sources", []):
            lines.append(f"page {s['page']}: {s['text'][:110].replace(chr(10), ' ')}…")
        for q in result.get("searches", []):
            lines.append(f"searched: {q}")
        if self.desk and self.desk.settings.mode == "agent" and not result.get("searches"):
            lines.append("answered without searching")
        if lines:
            self._bubble("\n".join(lines), "info")

    # ---------- actions ----------
    def _pick_folder(self):
        folder = filedialog.askdirectory(initialdir=self.docs_var.get())
        if folder:
            self.docs_var.set(folder)

    def _on_llm_change(self, provider):
        var = KEY_VARS.get(provider)
        self.key_entry.configure(state="normal")
        self.key_entry.delete(0, "end")
        if var and os.getenv(var):
            self.key_entry.insert(0, os.getenv(var))
        if not var:
            self.key_entry.configure(state="disabled")

    def _settings(self) -> Settings:
        return Settings(
            docs=self.docs_var.get(),
            llm_provider=self.llm_var.get(),
            llm_model=self.llm_model.get().strip() or None,
            embeddings_provider=self.emb_var.get(),
            embeddings_model=self.emb_model.get().strip() or None,
            chunk_size=int(self.chunk_entry.get()),
            k=int(self.k_entry.get()),
            mode=self.mode_var.get().lower(),
            check=self.check_var.get(),
        )

    def _set_busy(self, busy, message):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.send_btn.configure(state=state)
        self.apply_btn.configure(state=state)
        self.status.configure(text=message)

    def _run(self, kind, fn):
        def worker():
            try:
                self.results.put((kind, fn()))
            except Exception as exc:  # shown in the window, not swallowed
                traceback.print_exc()
                self.results.put(("error", f"{type(exc).__name__}: {exc}"))

        threading.Thread(target=worker, daemon=True).start()

    def rebuild(self):
        if self.busy:
            return
        try:
            settings = self._settings()
        except ValueError:
            self._bubble("Chunk size and k must be whole numbers.", "error")
            return
        var = KEY_VARS.get(settings.llm_provider)
        if var and self.key_entry.get().strip():
            os.environ[var] = self.key_entry.get().strip()  # this session only, never written to disk
        if settings.embeddings_provider == "openai" and not os.getenv("OPENAI_API_KEY"):
            self._bubble(
                "OpenAI embeddings need OPENAI_API_KEY. Pick ollama or huggingface embeddings, or add the key to .env.",
                "error",
            )
            return
        self._set_busy(True, "Indexing documents…")
        self._run("ready", lambda: HelpDesk(settings))

    def send(self):
        question = self.entry.get().strip()
        if not question or self.busy or self.desk is None:
            return
        # Mode and the check switch apply without re-indexing.
        self.desk.settings.mode = self.mode_var.get().lower()
        if self.desk.settings.check != self.check_var.get():
            self.desk.settings.check = self.check_var.get()
            self.desk._graph = None
        self.entry.delete(0, "end")
        self._bubble(question, "user")
        self._set_busy(True, "Thinking…")
        self._run("answer", lambda: self.desk.ask(question))

    def _poll(self):
        try:
            while True:
                kind, payload = self.results.get_nowait()
                if kind == "ready":
                    self.desk = payload
                    s = payload.settings
                    self.index_info = (
                        f"Indexed {payload.chunk_count} chunks from {payload.page_count} pages.\n"
                        f"Chat: {s.llm_provider}   Embeddings: {s.embeddings_provider}"
                    )
                    self._set_busy(False, self.index_info)
                    self._bubble(f"Ready. Indexed {payload.chunk_count} chunks from {payload.page_count} pages. Ask a question.", "info")
                    self.entry.focus_set()
                elif kind == "answer":
                    self._set_busy(False, self.index_info)
                    self._bubble(payload["answer"], "bot")
                    self._sources(payload)
                else:
                    self._set_busy(False, self.index_info)
                    self._bubble(payload, "error")
        except queue.Empty:
            pass
        self.after(100, self._poll)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
