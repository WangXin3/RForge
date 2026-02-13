import json

from flask import Blueprint, request, Response, stream_with_context

from app.services.retrieval import RetrievalService
from app.utils.api_response import error, success

chat_bp = Blueprint("chat", __name__)


@chat_bp.route("/v1/chat/completions", methods=["POST"])
def chat_completions():
    """RAG 增强检索问答

    接收用户问题和知识库 ID 列表，检索相关片段并生成回答。
    """
    data = request.get_json()

    if not data or "query" not in data:
        return error(message="缺少 query 参数")

    query = str(data["query"]).strip()
    if not query:
        return error(message="query 不能为空")

    kb_ids = data.get("kb_ids", [])
    if kb_ids is None:
        kb_ids = []
    if not isinstance(kb_ids, list):
        return error(message="kb_ids 必须是数组")

    top_k = data.get("top_k", 5)
    try:
        top_k = int(top_k)
    except (TypeError, ValueError):
        return error(message="top_k 必须是整数")
    if top_k < 1 or top_k > 20:
        return error(message="top_k 必须在 1-20 之间")

    stream = data.get("stream", True)
    if not isinstance(stream, bool):
        return error(message="stream 必须是布尔值")

    try:
        service = RetrievalService()

        if not stream:
            result = service.query(query=query, kb_ids=kb_ids, top_k=top_k)
            return success(
                message="问答成功",
                data={
                    "query": query,
                    "kb_ids": kb_ids,
                    "top_k": top_k,
                    "answer": result["answer"],
                    "references": result["references"],
                },
            )

        stream_result = service.stream_query(query=query, kb_ids=kb_ids, top_k=top_k)
        references = stream_result["references"]
        token_stream = stream_result["token_stream"]

        def to_sse(payload: dict) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        @stream_with_context
        def event_stream():
            yield to_sse(
                {
                    "type": "meta",
                    "query": query,
                    "kb_ids": kb_ids,
                    "top_k": top_k,
                }
            )
            for token in token_stream:
                yield to_sse({"type": "delta", "content": token})
            yield to_sse({"type": "references", "references": references})
            yield to_sse({"type": "done"})

        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
        return Response(event_stream(), mimetype="text/event-stream", headers=headers)
    except Exception as exc:
        return error(message=f"问答失败: {str(exc)}", code=500)
