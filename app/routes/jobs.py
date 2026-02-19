import json
import threading
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, current_app
from app import db
from app.models import Job, UserProfile, SearchConfig
from app.services.job_search import run_search, add_manual_job
from app.services.evaluator import evaluate_job, batch_evaluate
from app.services.status_checker import check_job_status, batch_check_status

# Background evaluation state (shared across requests)
_eval_state = {
    "running": False,
    "total": 0,
    "completed": 0,
    "current_job": "",
    "errors": 0,
}

jobs_bp = Blueprint("jobs", __name__)


@jobs_bp.route("/")
def list_jobs():
    status_filter = request.args.get("status", "")
    sort = request.args.get("sort", "date_found")
    direction = request.args.get("dir", "desc")

    query = Job.query

    if status_filter:
        query = query.filter_by(status=status_filter)

    if sort == "fit_score":
        order_col = Job.fit_score
    elif sort == "date_posted":
        order_col = Job.date_posted
    elif sort == "company":
        order_col = Job.company
    elif sort == "title":
        order_col = Job.title
    else:
        order_col = Job.date_found

    if direction == "asc":
        query = query.order_by(order_col.asc().nullslast())
    else:
        query = query.order_by(order_col.desc().nullsfirst())

    page = request.args.get("page", 1, type=int)
    pagination = query.paginate(page=page, per_page=25, error_out=False)

    return render_template(
        "jobs.html",
        jobs=pagination.items,
        pagination=pagination,
        status_filter=status_filter,
        sort=sort,
        direction=direction,
    )


@jobs_bp.route("/<int:job_id>")
def job_detail(job_id):
    job = Job.query.get_or_404(job_id)
    pros = json.loads(job.fit_pros) if job.fit_pros else []
    cons = json.loads(job.fit_cons) if job.fit_cons else []
    return render_template("job_detail.html", job=job, pros=pros, cons=cons)


@jobs_bp.route("/<int:job_id>/evaluate", methods=["POST"])
def evaluate(job_id):
    job = Job.query.get_or_404(job_id)
    profile = UserProfile.query.first()
    if not profile:
        flash("Please create your profile first.", "warning")
        return redirect(url_for("profile.view"))

    evaluate_job(job, profile)
    flash(f"Job evaluated — fit score: {job.fit_score}/100", "success")
    return redirect(url_for("jobs.job_detail", job_id=job.id))


@jobs_bp.route("/<int:job_id>/check-status", methods=["POST"])
def check_status(job_id):
    job = Job.query.get_or_404(job_id)
    result = check_job_status(job)
    if result is True:
        flash("Job appears to still be active.", "success")
    elif result is False:
        flash("Job appears to be closed or filled.", "danger")
    else:
        flash("Could not determine job status.", "warning")
    return redirect(url_for("jobs.job_detail", job_id=job.id))


@jobs_bp.route("/<int:job_id>/status", methods=["POST"])
def update_status(job_id):
    job = Job.query.get_or_404(job_id)
    new_status = request.form.get("status", "")
    if new_status in (
        Job.STATUS_NEW,
        Job.STATUS_REVIEWED,
        Job.STATUS_SHORTLISTED,
        Job.STATUS_APPLIED,
        Job.STATUS_REJECTED,
        Job.STATUS_CLOSED,
    ):
        job.status = new_status
        db.session.commit()
        flash(f"Status updated to '{new_status}'.", "info")
    return redirect(url_for("jobs.job_detail", job_id=job.id))


@jobs_bp.route("/<int:job_id>/notes", methods=["POST"])
def save_notes(job_id):
    job = Job.query.get_or_404(job_id)
    job.notes = request.form.get("notes", "")
    db.session.commit()
    flash("Notes saved.", "info")
    return redirect(url_for("jobs.job_detail", job_id=job.id))


@jobs_bp.route("/add", methods=["GET", "POST"])
def add_job():
    if request.method == "POST":
        job = add_manual_job(
            title=request.form.get("title", "").strip(),
            company=request.form.get("company", "").strip(),
            url=request.form.get("url", "").strip(),
            description=request.form.get("description", "").strip(),
            location=request.form.get("location", "").strip(),
            is_remote="is_remote" in request.form,
        )
        flash(f"Job '{job.title}' added.", "success")
        return redirect(url_for("jobs.job_detail", job_id=job.id))
    return render_template("add_job.html")


