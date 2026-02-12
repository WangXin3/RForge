import os
import uuid

from flask import Blueprint, current_app, request, jsonify
from sqlalchemy import func
from werkzeug.utils import secure_filename

from app.models.database import (
    db,
    init_database,
    DocumentChunk,
    KnowledgeBase,
    KnowledgeDocument,
)
from app.services.ingestion import IngestionPipeline

kb_bp = Blueprint("kb", __name__)


def _get_or_create_kb_for_upload(kb_id: str, kb_name: str, user_id: str) -> tuple[KnowledgeBase | None, tuple | None]:
    if kb_id:
        kb = KnowledgeBase.query.filter_by(id=kb_id).first()
        if kb is None:
            return None, (jsonify({"error": "知识库不存在"}), 404)
        return kb, None

    if not kb_name:
        return None, (jsonify({"error": "缺少 kb_id 或 kb_name 参数"}), 400)

    kb = (
        KnowledgeBase.query.filter_by(name=kb_name, user_id=user_id)
        .order_by(KnowledgeBase.created_at.desc())
        .first()
    )
    if kb is None:
        kb = KnowledgeBase(name=kb_name, user_id=user_id)
        db.session.add(kb)
        db.session.flush()
    return kb, None


@kb_bp.route("/v1/kb", methods=["POST"])
def create_kb():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    user_id = (data.get("user_id") or "system").strip()

    if not name:
        return jsonify({"error": "name 不能为空"}), 400

    try:
        init_database()
        kb = KnowledgeBase(name=name, user_id=user_id)
        db.session.add(kb)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("创建知识库失败: %s", exc)
        return jsonify({"error": f"创建知识库失败: {str(exc)}"}), 500

    return jsonify({"message": "知识库创建成功", "kb": kb.to_dict()}), 200


@kb_bp.route("/v1/kb", methods=["GET"])
def list_kbs():
    user_id = (request.args.get("user_id") or "").strip()
    try:
        init_database()
        stmt = KnowledgeBase.query
        if user_id:
            stmt = stmt.filter(KnowledgeBase.user_id == user_id)
        kbs = stmt.order_by(KnowledgeBase.created_at.desc()).all()
    except Exception as exc:
        current_app.logger.exception("查询知识库列表失败: %s", exc)
        return jsonify({"error": f"查询知识库失败: {str(exc)}"}), 500

    return jsonify({"items": [kb.to_dict() for kb in kbs]}), 200


@kb_bp.route("/v1/kb/<kb_id>", methods=["DELETE"])
def delete_kb(kb_id: str):
    try:
        init_database()
        kb = KnowledgeBase.query.filter_by(id=kb_id).first()
        if kb is None:
            return jsonify({"error": "知识库不存在"}), 404
        db.session.delete(kb)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("删除知识库失败: %s", exc)
        return jsonify({"error": f"删除知识库失败: {str(exc)}"}), 500

    return jsonify({"message": "知识库删除成功", "kb_id": kb_id}), 200


