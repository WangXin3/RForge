import os
from dotenv import load_dotenv

load_dotenv(".env")
if not os.getenv("DATABASE_URL"):
    # 兼容仅维护 .env.example 的本地开发场景
    load_dotenv(".env.example")


class Config:
    """基础配置"""

    SECRET_KEY = os.getenv("SECRET_KEY")

    # 数据库
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL")
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # OpenAI
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL")

    # 模型
    EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL")
    EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION"))
    LLM_MODEL = os.getenv("LLM_MODEL")

    # 文件上传
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
    MAX_CONTENT_LENGTH = int(os.getenv("MAX_CONTENT_LENGTH", 52428800))  # 50MB


class DevelopmentConfig(Config):
    """开发环境配置"""

    DEBUG = True


class ProductionConfig(Config):
    """生产环境配置"""

    DEBUG = False


config_map = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
}


def get_config():
    """根据环境变量获取配置"""
    env = os.getenv("FLASK_ENV", "development")
    return config_map.get(env, DevelopmentConfig)
