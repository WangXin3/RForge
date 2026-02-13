"""考核模式业务逻辑

流程：
1. 创建考核：选择知识库，创建 Quiz 记录（可自定义题目数和难度）
2. 发起考核：随机抽取片段，LLM **多线程并行**生成题目
3. 提交答案：逐题评分，严格对比原文
4. 考核总结：计算总分，流式生成综合评价
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from openai import OpenAI
from flask import current_app
from sqlalchemy import func, text

from app.models.database import db, DocumentChunk, Quiz, QuizQuestion

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_QUESTION_COUNT = 5
MIN_CHUNK_LENGTH = 50  # 过短片段不适合出题

# ---------------------------------------------------------------------------
# 难度映射
# ---------------------------------------------------------------------------
DIFFICULTY_MAP = {
    "easy": {
        "desc": "基础级",
        "instruction": (
            "题目应侧重基础概念和事实性知识，考查对核心定义、基本原理的直接记忆与理解，"
            "避免需要深度推理的复杂问题。"
        ),
    },
    "medium": {
        "desc": "进阶级",
        "instruction": (
            "题目应考查对知识点的理解和归纳能力，需要答题者解释原理、比较异同或阐述因果关系，"
            "而不仅仅是简单背诵。"
        ),
    },
    "hard": {
        "desc": "挑战级",
        "instruction": (
            "题目应要求答题者进行深度分析、推理或综合运用多个知识点，"
            "可以涉及场景应用、方案设计或批判性思考。"
        ),
    },
}

# ---------------------------------------------------------------------------
# Prompt 模板
# ---------------------------------------------------------------------------
QUESTION_GENERATION_PROMPT = """\
你是一位专业的知识考核出题专家。请根据以下参考材料，出一道{difficulty_desc}简答题。

核心要求：
1. 题目必须是一个完整、独立的问题，答题者无需阅读任何参考材料即可理解题意。
2. 严禁在题目中出现"根据原文"、"根据上文"、"根据材料"、"文中提到"等任何引用性表述。
3. 题目应像教科书课后练习题一样，包含足够的背景信息，让人直接能理解并作答。
4. {difficulty_instruction}
5. 给出精确的标准答案，标准答案必须完全基于参考材料内容，不可添加参考材料中没有的信息。
6. 如果参考材料信息不足以出一道有意义的题目（例如仅是目录、页眉页脚、无实质内容），请直接返回 SKIP。

参考材料：
{chunk_content}

请严格按照以下 JSON 格式返回（不要包含其他内容）：
{{"question": "题目内容", "standard_answer": "标准答案"}}

如果无法出题，请只返回：
SKIP"""

GRADING_PROMPT = """\
请对比原文内容，评估回答质量，并给出温和、清晰、建设性的反馈。

评分原则：
1. 满分 {max_score} 分，请给出 0~{max_score} 的整数分。评判须基于原文和标准答案，但不必机械地逐字对照。
2. 数量要求灵活把握：若题目要求「至少列举 N 项」「至少写出 N 点」等，只要回答中给出了正确且足够的 N 项（或更多），即视为满足要求，不得因标准答案里还有更多项而扣分。重点看是否答对、是否够数，而非是否与标准答案条数完全一致。
3. 有错误或遗漏时，用温和、建设性的语气指出，并简要说明正确思路或可补充的内容；回答正确或基本正确时，给予肯定和适当鼓励。
4. 回答完全正确且符合题目要求（含「至少 N 项」类要求）时，给满分；完全不相关或为空时，给 0 分。
5. 不应教条地将作答与标准答案逐字比对，应理解两者语义是否一致。若核心要点覆盖充分、语义高度一致，应给出高分或满分。

反馈语气要求：
- 以「你」称呼答题者，不使用第三人称称呼。
- 避免任何角色代入或角色名称，不出现“老师相信你”之类表达。
- 既有针对性点评，也可在合适处给予肯定与鼓励，避免过于严厉或挑剔。

原文片段：
{chunk_content}

题目：
{question}

标准答案：
{standard_answer}

回答：
{user_answer}

请严格按照以下 JSON 格式返回（不要包含其他内容）：
{{"score": 分数, "feedback": "以你称呼答题者、和蔼鼓励的详细评分反馈，可含正确之处肯定、错误或遗漏的温和指出与改进建议"}}"""

SUMMARY_PROMPT = """\
请根据以下考核结果，给出一份有温度、有指导性的总结评价。

考核总分：{total_score}/100

各题详情：
{questions_detail}

请从以下几个方面进行总结：
1. 总体评价：对本次作答的知识掌握情况做出整体判断，并先肯定做得好的地方。
2. 典型错误：温和指出回答中出现的典型错误和误解，解释为什么会错。
3. 遗漏知识点：说明还可以补充的关键知识点，帮助形成更完整的理解。
4. 改进建议：给出有针对性的学习建议。

