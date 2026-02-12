"""RAG 检索逻辑

用户提问后：
1. 向量化用户问题
2. 在 pgvector 中进行相似度检索
3. 将检索到的内容和问题送入 LLM 生成回答
"""

from __future__ import annotations

from openai import OpenAI
from flask import current_app

from app.models.database import DocumentChunk
from app.utils.embedding import get_embedding


class RetrievalService:
    """RAG 检索服务"""

    def _get_openai_client(self) -> OpenAI:
        api_key = current_app.config.get("OPENAI_API_KEY", "")
        base_url = current_app.config.get("OPENAI_BASE_URL", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 未配置，无法生成回答。")
        return OpenAI(api_key=api_key, base_url=base_url or None)

    def retrieve(self, query: str, kb_ids: list[str], top_k: int = 5) -> list[dict]:
        """根据用户问题检索相关文档片段

        使用余弦距离在 pgvector 中检索最相似的文档切片。
        """
        query_embedding = get_embedding(query)

        stmt = DocumentChunk.query.filter(DocumentChunk.embedding.isnot(None))
        if kb_ids:
            stmt = stmt.filter(DocumentChunk.kb_id.in_(kb_ids))

        rows = (
            stmt.order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
            .limit(top_k)
            .all()
        )

        return [row.to_dict() for row in rows]

    def generate_answer(self, query: str, contexts: list[dict]) -> str:
        """根据检索到的上下文生成回答"""
        if not contexts:
            return "未检索到相关知识片段，请确认已上传文档并完成向量化。"

        context_blocks: list[str] = []
        for idx, item in enumerate(contexts, start=1):
            content = (item.get("content") or "").strip()
            context_blocks.append(f"[片段{idx}] {content}")
        context_text = "\n\n".join(context_blocks)

        prompt = (
            "你是一个知识库问答助手。请仅基于提供的上下文回答，"
            "若上下文信息不足请明确说明。回答使用中文，尽量简洁。\n\n"
            f"用户问题：{query}\n\n"
            f"上下文：\n{context_text}"
        )

        try:
            client = self._get_openai_client()
            model = current_app.config.get("LLM_MODEL", "gpt-4o")
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            current_app.logger.exception("LLM 生成失败，使用降级回答。error=%s", exc)
            return (
                "LLM 生成失败，以下为检索到的相关片段摘要：\n\n"
                + "\n".join(f"- {(c.get('content') or '')[:180]}" for c in contexts)
            )

    def stream_answer(self, query: str, contexts: list[dict]):
        """根据检索上下文流式生成回答。"""
        if not contexts:
            yield "未检索到相关知识片段，请确认已上传文档并完成向量化。"
            return

        context_blocks: list[str] = []
        for idx, item in enumerate(contexts, start=1):
            content = (item.get("content") or "").strip()
            context_blocks.append(f"[片段{idx}] {content}")
        context_text = "\n\n".join(context_blocks)

        prompt = (
            "你是一个知识库问答助手。请仅基于提供的上下文回答，"
            "若上下文信息不足请明确说明。回答使用中文，尽量简洁。\n\n"
            f"用户问题：{query}\n\n"
            f"上下文：\n{context_text}"
        )

        try:
            client = self._get_openai_client()
            model = current_app.config.get("LLM_MODEL", "gpt-4o")
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                stream=True,
            )
            for chunk in stream:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                delta = getattr(choices[0], "delta", None)
                token = getattr(delta, "content", None) if delta else None
                if token:
                    yield token
        except Exception as exc:
            current_app.logger.exception("LLM 流式生成失败，使用降级回答。error=%s", exc)
            yield (
                "LLM 流式生成失败，以下为检索到的相关片段摘要：\n\n"
                + "\n".join(f"- {(c.get('content') or '')[:180]}" for c in contexts)
            )

    def query(self, query: str, kb_ids: list[str], top_k: int = 5) -> dict:
        """完整的 RAG 查询流程"""
        contexts = self.retrieve(query, kb_ids, top_k=top_k)
        answer = self.generate_answer(query, contexts)
        return {
            "answer": answer,
            "references": contexts,
        }

    def stream_query(self, query: str, kb_ids: list[str], top_k: int = 5) -> dict:
        """完整的 RAG 查询流程（流式）。"""
        contexts = self.retrieve(query, kb_ids, top_k=top_k)
        return {
            "token_stream": self.stream_answer(query, contexts),
            "references": contexts,
        }
