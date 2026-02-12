import uuid
from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy
from pgvector.sqlalchemy import Vector
from sqlalchemy import inspect, text

db = SQLAlchemy()


class KnowledgeBase(db.Model):
    """知识库元数据表"""

    __tablename__ = "knowledge_bases"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = db.Column(db.String(255), nullable=False)
    user_id = db.Column(db.String(50), nullable=False, default="system")  # 'system' 表示公共库
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # 关联文档与切片
    documents = db.relationship(
        "KnowledgeDocument",
        backref="knowledge_base",
        lazy=True,
        cascade="all, delete-orphan",
    )
    chunks = db.relationship("DocumentChunk", backref="knowledge_base", lazy=True, cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class DocumentChunk(db.Model):
    """文档切片表（核心：存储文本与向量）"""

    __tablename__ = "document_chunks"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kb_id = db.Column(db.String(36), db.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
    document_id = db.Column(
        db.String(36),
        db.ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=True,
    )
    content = db.Column(db.Text, nullable=False)
    embedding = db.Column(Vector(1536))  # 维度根据 Embedding 模型而定
    metadata_ = db.Column("metadata", db.JSON, nullable=True)  # 存储页码、源文件名等
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        return {
            "id": self.id,
            "kb_id": self.kb_id,
            "document_id": self.document_id,
            "content": self.content,
            "metadata": self.metadata_,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class KnowledgeDocument(db.Model):
    """知识库文档表"""

    __tablename__ = "knowledge_documents"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    kb_id = db.Column(db.String(36), db.ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    stored_path = db.Column(db.String(512), nullable=False)
    status = db.Column(db.String(32), nullable=False, default="ready")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    chunks = db.relationship(
        "DocumentChunk",
        backref="document",
        lazy=True,
        cascade="all, delete-orphan",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "kb_id": self.kb_id,
            "filename": self.filename,
            "stored_path": self.stored_path,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class QuizLog(db.Model):
    """考核记录表"""

    __tablename__ = "quiz_logs"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(50), nullable=False)
    question = db.Column(db.Text, nullable=False)
    standard_answer = db.Column(db.Text, nullable=True)
    user_answer = db.Column(db.Text, nullable=True)
    score = db.Column(db.Integer, nullable=True)
    feedback = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "question": self.question,
            "standard_answer": self.standard_answer,
            "user_answer": self.user_answer,
            "score": self.score,
            "feedback": self.feedback,
        }


def init_database() -> None:
    """初始化数据库对象与表结构（幂等）。"""
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        with db.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    db.create_all()

    inspector = inspect(db.engine)
    chunk_columns = {col["name"] for col in inspector.get_columns("document_chunks")}
    if "document_id" not in chunk_columns:
        with db.engine.begin() as conn:
            conn.execute(text("ALTER TABLE document_chunks ADD COLUMN document_id VARCHAR(36)"))

    if dialect == "postgresql":
        with db.engine.begin() as conn:
            conn.execute(
                text(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1 FROM pg_constraint
                            WHERE conname = 'fk_document_chunks_document_id'
                        ) THEN
                            ALTER TABLE document_chunks
                            ADD CONSTRAINT fk_document_chunks_document_id
                            FOREIGN KEY (document_id)
                            REFERENCES knowledge_documents(id)
                            ON DELETE CASCADE;
                        END IF;
                    END
                    $$;
                    """
                )
            )
