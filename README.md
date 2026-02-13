# RForge
**Forge knowledge. Repeat until it sticks.**

## TODO

- [x] 知识库增删改查
- [x] 知识文档增删改查以及调用指定模型将文本转向量化并存储
- [x] 基于多知识库的问答
- [x] 标准增删改查接口统一api字典返回
- [x] 考核模式
- [ ] 支持自定义模型提供商和模型
- [ ] 用户管理/数据隔离
- [ ] 前端界面
- [ ] 支持多种文档格式，优化文档分割效果
- [ ] 接入三方oss，存储上传的文件


## 核心能力
- 知识库管理：创建、列表、删除。
- 文档管理：一个知识库支持多个文档，支持上传、列表、删除。
- 检索问答：可指定多个知识库 `kb_ids` 参与检索。
- 考核模式：基于知识库片段自动出题并评分。

## 主要接口

### 知识库
- `POST /v1/kb`：创建知识库  
  请求体示例：`{"name":"产品文档","user_id":"system"}`
- `GET /v1/kb?user_id=system`：查询知识库列表（`user_id` 可选）
- `DELETE /v1/kb/<kb_id>`：删除知识库（级联删除其文档与切片）

### 文档
- `POST /v1/kb/upload`：上传文档并入库（`multipart/form-data`）
  - 必填：`file`
  - 二选一：`kb_id` 或 `kb_name`（兼容旧用法）
  - 可选：`user_id`（仅使用 `kb_name` 定位时有效，默认 `system`）
- `GET /v1/kb/<kb_id>/documents`：查询知识库下文档列表（含 `chunk_count`）
- `DELETE /v1/kb/<kb_id>/documents/<document_id>`：删除文档及其切片

### 问答
- `POST /v1/chat/completions`  
  请求体示例：`{"query":"问题内容","kb_ids":["kb-id-1","kb-id-2"]}`

### 考核
- `POST /v1/quiz`：创建考核  
  请求体示例：`{"user_id":"user1","kb_ids":["kb-id-1","kb-id-2"]}`
- `POST /v1/quiz/<quiz_id>/start`：发起考核，系统生成 10 道题目  
  返回题目列表（不含标准答案）
- `POST /v1/quiz/<quiz_id>/questions/<question_id>/submit`：提交单题答案  
  请求体示例：`{"answer":"用户的回答"}`  
  返回实时评分与反馈
- `GET /v1/quiz/<quiz_id>/summary`：考核总结（SSE 流式）  
  所有题目作答完毕后，计算总分（满分 100）并流式返回综合评价

## 说明
- 支持文档类型：`.pdf`、`.docx`、`.doc`、`.txt`、`.md`。
- 文档上传后会切片并向量化；若向量服务暂时不可用，系统会先保存文本切片，便于后续补向量。
