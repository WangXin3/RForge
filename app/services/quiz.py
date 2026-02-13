"""考核模式业务逻辑

流程：
1. 创建考核：选择知识库，创建 Quiz 记录
2. 发起考核：随机抽取片段，LLM 生成 10 道题目
3. 提交答案：逐题评分，严格对比原文
4. 考核总结：计算总分，流式生成综合评价
"""

from __future__ import annotations

import json
import logging

from openai import OpenAI
from flask import current_app
from sqlalchemy import func, text

from app.models.database import db, DocumentChunk, Quiz, QuizQuestion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
QUESTION_COUNT = 10
SCORE_PER_QUESTION = 10
MIN_CHUNK_LENGTH = 50  # 过短片段不适合出题
MAX_SAMPLE_ATTEMPTS = 30  # 最多尝试抽取次数（防止无限循环）

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------
QUESTION_GENERATION_PROMPT = """\
你是一位专业的知识考核出题专家。请根据以下原文片段，出一道简答题或思考题。

要求：
1. 题目应有一定深度，考查对原文核心知识点的理解。
2. 给出精确的标准答案，标准答案必须完全基于原文内容，不可添加原文中没有的信息。
3. 如果该片段信息不足以出一道有意义的题目（例如仅是目录、页眉页脚、无实质内容），请直接返回 SKIP。

原文片段：
{chunk_content}

请严格按照以下 JSON 格式返回（不要包含其他内容）：
{{"question": "题目内容", "standard_answer": "标准答案"}}

如果无法出题，请只返回：
SKIP"""

GRADING_PROMPT = """\
你是一位严谨的知识考核评分导师。请严格对比原文内容，评估用户的回答。

评分规则：
1. 满分 {max_score} 分，请给出 0~{max_score} 的整数分。
2. 必须严格基于原文和标准答案进行评判，不可自由发挥。
3. 必须逐点指出用户回答中的错误之处。
4. 必须指出用户回答中遗漏的关键知识点。
5. 如果用户回答完全正确且完整，给满分。
6. 如果用户回答完全不相关或为空，给 0 分。

原文片段：
{chunk_content}

题目：
{question}

标准答案：
{standard_answer}

用户回答：
{user_answer}

请严格按照以下 JSON 格式返回（不要包含其他内容）：
{{"score": 分数, "feedback": "详细的评分反馈，包括错误指出和遗漏分析"}}"""

SUMMARY_PROMPT = """\
你是一位资深的知识考核评审专家。请根据以下考核结果，给出全面的总结评价。

考核总分：{total_score}/100

各题详情：
{questions_detail}

请从以下几个方面进行总结：
1. 总体评价：对用户的知识掌握程度做出整体判断。
2. 典型错误：指出用户在回答中出现的典型错误和误解。
3. 遗漏知识点：评价用户遗漏的重要知识点。
4. 改进建议：给出有针对性的学习建议。

请用中文回答，语气专业但友好。"""


