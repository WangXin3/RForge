import json

from flask import Blueprint, request, Response, stream_with_context

from app.models.database import db, KnowledgeBase, Quiz, QuizQuestion
from app.services.quiz import QuizService
from app.utils.api_response import error, success

quiz_bp = Blueprint("quiz", __name__)


# ---------------------------------------------------------------------------
# 接口 1：创建考核  POST /v1/quiz
# ---------------------------------------------------------------------------
@quiz_bp.route("/v1/quiz", methods=["POST"])
def create_quiz():
    """创建考核

    用户选择多个知识库（公共 / 私有），创建一次考核会话。

    请求体：{
        "user_id": "user1",
        "kb_ids": ["kb-1", "kb-2"],
        "question_count": 5,       // 可选，题目数量 1~20，默认 5
        "difficulty": "easy"        // 可选，easy / medium / hard，默认 easy
    }
    """
    data = request.get_json()

    if not data or "user_id" not in data:
        return error(message="缺少 user_id 参数")

    user_id = str(data["user_id"]).strip()
    if not user_id:
        return error(message="user_id 不能为空")

    kb_ids = data.get("kb_ids", [])
    if not kb_ids or not isinstance(kb_ids, list):
        return error(message="kb_ids 必须是非空数组")

    # 可选参数：题目数量（默认 5，范围 1~20）
    question_count = data.get("question_count", 5)
    try:
        question_count = int(question_count)
    except (TypeError, ValueError):
        return error(message="question_count 必须是整数")
    if question_count < 1 or question_count > 20:
        return error(message="question_count 必须在 1~20 之间")

    # 可选参数：难度（默认 easy）
    VALID_DIFFICULTIES = ("easy", "medium", "hard")
    difficulty = data.get("difficulty", "easy")
    if difficulty not in VALID_DIFFICULTIES:
        return error(message=f"difficulty 必须是 {', '.join(VALID_DIFFICULTIES)} 之一")

    # 校验所有知识库存在且用户有权访问
    for kb_id in kb_ids:
        kb = KnowledgeBase.query.get(kb_id)
        if kb is None:
            return error(message=f"知识库 {kb_id} 不存在")
        # 公共库 (user_id='system') 所有人可用；私有库仅所有者可用
        if kb.user_id != "system" and kb.user_id != user_id:
            return error(message=f"无权访问知识库 {kb_id}")

    try:
        service = QuizService()
        quiz = service.create_quiz(
            user_id=user_id,
            kb_ids=kb_ids,
            question_count=question_count,
            difficulty=difficulty,
        )
        return success(message="考核创建成功", data=quiz.to_dict())
    except Exception as exc:
        return error(message=f"创建考核失败: {exc}", code=500)


# ---------------------------------------------------------------------------
# 接口 2：发起考核  POST /v1/quiz/<quiz_id>/start
# ---------------------------------------------------------------------------
@quiz_bp.route("/v1/quiz/<quiz_id>/start", methods=["POST"])
def start_quiz(quiz_id: str):
    """发起考核

    系统后台生成题目（数量由创建时设定），返回题目列表（不含标准答案）。
    """
    quiz = Quiz.query.get(quiz_id)
    if quiz is None:
        return error(message="考核不存在", code=404)

    if quiz.status != "created":
        return error(message=f"考核状态为 {quiz.status}，无法发起（仅 created 状态可发起）")

    try:
        service = QuizService()
        questions = service.generate_questions_for_quiz(quiz)
        questions_data = [
            {
                "question_id": q.id,
                "question_number": q.question_number,
                "question": q.question,
            }
            for q in questions
        ]
        return success(
            message=f"考核已开始，共 {len(questions)} 道题目",
            data={
                "quiz_id": quiz.id,
                "status": quiz.status,
                "questions": questions_data,
            },
        )
    except ValueError as exc:
        return error(message=str(exc))
    except Exception as exc:
        return error(message=f"发起考核失败: {exc}", code=500)


