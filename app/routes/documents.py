import os
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    send_from_directory,
    current_app,
)
from app import db
from app.models import Job, UserProfile, GeneratedDocument
from app.services.doc_generator import generate_resume, generate_cover_letter

documents_bp = Blueprint("documents", __name__)


@documents_bp.route("/")
def list_documents():
    docs = (
        GeneratedDocument.query.order_by(GeneratedDocument.created_at.desc()).all()
    )
    return render_template("documents.html", docs=docs)


@documents_bp.route("/generate/<int:job_id>", methods=["GET", "POST"])
def generate(job_id):
    job = Job.query.get_or_404(job_id)
    profile = UserProfile.query.first()
    if not profile:
        flash("Please create your profile first.", "warning")
        return redirect(url_for("profile.view"))

    if request.method == "POST":
        doc_type = request.form.get("doc_type", "resume")
        if doc_type == "cover_letter":
            doc = generate_cover_letter(job, profile)
            flash("Cover letter generated.", "success")
        else:
            doc = generate_resume(job, profile)
            flash("Resume generated.", "success")
        return redirect(url_for("documents.view_document", doc_id=doc.id))

    existing = GeneratedDocument.query.filter_by(job_id=job.id).all()
    return render_template("generate.html", job=job, existing=existing)


@documents_bp.route("/<int:doc_id>")
def view_document(doc_id):
    doc = GeneratedDocument.query.get_or_404(doc_id)
    job = Job.query.get(doc.job_id)
    return render_template("view_document.html", doc=doc, job=job)


@documents_bp.route("/<int:doc_id>/download")
def download_document(doc_id):
    doc = GeneratedDocument.query.get_or_404(doc_id)
    if not doc.filename:
        flash("No file available for download.", "warning")
        return redirect(url_for("documents.view_document", doc_id=doc.id))
    return send_from_directory(
        current_app.config["GENERATED_DOCS_FOLDER"],
        doc.filename,
        as_attachment=True,
    )


@documents_bp.route("/<int:doc_id>/delete", methods=["POST"])
def delete_document(doc_id):
    doc = GeneratedDocument.query.get_or_404(doc_id)
    if doc.filename:
        filepath = os.path.join(
            current_app.config["GENERATED_DOCS_FOLDER"], doc.filename
        )
        if os.path.exists(filepath):
            os.remove(filepath)
    db.session.delete(doc)
    db.session.commit()
    flash("Document deleted.", "info")
    return redirect(url_for("documents.list_documents"))