class QuizService:
    """考核服务"""

    def _get_openai_client(self) -> OpenAI:
        """获取 OpenAI 客户端"""
        api_key = current_app.config.get("OPENAI_API_KEY", "")
        base_url = current_app.config.get("OPENAI_BASE_URL", "")
        if not api_key:
            raise ValueError("OPENAI_API_KEY 未配置，无法进行考核。")
        return OpenAI(api_key=api_key, base_url=base_url or None)

    def _get_model(self) -> str:
        """获取 LLM 模型名称"""
        return current_app.config.get("LLM_MODEL", "gpt-4o")

    # ------------------------------------------------------------------
    # 1. 创建考核
    # ------------------------------------------------------------------
    def create_quiz(self, user_id: str, kb_ids: list[str]) -> Quiz:
        """创建考核会话"""
        quiz = Quiz(user_id=user_id, kb_ids=kb_ids, status="created")
        db.session.add(quiz)
        db.session.commit()
        return quiz

    # ------------------------------------------------------------------
    # 2. 抽取片段 & 生成题目
    # ------------------------------------------------------------------
    def sample_chunks(self, kb_ids: list[str], count: int = QUESTION_COUNT) -> list[DocumentChunk]:
        """从知识库中随机抽取足够数量的文档片段。

        过滤过短的内容，确保片段有足够信息用于出题。
        """
        chunks = (
            DocumentChunk.query
            .filter(DocumentChunk.kb_id.in_(kb_ids))
            .filter(func.length(DocumentChunk.content) >= MIN_CHUNK_LENGTH)
            .order_by(text("random()"))
            .limit(count * 3)  # 多取一些，以便 SKIP 后仍有足够题目
            .all()
        )
        return chunks

    def generate_question(self, chunk_content: str) -> dict | None:
        """调用 LLM 根据片段生成题目和标准答案。

        返回 {"question": "...", "standard_answer": "..."} 或 None（片段不适合出题）。
        """
        prompt = QUESTION_GENERATION_PROMPT.format(chunk_content=chunk_content)

        try:
            client = self._get_openai_client()
            resp = client.chat.completions.create(
                model=self._get_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            content = (resp.choices[0].message.content or "").strip()

            if content.upper().startswith("SKIP"):
                return None

            # 解析 JSON（兼容可能包含 markdown code block 的情况）
            json_str = content
            if "```" in json_str:
                # 提取 code block 内容
                lines = json_str.split("\n")
                in_block = False
                block_lines = []
                for line in lines:
                    if line.strip().startswith("```"):
                        if in_block:
                            break
                        in_block = True
                        continue
                    if in_block:
                        block_lines.append(line)
                if block_lines:
                    json_str = "\n".join(block_lines)

            result = json.loads(json_str)
            if "question" in result and "standard_answer" in result:
                return result
            return None
        except (json.JSONDecodeError, Exception) as exc:
            logger.warning("生成题目失败: %s", exc)
            return None

    def generate_questions_for_quiz(self, quiz: Quiz) -> list[QuizQuestion]:
        """为考核生成 10 道题目。

        从知识库抽取片段，逐个调用 LLM 生成题目，跳过不适合的片段。
        """
        kb_ids = quiz.kb_ids
        chunks = self.sample_chunks(kb_ids)

        if not chunks:
            raise ValueError("选定的知识库中没有足够的文档片段用于出题，请确认知识库中已上传文档。")

        questions: list[QuizQuestion] = []
        chunk_index = 0

        while len(questions) < QUESTION_COUNT and chunk_index < len(chunks):
            chunk = chunks[chunk_index]
            chunk_index += 1

            question_data = self.generate_question(chunk.content)
            if question_data is None:
                continue

            q = QuizQuestion(
                quiz_id=quiz.id,
                question_number=len(questions) + 1,
                chunk_content=chunk.content,
                question=question_data["question"],
                standard_answer=question_data["standard_answer"],
            )
            questions.append(q)

        if len(questions) < QUESTION_COUNT:
            raise ValueError(
                f"仅生成了 {len(questions)} 道题目，不足 {QUESTION_COUNT} 道。"
                "知识库中可能缺少足够的有效内容，请补充文档后重试。"
            )

        # 批量保存题目
        db.session.add_all(questions)
        quiz.status = "in_progress"
        db.session.commit()

        return questions

    # ------------------------------------------------------------------
    # 3. 评分
    # ------------------------------------------------------------------
    def grade_answer(
        self,
        chunk_content: str,
        question: str,
        standard_answer: str,
        user_answer: str,
    ) -> dict:
        """调用 LLM 对用户回答进行严格评分。

        返回 {"score": int, "feedback": str}。
        """
        prompt = GRADING_PROMPT.format(
            max_score=SCORE_PER_QUESTION,
            chunk_content=chunk_content,
            question=question,
            standard_answer=standard_answer,
            user_answer=user_answer,
        )

        try:
            client = self._get_openai_client()
            resp = client.chat.completions.create(
                model=self._get_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
            )
            content = (resp.choices[0].message.content or "").strip()

            # 解析 JSON
            json_str = content
            if "```" in json_str:
                lines = json_str.split("\n")
                in_block = False
                block_lines = []
                for line in lines:
                    if line.strip().startswith("```"):
                        if in_block:
                            break
                        in_block = True
                        continue
                    if in_block:
                        block_lines.append(line)
                if block_lines:
                    json_str = "\n".join(block_lines)

            result = json.loads(json_str)
            score = int(result.get("score", 0))
            score = max(0, min(SCORE_PER_QUESTION, score))  # 限制在 0~10
            feedback = result.get("feedback", "")
            return {"score": score, "feedback": feedback}
        except (json.JSONDecodeError, Exception) as exc:
            logger.exception("评分失败: %s", exc)
            raise ValueError(f"评分过程中出现错误: {exc}") from exc

    def submit_answer(self, quiz_question: QuizQuestion, user_answer: str) -> dict:
        """提交答案并评分，更新数据库记录。"""
        grade_result = self.grade_answer(
            chunk_content=quiz_question.chunk_content,
            question=quiz_question.question,
            standard_answer=quiz_question.standard_answer,
            user_answer=user_answer,
        )

        quiz_question.user_answer = user_answer
        quiz_question.score = grade_result["score"]
        quiz_question.feedback = grade_result["feedback"]
        db.session.commit()

        return {
            "question_id": quiz_question.id,
            "question_number": quiz_question.question_number,
            "score": grade_result["score"],
            "feedback": grade_result["feedback"],
        }

    # ------------------------------------------------------------------
    # 4. 总结
    # ------------------------------------------------------------------
    def calculate_total_score(self, quiz: Quiz) -> int:
        """计算考核总分"""
        total = sum(q.score for q in quiz.questions if q.score is not None)
        return total

    def _build_questions_detail(self, questions: list[QuizQuestion]) -> str:
        """构建题目详情文本，用于总结 Prompt"""
        details = []
        for q in questions:
            detail = (
                f"第{q.question_number}题（得分：{q.score}/{SCORE_PER_QUESTION}）\n"
                f"题目：{q.question}\n"
                f"标准答案：{q.standard_answer}\n"
                f"用户回答：{q.user_answer}\n"
                f"评分反馈：{q.feedback}"
            )
            details.append(detail)
        return "\n\n---\n\n".join(details)

    def generate_summary_stream(self, quiz: Quiz):
        """流式生成考核总结。

        返回一个生成器，逐 token 产出总结内容。
        """
        total_score = self.calculate_total_score(quiz)
        questions_detail = self._build_questions_detail(quiz.questions)

        prompt = SUMMARY_PROMPT.format(
            total_score=total_score,
            questions_detail=questions_detail,
        )

        try:
            client = self._get_openai_client()
            stream = client.chat.completions.create(
                model=self._get_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
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
            logger.exception("生成考核总结失败: %s", exc)
            yield f"生成考核总结时出现错误: {exc}"

    def complete_quiz(self, quiz: Quiz, total_score: int, summary: str) -> None:
        """完成考核，持久化总分和总结。"""
        from datetime import datetime, timezone

        quiz.total_score = total_score
        quiz.summary = summary
        quiz.status = "completed"
        quiz.completed_at = datetime.now(timezone.utc)
        db.session.commit()
