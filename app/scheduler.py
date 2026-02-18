"""Background scheduler for automatic job searches."""

from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timezone, timedelta


scheduler = BackgroundScheduler()
_app = None


def init_scheduler(app):
    """Initialize and start the background scheduler."""
    global _app
    _app = app

    scheduler.add_job(
        _run_due_searches,
        "interval",
        minutes=30,
        id="job_search_runner",
        replace_existing=True,
    )
    scheduler.start()
    app.logger.info("Background scheduler started (checks every 30 minutes).")


def _run_due_searches():
    """Check for search configs that are due and run them."""
    if not _app:
        return

    with _app.app_context():
        from app.models import SearchConfig
        from app.services.job_search import run_search

        now = datetime.now(timezone.utc)
        configs = SearchConfig.query.filter_by(is_active=True).all()

        for config in configs:
            if config.last_run is None:
                is_due = True
            else:
                next_run = config.last_run + timedelta(hours=config.interval_hours)
                is_due = now >= next_run

            if is_due:
                try:
                    new_jobs = run_search(config)
                    _app.logger.info(
                        f"Search '{config.name}': found {len(new_jobs)} new jobs."
                    )
                except Exception as e:
                    _app.logger.error(f"Search '{config.name}' failed: {e}")
