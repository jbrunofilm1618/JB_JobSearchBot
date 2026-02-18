from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
import os

db = SQLAlchemy()
migrate = Migrate()


def create_app():
    app = Flask(__name__)
    app.config.from_object("app.config.Config")

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["GENERATED_DOCS_FOLDER"], exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)

    from app.routes.dashboard import dashboard_bp
    from app.routes.profile import profile_bp
    from app.routes.jobs import jobs_bp
    from app.routes.documents import documents_bp

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(profile_bp, url_prefix="/profile")
    app.register_blueprint(jobs_bp, url_prefix="/jobs")
    app.register_blueprint(documents_bp, url_prefix="/documents")

    with app.app_context():
        db.create_all()

    return app
