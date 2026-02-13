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


class Quiz(db.Model):
    """考核会话表"""

    __tablename__ = "quizzes"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = db.Column(db.String(50), nullable=False)
    kb_ids = db.Column(db.JSON, nullable=False)  # 选择的知识库 ID 列表
    question_count = db.Column(db.Integer, nullable=False, default=5)  # 题目数量，默认 5
    difficulty = db.Column(db.String(16), nullable=False, default="easy")  # easy / medium / hard
    status = db.Column(db.String(32), nullable=False, default="created")  # created / in_progress / completed
    total_score = db.Column(db.Integer, nullable=True)
    summary = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = db.Column(db.DateTime, nullable=True)

    questions = db.relationship(
        "QuizQuestion",
        backref="quiz",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="QuizQuestion.question_number",
    )

    def to_dict(self, include_questions=False):
        result = {
            "id": self.id,
            "user_id": self.user_id,
            "kb_ids": self.kb_ids,
            "question_count": self.question_count,
            "difficulty": self.difficulty,
            "status": self.status,
            "total_score": self.total_score,
            "summary": self.summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
        if include_questions:
            result["questions"] = [q.to_dict() for q in self.questions]
        return result


class QuizQuestion(db.Model):
    """考核题目表"""

    __tablename__ = "quiz_questions"

    id = db.Column(db.String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    quiz_id = db.Column(db.String(36), db.ForeignKey("quizzes.id", ondelete="CASCADE"), nullable=False)
    question_number = db.Column(db.Integer, nullable=False)  # 1~10
    chunk_content = db.Column(db.Text, nullable=False)  # 出题依据的原文片段
    question = db.Column(db.Text, nullable=False)  # 题目
    standard_answer = db.Column(db.Text, nullable=False)  # 标准答案
    user_answer = db.Column(db.Text, nullable=True)  # 用户作答
    score = db.Column(db.Integer, nullable=True)  # 单题得分 0~10
    feedback = db.Column(db.Text, nullable=True)  # 单题反馈
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    def to_dict(self, include_standard_answer=False):
        result = {
            "id": self.id,
            "quiz_id": self.quiz_id,
            "question_number": self.question_number,
            "question": self.question,
            "user_answer": self.user_answer,
            "score": self.score,
            "feedback": self.feedback,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_standard_answer:
            result["standard_answer"] = self.standard_answer
            result["chunk_content"] = self.chunk_content
        return result


def init_database() -> None:
    """初始化数据库对象与表结构（幂等）。"""
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        with db.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    # 迁移：删除旧的 quiz_logs 表（已被 quizzes + quiz_questions 替代）
    inspector = inspect(db.engine)
    existing_tables = inspector.get_table_names()
    if "quiz_logs" in existing_tables:
        with db.engine.begin() as conn:
            conn.execute(text("DROP TABLE IF EXISTS quiz_logs CASCADE"))

    db.create_all()

    # 迁移：为 quizzes 表添加 question_count / difficulty 列
    if "quizzes" in existing_tables:
        quiz_columns = {col["name"] for col in inspector.get_columns("quizzes")}
        if "question_count" not in quiz_columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE quizzes ADD COLUMN question_count INT NOT NULL DEFAULT 5"))
        if "difficulty" not in quiz_columns:
            with db.engine.begin() as conn:
                conn.execute(text("ALTER TABLE quizzes ADD COLUMN difficulty VARCHAR(16) NOT NULL DEFAULT 'easy'"))

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