@kb_bp.route("/v1/kb/upload", methods=["POST"])
def upload_document():
    """上传文档到知识库

    接收 file + kb_id 或 file + kb_name，解析文档并存入向量数据库。
    """
    if "file" not in request.files:
        return jsonify({"error": "未提供文件"}), 400

    file = request.files["file"]
    kb_id = (request.form.get("kb_id") or "").strip()
    kb_name = (request.form.get("kb_name") or "").strip()
    user_id = (request.form.get("user_id") or "system").strip()

    if file.filename == "":
        return jsonify({"error": "文件名为空"}), 400
    if not kb_id and not kb_name:
        return jsonify({"error": "缺少 kb_id 或 kb_name 参数"}), 400

    original_filename = os.path.basename(file.filename).strip()
    ext = os.path.splitext(original_filename)[1].lower()
    allowed_exts = {".pdf", ".docx", ".doc", ".txt", ".md"}
    if ext not in allowed_exts:
        return jsonify({"error": f"不支持的文件类型: {ext}"}), 400

    # 文件保存时使用安全文件名，避免路径注入与跨平台兼容问题。
    safe_basename = secure_filename(os.path.splitext(original_filename)[0])
    if safe_basename:
        saved_filename = f"{safe_basename}_{uuid.uuid4().hex[:8]}{ext}"
    else:
        saved_filename = f"upload_{uuid.uuid4().hex}{ext}"

    upload_folder = current_app.config.get("UPLOAD_FOLDER", "uploads")
    os.makedirs(upload_folder, exist_ok=True)
    saved_path = os.path.join(upload_folder, saved_filename)
    file.save(saved_path)

    try:
        init_database()
        kb, err_resp = _get_or_create_kb_for_upload(kb_id=kb_id, kb_name=kb_name, user_id=user_id)
        if err_resp is not None:
            if os.path.exists(saved_path):
                os.remove(saved_path)
            return err_resp

        document = KnowledgeDocument(
            kb_id=kb.id,
            filename=original_filename,
            stored_path=saved_path,
            status="ready",
        )
        db.session.add(document)
        db.session.flush()

        pipeline = IngestionPipeline()
        result = pipeline.process(
            file_path=saved_path,
            kb_id=kb.id,
            document_id=document.id,
            source_filename=original_filename,
        )
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        if os.path.exists(saved_path):
            try:
                os.remove(saved_path)
            except Exception:
                pass
        current_app.logger.exception("上传与入库失败: %s", exc)
        return jsonify({"error": f"上传失败: {str(exc)}"}), 500

    return jsonify(
        {
            "message": "文档上传并入库成功",
            "kb_id": kb.id,
            "kb_name": kb.name,
            "document_id": document.id,
            "filename": original_filename,
            "chunk_count": result["chunk_count"],
            "text_block_count": result["text_block_count"],
        }
    ), 200


@kb_bp.route("/v1/kb/<kb_id>/documents", methods=["GET"])
def list_documents(kb_id: str):
    try:
        init_database()
        kb = KnowledgeBase.query.filter_by(id=kb_id).first()
        if kb is None:
            return jsonify({"error": "知识库不存在"}), 404

        rows = (
            db.session.query(
                KnowledgeDocument,
                func.count(DocumentChunk.id).label("chunk_count"),
            )
            .outerjoin(DocumentChunk, DocumentChunk.document_id == KnowledgeDocument.id)
            .filter(KnowledgeDocument.kb_id == kb_id)
            .group_by(KnowledgeDocument.id)
            .order_by(KnowledgeDocument.created_at.desc())
            .all()
        )
    except Exception as exc:
        current_app.logger.exception("查询文档列表失败: %s", exc)
        return jsonify({"error": f"查询文档列表失败: {str(exc)}"}), 500

    items = []
    for doc, chunk_count in rows:
        item = doc.to_dict()
        item["chunk_count"] = int(chunk_count or 0)
        items.append(item)
    return jsonify({"kb_id": kb_id, "items": items}), 200


@kb_bp.route("/v1/kb/<kb_id>/documents/<document_id>", methods=["DELETE"])
def delete_document(kb_id: str, document_id: str):
    file_path = ""
    try:
        init_database()
        doc = KnowledgeDocument.query.filter_by(id=document_id, kb_id=kb_id).first()
        if doc is None:
            return jsonify({"error": "文档不存在"}), 404
        file_path = (doc.stored_path or "").strip()
        db.session.delete(doc)
        db.session.commit()
    except Exception as exc:
        db.session.rollback()
        current_app.logger.exception("删除文档失败: %s", exc)
        return jsonify({"error": f"删除文档失败: {str(exc)}"}), 500

    if file_path and os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as exc:
            current_app.logger.warning("删除文档文件失败: %s", exc)

    return jsonify({"message": "文档删除成功", "kb_id": kb_id, "document_id": document_id}), 200
