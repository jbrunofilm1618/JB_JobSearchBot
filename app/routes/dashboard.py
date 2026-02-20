from flask import Blueprint, render_template
from datetime import datetime, timezone
from app.models import Job, UserProfile, SearchConfig

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    profile = UserProfile.query.first()
    total_jobs = Job.query.count()
    new_jobs = Job.query.filter_by(status=Job.STATUS_NEW).count()
    shortlisted = Job.query.filter_by(status=Job.STATUS_SHORTLISTED).count()
    applied = Job.query.filter_by(status=Job.STATUS_APPLIED).count()

    # Top matches: exclude applied, rejected, and closed jobs
    top_jobs = (
        Job.query.filter(
            Job.fit_score.isnot(None),
            Job.status.notin_([Job.STATUS_APPLIED, Job.STATUS_REJECTED, Job.STATUS_CLOSED]),
        )
        .order_by(Job.fit_score.desc())
        .limit(10)
        .all()
    )

    recent_jobs = Job.query.order_by(Job.date_found.desc()).limit(10).all()
    active_searches = SearchConfig.query.filter_by(is_active=True).count()

    # Applied jobs with follow-up tracking
    applied_jobs = (
        Job.query.filter_by(status=Job.STATUS_APPLIED)
        .order_by(Job.date_applied.desc().nullslast())
        .all()
    )
    now = datetime.now(timezone.utc)
    applied_tracking = []
    for job in applied_jobs:
        if job.date_applied:
            days_since = (now - job.date_applied).days
            # Follow up every 7 days
            needs_followup = days_since >= 7 and (days_since % 7) < 3
            days_until_followup = 7 - (days_since % 7) if not needs_followup else 0
        else:
            days_since = None
            needs_followup = False
            days_until_followup = None
        applied_tracking.append({
            "job": job,
            "days_since": days_since,
            "needs_followup": needs_followup,
            "days_until_followup": days_until_followup,
        })

    return render_template(
        "dashboard.html",
        profile=profile,
        total_jobs=total_jobs,
        new_jobs=new_jobs,
        shortlisted=shortlisted,
        applied=applied,
        top_jobs=top_jobs,
        recent_jobs=recent_jobs,
        active_searches=active_searches,
        applied_tracking=applied_tracking,
    )
