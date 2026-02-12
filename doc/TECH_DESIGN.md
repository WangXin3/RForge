## 1. 技术栈选型
* 使用.venv管理python环境
* python版本是3.13.9
* pip安装包
* 后端框架： Flask (Python 3.11+)
* 数据库： PostgreSQL + pgvector (存储关系数据及向量)
* AI 编排： LangChain + LangGraph
* Embedding 模型： OpenAI text-embedding-3-small 或本地 BGE-m3
* LLM： GPT-4o 或 DeepSeek-V3


## 2. 数据库建模 (Postgres)
```sql
-- 开启向量插件
CREATE EXTENSION IF NOT EXISTS vector;

-- 知识库元数据表
CREATE TABLE knowledge_bases (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255),
    user_id VARCHAR(50), -- 'system' 表示公共库
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 知识库文档表
CREATE TABLE knowledge_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id UUID REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    filename VARCHAR(255),
    stored_path VARCHAR(512),
    status VARCHAR(32) DEFAULT 'ready',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 文档切片表 (核心：存储文本与向量)
CREATE TABLE document_chunks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id UUID REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    document_id UUID REFERENCES knowledge_documents(id) ON DELETE CASCADE,
    content TEXT,
    embedding VECTOR(1536), -- 维度根据 Embedding 模型而定
    metadata JSONB,         -- 存储页码、源文件名等
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 考核记录表
CREATE TABLE quiz_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR(50),
    question TEXT,
    standard_answer TEXT,
    user_answer TEXT,
    score INT,
    feedback TEXT
);
```

## 3. 系统架构组件

### A. 数据摄入流水线 (Ingestion Pipeline)

* Loader: 使用 Unstructured 模块读取文件。

* Splitter: 采用语义切片（Semantic Chunking）或递归字符切片。

* Embedder: 调用 Embedding 接口。

* Storage: 执行 SQL：INSERT INTO document_chunks (content, embedding) VALUES (...)。

### B. 模式一：RAG 检索逻辑

* 用户提问： $Q$
* 向量化： $V_q = embed(Q)$
* 相似度检索：
```sql
SELECT content, metadata FROM document_chunks 
WHERE kb_id IN (选定的库ID)
ORDER BY embedding <=> V_q LIMIT 5; -- 使用余弦距离
```
* 生成回答： 将 Content + $Q$ 送入 LLM。

### C. 模式二：考核模式逻辑 (LangGraph 控制流)

* 节点 1 (Sampler): 从 document_chunks 中 ORDER BY random() 抽取一个片段。

* 节点 2 (Questioner): LLM 根据片段生成问题和评分标准。

* 节点 3 (UI Wait): 挂起等待用户 Flask 接口提交 user_answer。

* 节点 4 (Grader): * 输入： 原文 + 问题 + 标准答案 + 用户回答。

  * Prompt： “你是一个严谨的导师，请对比原文评估用户回答，满分100分。”
  * 输出： 分数、解析。

## 4.关键 API 接口定义

* POST /v1/kb：创建知识库（`name`, `user_id`）。
* GET /v1/kb：查询知识库列表（可按 `user_id` 过滤）。
* DELETE /v1/kb/<kb_id>：删除知识库（级联删除文档与切片）。
* POST /v1/kb/upload：上传文档，接收 `file` + `kb_id`（优先）或 `kb_name`。
* GET /v1/kb/<kb_id>/documents：获取知识库文档列表（含切片数量）。
* DELETE /v1/kb/<kb_id>/documents/<document_id>：删除指定文档及其切片。

* POST /v1/chat/completions:

参数: { "query": "...", "kb_ids": ["..."] }

* GET /v1/quiz/start:

返回: { "quiz_id": "...", "question": "..." }

* POST /v1/quiz/submit:

参数: { "quiz_id": "...", "answer": "..." }

返回: { "score": 85, "feedback": "..." }

## 5.特别提醒

* 索引优化： 在 Postgres 中，当数据量达到万级以上时，请务必为 embedding 列创建 HNSW 索引 以加速检索：
```sql
CREATE INDEX ON document_chunks USING hnsw (embedding vector_cosine_ops);
```

* 并发处理： Flask 默认是同步的，对于长文本解析或考核模式，建议使用 Celery 处理后台任务，或者使用 Flask-SocketIO 进行实时进度反馈。

* 冷启动问题： 在考核模式下，系统随机抽取的片段可能不适合出题（例如只是目录）。建议在出题 Prompt 中加入一个判断逻辑：“如果此片段信息不足以出题，请返回 SKIP”。

