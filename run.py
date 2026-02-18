#!/usr/bin/env python3
"""Entry point for the Job Search Bot application."""

from app import create_app
from app.scheduler import init_scheduler

app = create_app()
init_scheduler(app)

if __name__ == "__main__":
    app.run(debug=True, port=5000)
