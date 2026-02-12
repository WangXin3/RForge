-- RAG 知识问答与考核系统 - 初始数据库 Schema
-- 需要 PostgreSQL 并安装 pgvector 扩展

-- 开启向量插件
CREATE EXTENSION IF NOT EXISTS vector;

-- 知识库元数据表
CREATE TABLE IF NOT EXISTS knowledge_bases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    user_id VARCHAR(50) NOT NULL DEFAULT 'system', -- 'system' 表示公共库
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 文档切片表 (核心：存储文本与向量)
CREATE TABLE IF NOT EXISTS document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id UUID REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    embedding VECTOR(1536), -- 维度根据 Embedding 模型而定
    metadata JSONB,         -- 存储页码、源文件名等
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 考核记录表
CREATE TABLE IF NOT EXISTS quiz_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(50) NOT NULL,
    question TEXT NOT NULL,
    standard_answer TEXT,
    user_answer TEXT,
    score INT,
    feedback TEXT
);

-- 当数据量达到万级以上时，为 embedding 列创建 HNSW 索引以加速检索
-- CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops);