# ---------------------------------------------------------------------------
# 接口 3：提交答案  POST /v1/quiz/<quiz_id>/questions/<question_id>/submit
# ---------------------------------------------------------------------------
@quiz_bp.route("/v1/quiz/<quiz_id>/questions/<question_id>/submit", methods=["POST"])
def submit_answer(quiz_id: str, question_id: str):
    """提交答案

    用户提交单题答案，系统实时评分并返回反馈。

    请求体：{"answer": "用户的回答"}
    """
    data = request.get_json()

    if not data or "answer" not in data:
        return error(message="缺少 answer 参数")

    user_answer = str(data["answer"]).strip()
    if not user_answer:
        return error(message="answer 不能为空")

    # 校验考核
    quiz = Quiz.query.get(quiz_id)
    if quiz is None:
        return error(message="考核不存在", code=404)

    if quiz.status != "in_progress":
        return error(message=f"考核状态为 {quiz.status}，无法提交答案（仅 in_progress 状态可提交）")

    # 校验题目
    question = QuizQuestion.query.filter_by(id=question_id, quiz_id=quiz_id).first()
    if question is None:
        return error(message="题目不存在或不属于该考核", code=404)

    if question.user_answer is not None:
        return error(message=f"第 {question.question_number} 题已作答，不可重复提交")

    try:
        service = QuizService()
        result = service.submit_answer(question, user_answer)
        return success(message="答案提交成功", data=result)
    except ValueError as exc:
        return error(message=str(exc))
    except Exception as exc:
        return error(message=f"提交答案失败: {exc}", code=500)


# ---------------------------------------------------------------------------
# 接口 4：考核总结  GET /v1/quiz/<quiz_id>/summary  (支持流式开关)
# ---------------------------------------------------------------------------
@quiz_bp.route("/v1/quiz/<quiz_id>/summary", methods=["GET"])
def quiz_summary(quiz_id: str):
    """考核总结（支持流式/非流式）

    所有题目作答完毕后，计算总分并生成综合评价。
    - stream=true（默认）：SSE 流式返回
    - stream=false：普通 JSON 返回
    """
    quiz = Quiz.query.get(quiz_id)
    if quiz is None:
        return error(message="考核不存在", code=404)

    # 如果已经完成，直接返回已有总结
    if quiz.status == "completed" and quiz.summary:
        return success(
            message="考核已完成",
            data={
                "quiz_id": quiz.id,
                "status": quiz.status,
                "total_score": quiz.total_score,
                "summary": quiz.summary,
            },
        )

    if quiz.status != "in_progress":
        return error(message=f"考核状态为 {quiz.status}，无法生成总结（需要 in_progress 状态）")

    # 检查是否所有题目都已作答
    questions = quiz.questions
    unanswered = [q for q in questions if q.user_answer is None]
    if unanswered:
        unanswered_nums = [str(q.question_number) for q in unanswered]
        return error(message=f"第 {', '.join(unanswered_nums)} 题尚未作答，请先完成所有题目")

    service = QuizService()
    total_score = service.calculate_total_score(quiz)
    stream_param = (request.args.get("stream", "true") or "").strip().lower()
    use_stream = stream_param not in {"0", "false", "no", "off"}

    # 非流式：直接生成总结并落库
    if not use_stream:
        try:
            summary_text = service.generate_summary(quiz)
            service.complete_quiz(quiz, total_score, summary_text)
            return success(
                message="考核已完成",
                data={
                    "quiz_id": quiz.id,
                    "status": quiz.status,
                    "total_score": quiz.total_score,
                    "summary": quiz.summary,
                },
            )
        except ValueError as exc:
            return error(message=str(exc))
        except Exception as exc:
            return error(message=f"生成考核总结失败: {exc}", code=500)

    def to_sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    @stream_with_context
    def event_stream():
        # 发送元数据
        yield to_sse({
            "type": "meta",
            "quiz_id": quiz.id,
            "total_score": total_score,
        })

        full_summary = []
        try:
            # 流式生成总结
            for token in service.generate_summary_stream(quiz):
                full_summary.append(token)
                yield to_sse({"type": "delta", "content": token})

            # 持久化总结（流式结束后）
            summary_text = "".join(full_summary).strip()
            if not summary_text:
                summary_text = "本次考核已完成。"
            service.complete_quiz(quiz, total_score, summary_text)
            yield to_sse({"type": "done"})
        except Exception as exc:
            yield to_sse({"type": "error", "message": f"总结生成或保存失败: {exc}"})

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(event_stream(), mimetype="text/event-stream", headers=headers)
