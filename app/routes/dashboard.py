from flask import Blueprint, render_template
from app.models import Job, UserProfile, SearchConfig

dashboard_bp = Blueprint("dashboard", __name__)


@dashboard_bp.route("/")
def index():
    profile = UserProfile.query.first()
    total_jobs = Job.query.count()
    new_jobs = Job.query.filter_by(status=Job.STATUS_NEW).count()
    shortlisted = Job.query.filter_by(status=Job.STATUS_SHORTLISTED).count()
    applied = Job.query.filter_by(status=Job.STATUS_APPLIED).count()
    top_jobs = (
        Job.query.filter(Job.fit_score.isnot(None))
        .order_by(Job.fit_score.desc())
        .limit(10)
        .all()
    )
    recent_jobs = Job.query.order_by(Job.date_found.desc()).limit(10).all()
    active_searches = SearchConfig.query.filter_by(is_active=True).count()

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
    )
