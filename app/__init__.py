import os

from flask import Flask
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from config import get_config
from app.models.database import db
from app.utils.api_response import error, success


def create_app(config_class=None):
    """Flask 应用工厂"""
    app = Flask(__name__)

    # 加载配置
    if config_class is None:
        config_class = get_config()
    app.config.from_object(config_class)

    # 确保上传目录存在
    upload_folder = app.config.get("UPLOAD_FOLDER", "uploads")
    os.makedirs(upload_folder, exist_ok=True)

    # 初始化扩展
    CORS(app)
    db.init_app(app)

    # 注册蓝图
    from app.api.kb import kb_bp
    from app.api.chat import chat_bp
    from app.api.quiz import quiz_bp

    app.register_blueprint(kb_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(quiz_bp)

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc):
        return error(message=exc.description, code=exc.code or 500)

    @app.errorhandler(Exception)
    def handle_unexpected_exception(exc):
        app.logger.exception("未处理异常: %s", exc)
        return error(message="服务器内部错误", code=500)

    # 健康检查端点
    @app.route("/health", methods=["GET"])
    def health_check():
        return success(message="RAG 知识问答与考核系统运行中", data={"status": "ok"})

    return app
