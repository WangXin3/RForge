"""考核模式逻辑 (LangGraph 控制流)

节点 1 (Sampler): 从 document_chunks 中随机抽取一个片段
节点 2 (Questioner): LLM 根据片段生成问题和评分标准
节点 3 (UI Wait): 挂起等待用户提交答案
节点 4 (Grader): 对比原文评估用户回答，打分并给出反馈
"""


class QuizService:
    """考核服务"""

    def __init__(self, llm_service=None):
        self.llm_service = llm_service

    def sample_chunk(self, kb_ids: list[str] | None = None) -> dict:
        """从知识库中随机抽取一个文档片段"""
        # TODO: ORDER BY random() 抽取片段
        # 如果片段信息不足以出题，返回 SKIP
        raise NotImplementedError

    def generate_question(self, chunk_content: str) -> dict:
        """根据文档片段生成考核题目和评分标准"""
        # TODO: LLM 生成问题和标准答案
        raise NotImplementedError

    def grade_answer(
        self,
        original_content: str,
        question: str,
        standard_answer: str,
        user_answer: str,
    ) -> dict:
        """智能判分

        Prompt: "你是一个严谨的导师，请对比原文评估用户回答，满分100分。"
        """
        # TODO: LLM 判分并返回分数和解析
        raise NotImplementedError

    def start_quiz(self, kb_ids: list[str] | None = None) -> dict:
        """开始一轮考核，返回题目"""
        chunk = self.sample_chunk(kb_ids)
        question_data = self.generate_question(chunk["content"])
        return {
            "quiz_id": "",  # TODO: 生成并保存考核记录
            "question": question_data["question"],
        }

    def submit_answer(self, quiz_id: str, user_answer: str) -> dict:
        """提交答案并获取评分"""
        # TODO: 查询考核记录，调用 grade_answer
        raise NotImplementedError
