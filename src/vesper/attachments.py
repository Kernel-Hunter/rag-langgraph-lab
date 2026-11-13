"""Files, folders and images the user attaches to a message (terminal `@path`, desktop + button).

Text files and PDFs go into the message as `<file>` blocks, a folder as a `<folder>` listing,
and images as image blocks the model can look at. Images are sent once: after the turn they
are replaced in the history by a short note, so they do not cost tokens on every later turn.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
MAX_IMAGE_BYTES = 5 * 1024 * 1024   # what the main providers accept per image
MAX_IMAGES = 8
MAX_FILE_CHARS = 60_000             # per attached file
MAX_TOTAL_CHARS = 200_000           # all attached text in one message
FILE_LINES = 2000


@dataclass
class Attached:
    blocks: list[str] = field(default_factory=list)   # <file> and <folder> text, appended to the message
    images: list[dict] = field(default_factory=list)  # image content blocks
    names: list[str] = field(default_factory=list)    # what was attached, for the UI
    problems: list[str] = field(default_factory=list)  # "name: why it was left out"

    def message(self, text: str) -> str:
        return text + ("\n\n" + "\n\n".join(self.blocks) if self.blocks else "")


def image_block(data: bytes, mime: str, name: str) -> dict:
    return {"type": "image", "base64": base64.b64encode(data).decode("ascii"), "mime_type": mime,
            "id": f"attachment:{name}"[:200]}


def attach(workspace, refs: list[str], out: Attached | None = None) -> Attached:
    """Reads each path (relative to the working directory, or absolute) into `out`."""
    from vesper.workspace import ToolError

    out = out or Attached()
    used = sum(len(b) for b in out.blocks)
    for ref in refs:
        try:
            path = workspace.resolve(ref)
            label = workspace.rel(path)
            if path.is_dir():
                body = workspace.list_files(str(path), 2)
                block = f'<folder path="{label}">\n{body}\n</folder>'
            elif not path.is_file():
                raise ToolError("not found")
            elif path.suffix.lower() in IMAGE_TYPES:
                if len(out.images) >= MAX_IMAGES:
                    raise ToolError(f"at most {MAX_IMAGES} images per message")
                if path.stat().st_size > MAX_IMAGE_BYTES:
                    raise ToolError("images must be under 5 MB")
                if workspace._is_secret(path):
                    raise ToolError("secret files are never read")
                out.images.append(image_block(path.read_bytes(), IMAGE_TYPES[path.suffix.lower()], label))
                out.names.append(label)
                continue
            else:
                body = workspace.read_file(str(path), 1, FILE_LINES)[:MAX_FILE_CHARS]
                block = f'<file path="{label}">\n{body}\n</file>'
        except ToolError as exc:
            out.problems.append(f"{ref}: {exc}")
            continue
        except OSError as exc:
            out.problems.append(f"{ref}: {exc.strerror or exc}")
            continue
        if used + len(block) > MAX_TOTAL_CHARS:
            out.problems.append(f"{ref}: the message already holds as much attached text as fits")
            continue
        used += len(block)
        out.blocks.append(block)
        out.names.append(label)
    return out


def human_content(text: str, images: list[dict]):
    """The content of the user's message: plain text, or text plus image blocks."""
    return [{"type": "text", "text": text}, *images] if images else text


def without_images(content):
    """Message content with image blocks replaced by a one-line note (sent once, then dropped)."""
    if not isinstance(content, list) or not any(isinstance(b, dict) and b.get("type") in ("image", "image_url")
                                                for b in content):
        return content
    parts = []
    for b in content:
        if isinstance(b, dict) and b.get("type") in ("image", "image_url"):
            name = str(b.get("id", "")).removeprefix("attachment:") or "image"
            parts.append({"type": "text", "text": f"\n[image {name} was shown to you in this message]"})
        else:
            parts.append(b)
    return parts
