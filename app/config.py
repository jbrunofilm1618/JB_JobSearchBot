import os
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv(
        "DATABASE_URL", f"sqlite:///{os.path.join(BASE_DIR, 'jobsearch.db')}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
    GENERATED_DOCS_FOLDER = os.path.join(BASE_DIR, "generated_docs")
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB upload limit
