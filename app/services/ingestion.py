"""数据摄入流水线 (Ingestion Pipeline)。"""

from __future__ import annotations

import os
from typing import Any

from flask import current_app
from langchain_text_splitters import MarkdownHeaderTextSplitter

from app.models.database import db, DocumentChunk
from app.utils.embedding import get_embeddings_batch


class IngestionPipeline:
    """文档摄入流水线。"""

    def load_document(self, file_path: str) -> list[str]:
        """加载文档并返回原始文本块。"""
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            return self._load_pdf(file_path)
        if ext in {".docx", ".doc"}:
            return self._load_docx(file_path)
        if ext in {".txt", ".md"}:
            return self._load_plain_text(file_path)
        raise ValueError(f"不支持的文件类型: {ext}")

    def _load_pdf(self, file_path: str) -> list[str]:
        from pypdf import PdfReader

        reader = PdfReader(file_path)
        texts: list[str] = []
        for page in reader.pages:
            page_text = (page.extract_text() or "").strip()
            if page_text:
                texts.append(page_text)
        return texts

    def _load_docx(self, file_path: str) -> list[str]:
        from docx import Document

        doc = Document(file_path)
        texts = [p.text.strip() for p in doc.paragraphs if p.text and p.text.strip()]
        return texts

    def _load_plain_text(self, file_path: str) -> list[str]:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()
        return [content] if content else []

    def split_text(
            self, texts: list[str], file_ext: str, chunk_size: int = 800, overlap: int = 100
    ) -> list[str]:
        """按文件类型将文本切片。"""
        if file_ext == ".md":
            return self._split_markdown(texts=texts, chunk_size=chunk_size, overlap=overlap)
        return self._split_default(texts=texts, chunk_size=chunk_size, overlap=overlap)

    def _split_markdown(self, texts: list[str], chunk_size: int, overlap: int) -> list[str]:
        """Markdown 文本切片。"""
        headers_to_split_on = [
            ("#", "Header 1"),
            ("##", "Header 2"),
            ("###", "Header 3"),
            ("####", "Header 4"),
            ("#####", "Header 5"),
        ]

        splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on, strip_headers=False)
        chunks: list[str] = []
        for text in texts:
            for chunk in splitter.split_text(text):
                if chunk.page_content:
                    chunks.append(chunk.page_content)
        return chunks

    def _split_default(self, texts: list[str], chunk_size: int, overlap: int) -> list[str]:
        """默认文本切片。"""
        chunks: list[str] = []
        for text in texts:
            start = 0
            length = len(text)
            while start < length:
                end = min(start + chunk_size, length)
                chunk = text[start:end].strip()
                if chunk:
                    chunks.append(chunk)
                if end >= length:
                    break
                start = max(0, end - overlap)
        return chunks

    def embed_and_store(self, chunks: list[str], kb_id: str, document_id: str, source_filename: str) -> int:
        """向量化并存入数据库，返回存储的切片数量。"""
        if not chunks:
            return 0

        embeddings: list[list[float] | None]
        try:
            embeddings = get_embeddings_batch(chunks)
        except Exception as exc:
            # API Key 未配置或外部服务不可用时，允许先落库文本，后续可补向量。
            model = current_app.config.get("EMBEDDING_MODEL", "text-embedding-3-small")
            current_app.logger.exception(
                "Embedding 生成失败，将仅保存文本。model=%s chunk_count=%s source=%s error=%s",
                model,
                len(chunks),
                source_filename,
                exc,
            )
            embeddings = [None] * len(chunks)

        if len(embeddings) != len(chunks):
            embeddings = [None] * len(chunks)

        rows: list[DocumentChunk] = []
        for idx, chunk in enumerate(chunks):
            rows.append(
                DocumentChunk(
                    kb_id=kb_id,
                    document_id=document_id,
                    content=chunk,
                    embedding=embeddings[idx],
                    metadata_={"source": source_filename, "chunk_index": idx},
                )
            )

        db.session.add_all(rows)
        db.session.flush()
        return len(rows)

    def process(self, file_path: str, kb_id: str, document_id: str, source_filename: str) -> dict[str, Any]:
        """完整的摄入流程。"""
        texts = self.load_document(file_path)
        if not texts:
            raise ValueError("文档内容为空或无法解析。")
        ext = os.path.splitext(file_path)[1].lower()
        chunks = self.split_text(texts=texts, file_ext=ext)
        chunk_count = self.embed_and_store(
            chunks,
            kb_id=kb_id,
            document_id=document_id,
            source_filename=source_filename,
        )
        return {"chunk_count": chunk_count, "text_block_count": len(texts)}
