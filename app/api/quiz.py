from flask import Blueprint, request

from app.utils.api_response import error, success

quiz_bp = Blueprint("quiz", __name__)


@quiz_bp.route("/v1/quiz/start", methods=["GET"])
def start_quiz():
    """开始考核

    从选定知识库中随机抽取内容，生成考核题目。
    """
    # TODO: 实现考核题目生成逻辑
    return success(
        message="考核功能开发中",
        data={
            "quiz_id": "",
            "question": "",
        },
    )


@quiz_bp.route("/v1/quiz/submit", methods=["POST"])
def submit_quiz():
    """提交考核答案

    接收用户答案，LLM 对比原文进行评分。
    """
    data = request.get_json()

    if not data or "quiz_id" not in data or "answer" not in data:
        return error(message="缺少 quiz_id 或 answer 参数")

    quiz_id = data["quiz_id"]
    answer = data["answer"]

    # TODO: 实现智能判分逻辑
    return success(
        message="判分功能开发中",
        data={
            "quiz_id": quiz_id,
            "score": 0,
            "feedback": "",
        },
    )
