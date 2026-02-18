import os
from flask import Blueprint, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
from flask import current_app
from app import db
from app.models import UserProfile, ExampleJob

profile_bp = Blueprint("profile", __name__)

ALLOWED_EXTENSIONS = {"txt", "pdf", "doc", "docx", "md"}


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


@profile_bp.route("/", methods=["GET"])
def view():
    profile = UserProfile.query.first()
    examples = []
    if profile:
        examples = ExampleJob.query.filter_by(user_id=profile.id).all()
    return render_template("profile.html", profile=profile, examples=examples)


@profile_bp.route("/save", methods=["POST"])
def save():
    profile = UserProfile.query.first()
    if not profile:
        profile = UserProfile()
        db.session.add(profile)

    profile.name = request.form.get("name", "").strip()
    profile.email = request.form.get("email", "").strip()
    profile.phone = request.form.get("phone", "").strip()
    profile.location = request.form.get("location", "").strip()
    profile.summary = request.form.get("summary", "").strip()
    profile.skills = request.form.get("skills", "").strip()
    profile.experience_years = int(request.form.get("experience_years", 0) or 0)
    profile.desired_titles = request.form.get("desired_titles", "").strip()
    profile.desired_locations = request.form.get("desired_locations", "").strip()
    min_sal = request.form.get("min_salary", "").strip()
    profile.min_salary = int(min_sal) if min_sal else None

    # Handle resume upload
    resume_file = request.files.get("resume")
    if resume_file and resume_file.filename and _allowed_file(resume_file.filename):
        filename = secure_filename(resume_file.filename)
        filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        resume_file.save(filepath)
        profile.resume_filename = filename

        # Read text content for AI processing
        if filename.endswith(".txt") or filename.endswith(".md"):
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                profile.resume_text = f.read()
        else:
            # For PDF/DOCX, store a note that file was uploaded
            # Full parsing can be added later with python-docx or pdfplumber
            profile.resume_text = request.form.get("resume_text", "").strip()

    # Also accept pasted resume text
    pasted = request.form.get("resume_text", "").strip()
    if pasted and not profile.resume_text:
        profile.resume_text = pasted

    db.session.commit()
    flash("Profile saved successfully.", "success")
    return redirect(url_for("profile.view"))


@profile_bp.route("/example-job/add", methods=["POST"])
def add_example_job():
    profile = UserProfile.query.first()
    if not profile:
        flash("Please create your profile first.", "warning")
        return redirect(url_for("profile.view"))

    example = ExampleJob(
        user_id=profile.id,
        title=request.form.get("title", "").strip(),
        company=request.form.get("company", "").strip(),
        url=request.form.get("url", "").strip(),
        description=request.form.get("description", "").strip(),
        why_good_fit=request.form.get("why_good_fit", "").strip(),
    )
    db.session.add(example)
    db.session.commit()
    flash("Example job added.", "success")
    return redirect(url_for("profile.view"))


@profile_bp.route("/example-job/<int:example_id>/delete", methods=["POST"])
def delete_example_job(example_id):
    example = ExampleJob.query.get_or_404(example_id)
    db.session.delete(example)
    db.session.commit()
    flash("Example job removed.", "info")
    return redirect(url_for("profile.view"))