总结原则：
1. 判断需基于题目、标准答案、作答内容和评分反馈，不机械逐字比对，重点看核心要点是否掌握。
2. 若题目有「至少 N 项」等数量下限，只要你已正确覆盖且达到最低数量，就应视为达标，不因标准答案条目更多而否定。
3. 先肯定做得好的部分，再指出可改进之处；指出问题时给出可执行的补强方向。

表达要求：
- 使用中文，语气亲切、鼓励、耐心，不苛责、不讽刺。
- 全文以「你」来称呼答题者，不使用第三人称称呼。
- 避免任何角色代入或角色名称，不出现“老师相信你”之类表达。
- 在指出不足的同时给出可执行的改进方向，语言简洁清晰。"""


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

    @staticmethod
    def _score_per_question(question_count: int) -> int:
        """根据题目数量计算单题分值，确保总分尽量接近 100"""
        return 100 // question_count

    # ------------------------------------------------------------------
    # 1. 创建考核
    # ------------------------------------------------------------------
    def create_quiz(
        self,
        user_id: str,
        kb_ids: list[str],
        question_count: int = DEFAULT_QUESTION_COUNT,
        difficulty: str = "easy",
    ) -> Quiz:
        """创建考核会话"""
        quiz = Quiz(
            user_id=user_id,
            kb_ids=kb_ids,
            question_count=question_count,
            difficulty=difficulty,
            status="created",
        )
        db.session.add(quiz)
        db.session.commit()
        return quiz

    # ------------------------------------------------------------------
    # 2. 抽取片段 & 生成题目
    # ------------------------------------------------------------------
    def sample_chunks(self, kb_ids: list[str], count: int = DEFAULT_QUESTION_COUNT) -> list[DocumentChunk]:
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

    @staticmethod
    def _parse_llm_json(content: str) -> dict | None:
        """解析 LLM 返回的 JSON（兼容 markdown code block 包裹）。"""
        json_str = content
        if "```" in json_str:
            lines = json_str.split("\n")
            in_block = False
            block_lines: list[str] = []
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
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _generate_question_standalone(
        chunk_content: str,
        difficulty: str,
        api_key: str,
        base_url: str,
        model: str,
    ) -> dict | None:
        """线程安全的题目生成方法（不依赖 Flask current_app）。

        返回 {"question": "...", "standard_answer": "..."} 或 None。
        """
        diff_info = DIFFICULTY_MAP.get(difficulty, DIFFICULTY_MAP["easy"])
        prompt = QUESTION_GENERATION_PROMPT.format(
            difficulty_desc=diff_info["desc"],
            difficulty_instruction=diff_info["instruction"],
            chunk_content=chunk_content,
        )

        try:
            client = OpenAI(api_key=api_key, base_url=base_url or None)
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            content = (resp.choices[0].message.content or "").strip()

            if content.upper().startswith("SKIP"):
                return None

            result = QuizService._parse_llm_json(content)
            if result and "question" in result and "standard_answer" in result:
                return result
            return None
        except Exception as exc:
            logger.warning("生成题目失败: %s", exc)
            return None

    def generate_question(self, chunk_content: str, difficulty: str = "easy") -> dict | None:
        """调用 LLM 根据片段生成题目和标准答案（主线程版本）。

        返回 {"question": "...", "standard_answer": "..."} 或 None（片段不适合出题）。
        """
        api_key = current_app.config.get("OPENAI_API_KEY", "")
        base_url = current_app.config.get("OPENAI_BASE_URL", "")
        model = self._get_model()
        if not api_key:
            raise ValueError("OPENAI_API_KEY 未配置，无法进行考核。")
        return self._generate_question_standalone(chunk_content, difficulty, api_key, base_url, model)

    def generate_questions_for_quiz(self, quiz: Quiz) -> list[QuizQuestion]:
        """为考核生成题目（多线程并行）。

        从知识库抽取片段，并行调用 LLM 生成题目，跳过不适合的片段。
        """
        question_count = quiz.question_count
        difficulty = quiz.difficulty
        kb_ids = quiz.kb_ids
        chunks = self.sample_chunks(kb_ids, count=question_count)

        if not chunks:
            raise ValueError("选定的知识库中没有足够的文档片段用于出题，请确认知识库中已上传文档。")

        # 预先获取配置，避免子线程内访问 Flask current_app
        api_key = current_app.config.get("OPENAI_API_KEY", "")
        base_url = current_app.config.get("OPENAI_BASE_URL", "")
        model = self._get_model()
        if not api_key:
            raise ValueError("OPENAI_API_KEY 未配置，无法进行考核。")

        # 多线程并行生成题目
        results: list[tuple[DocumentChunk, dict]] = []
        max_workers = min(question_count, 10)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = {
                executor.submit(
                    self._generate_question_standalone,
                    chunk.content,
                    difficulty,
                    api_key,
                    base_url,
                    model,
                ): chunk
                for chunk in chunks
            }

            for future in as_completed(future_to_chunk):
                chunk = future_to_chunk[future]
                try:
                    question_data = future.result()
                except Exception as exc:
                    logger.warning("线程生成题目异常: %s", exc)
                    continue

                if question_data is not None:
                    results.append((chunk, question_data))

                if len(results) >= question_count:
                    # 已凑够题目，取消剩余任务
                    for f in future_to_chunk:
                        f.cancel()
                    break

        if len(results) < question_count:
            raise ValueError(
                f"仅生成了 {len(results)} 道题目，不足 {question_count} 道。"
                "知识库中可能缺少足够的有效内容，请补充文档后重试。"
            )

        # 构建 QuizQuestion 对象
        questions: list[QuizQuestion] = []
        for idx, (chunk, question_data) in enumerate(results[:question_count]):
            q = QuizQuestion(
                quiz_id=quiz.id,
                question_number=idx + 1,
                chunk_content=chunk.content,
                question=question_data["question"],
                standard_answer=question_data["standard_answer"],
            )
            questions.append(q)

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
        max_score: int = 10,
    ) -> dict:
        """调用 LLM 对用户回答进行严格评分。

        返回 {"score": int, "feedback": str}。
        """
        prompt = GRADING_PROMPT.format(
            max_score=max_score,
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

            result = self._parse_llm_json(content)
            if result is None:
                raise ValueError(f"无法解析评分结果: {content}")

            score = int(result.get("score", 0))
            score = max(0, min(max_score, score))  # 限制在 0~max_score
            feedback = result.get("feedback", "")
            return {"score": score, "feedback": feedback}
        except (json.JSONDecodeError, Exception) as exc:
            logger.exception("评分失败: %s", exc)
            raise ValueError(f"评分过程中出现错误: {exc}") from exc

    def submit_answer(self, quiz_question: QuizQuestion, user_answer: str) -> dict:
        """提交答案并评分，更新数据库记录。"""
        # 根据所属考核的题目数量动态计算单题分值
        quiz = Quiz.query.get(quiz_question.quiz_id)
        max_score = self._score_per_question(quiz.question_count) if quiz else 10

        grade_result = self.grade_answer(
            chunk_content=quiz_question.chunk_content,
            question=quiz_question.question,
            standard_answer=quiz_question.standard_answer,
            user_answer=user_answer,
            max_score=max_score,
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

    def _build_questions_detail(self, questions: list[QuizQuestion], score_per_question: int) -> str:
        """构建题目详情文本，用于总结 Prompt"""
        details = []
        for q in questions:
            detail = (
                f"第{q.question_number}题（得分：{q.score}/{score_per_question}）\n"
                f"题目：{q.question}\n"
                f"标准答案：{q.standard_answer}\n"
                f"作答内容：{q.user_answer}\n"
                f"评分反馈：{q.feedback}"
            )
            details.append(detail)
        return "\n\n---\n\n".join(details)

    def generate_summary_stream(self, quiz: Quiz):
        """流式生成考核总结。

        返回一个生成器，逐 token 产出总结内容。
        """
        total_score = self.calculate_total_score(quiz)
        score_per_question = self._score_per_question(quiz.question_count)
        questions_detail = self._build_questions_detail(quiz.questions, score_per_question)

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

    def generate_summary(self, quiz: Quiz) -> str:
        """非流式生成考核总结文本。"""
        total_score = self.calculate_total_score(quiz)
        score_per_question = self._score_per_question(quiz.question_count)
        questions_detail = self._build_questions_detail(quiz.questions, score_per_question)

        prompt = SUMMARY_PROMPT.format(
            total_score=total_score,
            questions_detail=questions_detail,
        )

        try:
            client = self._get_openai_client()
            resp = client.chat.completions.create(
                model=self._get_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
            )
            return (resp.choices[0].message.content or "").strip()
        except Exception as exc:
            logger.exception("生成考核总结失败: %s", exc)
            raise ValueError(f"生成考核总结时出现错误: {exc}") from exc

    def complete_quiz(self, quiz: Quiz, total_score: int, summary: str) -> None:
        """完成考核，持久化总分和总结。"""
        from datetime import datetime, timezone

        # 兼容流式场景，确保对象已绑定到当前 session，避免提交丢失。
        persisted_quiz = db.session.merge(quiz)
        persisted_quiz.total_score = total_score
        persisted_quiz.summary = summary
        persisted_quiz.status = "completed"
        persisted_quiz.completed_at = datetime.now(timezone.utc)
        db.session.commit()
