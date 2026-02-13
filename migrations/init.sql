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

-- 考核会话表
CREATE TABLE IF NOT EXISTS quizzes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(50) NOT NULL,
    kb_ids JSONB NOT NULL,                          -- 选择的知识库 ID 列表
    status VARCHAR(32) NOT NULL DEFAULT 'created',  -- created / in_progress / completed
    total_score INT,
    summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);

-- 考核题目表
CREATE TABLE IF NOT EXISTS quiz_questions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quiz_id UUID REFERENCES quizzes(id) ON DELETE CASCADE,
    question_number INT NOT NULL,           -- 1~10
    chunk_content TEXT NOT NULL,             -- 出题依据的原文片段
    question TEXT NOT NULL,                  -- 题目
    standard_answer TEXT NOT NULL,           -- 标准答案
    user_answer TEXT,                        -- 用户作答
    score INT,                              -- 单题得分 0~10
    feedback TEXT,                           -- 单题反馈
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 当数据量达到万级以上时，为 embedding 列创建 HNSW 索引以加速检索
-- CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops);
