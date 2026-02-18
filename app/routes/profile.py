import os
from flask import Blueprint, render_template, request, redirect, url_for, flash
from werkzeug.utils import secure_filename
from flask import current_app
from app import db
from app.models import UserProfile, ExampleJob, ResumeVariant

profile_bp = Blueprint("profile", __name__)

ALLOWED_EXTENSIONS = {"txt", "pdf", "doc", "docx", "md"}


def _allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def _extract_pdf_text(filepath: str) -> str:
    """Extract text from a PDF file using pdfplumber."""
    try:
        import pdfplumber

        text_parts = []
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
        return "\n\n".join(text_parts)
    except Exception as e:
        return f"[PDF text extraction failed: {e}]"


def _extract_docx_text(filepath: str) -> str:
    """Extract text from a DOCX file using python-docx."""
    try:
        from docx import Document as DocxDocument

        doc = DocxDocument(filepath)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
    except Exception as e:
        return f"[DOCX text extraction failed: {e}]"


@profile_bp.route("/", methods=["GET"])
def view():
    profile = UserProfile.query.first()
    examples = []
    variants = []
    if profile:
        examples = ExampleJob.query.filter_by(user_id=profile.id).all()
        variants = ResumeVariant.query.filter_by(user_id=profile.id).all()
    return render_template("profile.html", profile=profile, examples=examples, variants=variants)


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
        elif filename.endswith(".pdf"):
            profile.resume_text = _extract_pdf_text(filepath)
        elif filename.endswith(".docx"):
            profile.resume_text = _extract_docx_text(filepath)
        else:
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


def _extract_file_text(filepath: str, filename: str) -> str:
    """Extract text from an uploaded file based on extension."""
    if filename.endswith(".txt") or filename.endswith(".md"):
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    elif filename.endswith(".pdf"):
        return _extract_pdf_text(filepath)
    elif filename.endswith(".docx"):
        return _extract_docx_text(filepath)
    return ""


@profile_bp.route("/variant/add", methods=["POST"])
def add_variant():
    profile = UserProfile.query.first()
    if not profile:
        flash("Please create your profile first.", "warning")
        return redirect(url_for("profile.view"))

    label = request.form.get("label", "").strip()
    if not label:
        flash("Variant label is required.", "warning")
        return redirect(url_for("profile.view"))

    variant = ResumeVariant(
        user_id=profile.id,
        label=label,
        target_roles=request.form.get("target_roles", "").strip(),
    )

    # Handle resume file upload
    resume_file = request.files.get("resume_file")
    if resume_file and resume_file.filename and _allowed_file(resume_file.filename):
        filename = secure_filename(resume_file.filename)
        filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
        resume_file.save(filepath)
        variant.filename = filename
        variant.resume_text = _extract_file_text(filepath, filename)

    # Handle cover letter file upload
    cl_file = request.files.get("cover_letter_file")
    if cl_file and cl_file.filename and _allowed_file(cl_file.filename):
        cl_filename = secure_filename(cl_file.filename)
        cl_filepath = os.path.join(current_app.config["UPLOAD_FOLDER"], cl_filename)
        cl_file.save(cl_filepath)
        variant.cover_letter_text = _extract_file_text(cl_filepath, cl_filename)

    db.session.add(variant)
    db.session.commit()
    flash(f"Resume variant '{label}' added.", "success")
    return redirect(url_for("profile.view"))


@profile_bp.route("/variant/<int:variant_id>/delete", methods=["POST"])
def delete_variant(variant_id):
    variant = ResumeVariant.query.get_or_404(variant_id)
    db.session.delete(variant)
    db.session.commit()
    flash("Resume variant removed.", "info")
    return redirect(url_for("profile.view"))
