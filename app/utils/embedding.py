"""Embedding 工具函数。"""

from openai import OpenAI
from openai import APIError
from openai import APIStatusError
from flask import current_app


def _get_openai_client() -> OpenAI:
    api_key = current_app.config.get("OPENAI_API_KEY", "")
    base_url = current_app.config.get("OPENAI_BASE_URL", "")
    if not api_key:
        raise ValueError("OPENAI_API_KEY 未配置，无法执行向量化。")
    return OpenAI(api_key=api_key, base_url=base_url or None)


def _log_embedding_api_error(exc: Exception, model: str, context: str) -> None:
    """打印向量平台返回的详细报错信息。"""
    logger = current_app.logger
    if isinstance(exc, APIStatusError):
        logger.exception(
            "%s 失败。model=%s status_code=%s request_id=%s body=%s",
            context,
            model,
            exc.status_code,
            getattr(exc, "request_id", None),
            getattr(exc, "body", None),
        )
        return
    if isinstance(exc, APIError):
        logger.exception(
            "%s 失败。model=%s request_id=%s message=%s",
            context,
            model,
            getattr(exc, "request_id", None),
            str(exc),
        )
        return
    logger.exception("%s 失败。model=%s error=%s", context, model, str(exc))


def get_embedding(text: str) -> list[float]:
    """将文本转换为向量。"""
    if not text or not text.strip():
        raise ValueError("文本为空，无法执行向量化。")
    client = _get_openai_client()
    model = current_app.config.get("EMBEDDING_MODEL", "text-embedding-3-small")
    dimension = int(current_app.config.get("EMBEDDING_DIMENSION", 1536))
    try:
        response = client.embeddings.create(input=text, model=model, dimensions=dimension)
        return response.data[0].embedding
    except Exception as exc:
        _log_embedding_api_error(
            exc,
            model,
            f"单条向量化(text_len={len(text)}, dimensions={dimension})",
        )
        raise


def get_embeddings_batch(texts: list[str]) -> list[list[float]]:
    """批量将文本转换为向量。"""
    cleaned_texts = [text.strip() for text in texts if text and text.strip()]
    if not cleaned_texts:
        return []
    client = _get_openai_client()
    model = current_app.config.get("EMBEDDING_MODEL", "text-embedding-3-small")
    dimension = int(current_app.config.get("EMBEDDING_DIMENSION", 1536))
    try:
        response = client.embeddings.create(
            input=cleaned_texts,
            model=model,
            dimensions=dimension,
        )
        return [item.embedding for item in response.data]
    except Exception as exc:
        _log_embedding_api_error(
            exc,
            model,
            f"批量向量化(batch_size={len(cleaned_texts)}, dimensions={dimension})",
        )
        raise
