"""The book retriever as a LangChain component, so it can be dropped into any LangChain / LangGraph pipeline.

`BookRetriever` wraps this app's own chapter-scoped hybrid retrieval (skills.book.retrieve) and returns LangChain `Document`s with
page metadata. `StoreRetriever` does plain dense search through whichever vector store backend is selected (SQLite or Chroma), which
is the apples-to-apples comparison used by scripts/eval_retrieval.py."""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from langchain_core.callbacks import AsyncCallbackManagerForRetrieverRun, CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from .. import vectorstore
from ..skills import book


def _doc(h: dict) -> Document:
    return Document(page_content=h["text"], metadata={"page": h.get("page"), "page_end": h.get("page_end"), "score": h.get("score")})


class BookRetriever(BaseRetriever):
    """Chapter-scoped dense + keyword retrieval (the app's own algorithm)."""
    file_id: str
    span: Optional[tuple[int, int]] = None
    sections: list[tuple[str, int]] = []
    k: int = 8

    async def _aget_relevant_documents(self, query: str, *, run_manager: AsyncCallbackManagerForRetrieverRun) -> list[Document]:
        return [_doc(h) for h in await book.retrieve(self.file_id, query, self.span, self.sections, self.k)]

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        return [_doc(h) for h in asyncio.run(book.retrieve(self.file_id, query, self.span, self.sections, self.k))]


class StoreRetriever(BaseRetriever):
    """Dense search only, through a VectorStore backend ('sqlite' or 'chroma')."""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    scope: str
    backend: Any
    k: int = 8
    span: Optional[tuple[int, int]] = None

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        from .. import embeddings
        qv = embeddings.embed([query])["vectors"][0]
        return [_doc(h) for h in self.backend.search(self.scope, qv, self.k, self.span)]
