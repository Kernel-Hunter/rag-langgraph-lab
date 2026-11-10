"""Code 1 of 3: indexing (load, split, embed, store). Slide 5."""

import re
from pathlib import Path

import pypdf
from langchain_core.documents import Document
from langchain_core.vectorstores import InMemoryVectorStore
from langchain_text_splitters import RecursiveCharacterTextSplitter

PAGE_MARKER = re.compile(r"^<!--\s*page\s+(\d+)\s*-->\s*$", re.MULTILINE)


def _load_pdf(path: Path) -> list[Document]:
    reader = pypdf.PdfReader(str(path))
    return [
        Document(
            page_content=page.extract_text() or "",
            metadata={"source": path.name, "page": i + 1},
        )
        for i, page in enumerate(reader.pages)
    ]


def _load_text(path: Path) -> list[Document]:
    """Text/Markdown files. `<!-- page N -->` lines act as page breaks so citations work."""
    text = path.read_text(encoding="utf-8")
    parts = PAGE_MARKER.split(text)
    if len(parts) == 1:
        return [Document(page_content=text, metadata={"source": path.name, "page": 1})]
    docs = []
    # parts = [before_first_marker, n1, body1, n2, body2, ...]
    for number, body in zip(parts[1::2], parts[2::2]):
        if body.strip():
            docs.append(
                Document(page_content=body.strip(), metadata={"source": path.name, "page": int(number)})
            )
    return docs


def load_documents(path: str | Path) -> list[Document]:
    """Step 1. One Document per page, with metadata to cite later."""
    path = Path(path)
    files = sorted(p for p in path.rglob("*") if p.is_file()) if path.is_dir() else [path]
    docs: list[Document] = []
    for f in files:
        if f.suffix.lower() == ".pdf":
            docs.extend(_load_pdf(f))
        elif f.suffix.lower() in {".md", ".txt"}:
            docs.extend(_load_text(f))
    if not docs:
        raise FileNotFoundError(f"No .pdf, .md or .txt documents found at {path}")
    return docs


def split_documents(docs: list[Document], chunk_size: int = 600, chunk_overlap: int = 100) -> list[Document]:
    """Step 2. Paragraph-sized chunks with an overlap. Sizes are in characters."""
    splitter = RecursiveCharacterTextSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    return splitter.split_documents(docs)


def build_vector_store(chunks: list[Document], embeddings, store: str = "memory", persist_dir: str = "./chroma_db"):
    """Steps 3 + 4. Embed and store. The store calls the embedding model."""
    if store == "chroma":
        from langchain_chroma import Chroma  # pip install -e ".[chroma]"

        vector_store = Chroma(
            collection_name="handbook",
            embedding_function=embeddings,
            persist_directory=persist_dir,
        )
        # Index once; next run opens the folder and skips add_documents.
        # Delete ./chroma_db after changing documents, chunk size or embeddings.
        if not vector_store.get(limit=1)["ids"]:
            vector_store.add_documents(chunks)
        return vector_store

    vector_store = InMemoryVectorStore(embeddings)
    vector_store.add_documents(chunks)
    return vector_store


def index(path, embeddings, chunk_size: int = 600, chunk_overlap: int = 100, store: str = "memory", verbose: bool = True):
    docs = load_documents(path)
    chunks = split_documents(docs, chunk_size, chunk_overlap)
    vector_store = build_vector_store(chunks, embeddings, store=store)
    if verbose:
        print(f"Indexed {len(chunks)} chunks from {len(docs)} pages.")
    return vector_store