@jobs_bp.route("/batch-evaluate", methods=["POST"])
def batch_evaluate_jobs():
    profile = UserProfile.query.first()
    if not profile:
        flash("Please create your profile first.", "warning")
        return redirect(url_for("profile.view"))

    if _eval_state["running"]:
        flash("Evaluation already in progress.", "info")
        return redirect(url_for("jobs.eval_progress"))

    unevaluated = Job.query.filter(
        Job.fit_score.is_(None), Job.status == Job.STATUS_NEW
    ).all()

    if not unevaluated:
        flash("No new jobs to evaluate.", "info")
        return redirect(url_for("jobs.list_jobs"))

    _eval_state["running"] = True
    _eval_state["total"] = len(unevaluated)
    _eval_state["completed"] = 0
    _eval_state["current_job"] = ""
    _eval_state["errors"] = 0

    job_ids = [j.id for j in unevaluated]
    profile_id = profile.id
    app = current_app._get_current_object()

    def _run_eval():
        with app.app_context():
            p = UserProfile.query.get(profile_id)
            for jid in job_ids:
                j = db.session.get(Job, jid)
                if not j:
                    _eval_state["completed"] += 1
                    continue
                _eval_state["current_job"] = f"{j.title} at {j.company}"
                try:
                    evaluate_job(j, p)
                except Exception as e:
                    _eval_state["errors"] += 1
                    print(f"Eval error for job {jid}: {e}")
                _eval_state["completed"] += 1
            _eval_state["running"] = False

    threading.Thread(target=_run_eval, daemon=True).start()
    return redirect(url_for("jobs.eval_progress"))


@jobs_bp.route("/batch-evaluate/progress")
def eval_progress():
    return render_template("eval_progress.html", state=_eval_state)


@jobs_bp.route("/batch-evaluate/status")
def eval_status():
    return jsonify(_eval_state)


@jobs_bp.route("/batch-check-status", methods=["POST"])
def batch_check():
    results = batch_check_status()
    active = sum(1 for _, s in results if s is True)
    closed = sum(1 for _, s in results if s is False)
    flash(f"Checked {len(results)} jobs: {active} active, {closed} closed.", "info")
    return redirect(url_for("jobs.list_jobs"))


# --- Search Config ---

@jobs_bp.route("/searches")
def search_list():
    configs = SearchConfig.query.all()
    return render_template("searches.html", configs=configs)


@jobs_bp.route("/searches/add", methods=["GET", "POST"])
def add_search():
    if request.method == "POST":
        profile = UserProfile.query.first()
        if not profile:
            flash("Please create your profile first.", "warning")
            return redirect(url_for("profile.view"))

        config = SearchConfig(
            user_id=profile.id,
            name=request.form.get("name", "").strip(),
            search_terms=request.form.get("search_terms", "").strip(),
            boards=request.form.get("boards", "indeed").strip(),
            location=request.form.get("location", "").strip(),
            remote_only="remote_only" in request.form,
            results_wanted=int(request.form.get("results_wanted", 25) or 25),
            interval_hours=int(request.form.get("interval_hours", 24) or 24),
        )
        db.session.add(config)
        db.session.commit()
        flash(f"Search '{config.name}' created.", "success")
        return redirect(url_for("jobs.search_list"))
    return render_template("add_search.html")


@jobs_bp.route("/searches/run-all", methods=["POST"])
def run_all_searches():
    configs = SearchConfig.query.filter_by(is_active=True).all()
    if not configs:
        flash("No active search configurations. Create one first.", "warning")
        return redirect(url_for("jobs.search_list"))

    total_new = 0
    errors = 0
    for config in configs:
        try:
            new_jobs = run_search(config)
            total_new += len(new_jobs)
        except Exception as e:
            print(f"Error running search '{config.name}': {e}")
            errors += 1

    msg = f"Global search complete — found {total_new} new jobs across {len(configs)} searches."
    if errors:
        msg += f" ({errors} searches had errors.)"
    flash(msg, "success")
    return redirect(url_for("jobs.list_jobs", sort="date_found"))


@jobs_bp.route("/searches/<int:config_id>/run", methods=["POST"])
def run_search_now(config_id):
    config = SearchConfig.query.get_or_404(config_id)
    new_jobs = run_search(config)
    flash(f"Search complete — found {len(new_jobs)} new jobs.", "success")
    return redirect(url_for("jobs.list_jobs", sort="date_found"))


@jobs_bp.route("/searches/<int:config_id>/delete", methods=["POST"])
def delete_search(config_id):
    config = SearchConfig.query.get_or_404(config_id)
    db.session.delete(config)
    db.session.commit()
    flash("Search deleted.", "info")
    return redirect(url_for("jobs.search_list"))
