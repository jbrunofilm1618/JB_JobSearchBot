from app import db
from datetime import datetime, timezone


class UserProfile(db.Model):
    __tablename__ = "user_profile"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False, default="")
    email = db.Column(db.String(200), default="")
    phone = db.Column(db.String(50), default="")
    location = db.Column(db.String(200), default="")
    summary = db.Column(db.Text, default="")
    skills = db.Column(db.Text, default="")  # comma-separated
    experience_years = db.Column(db.Integer, default=0)
    desired_titles = db.Column(db.Text, default="")  # comma-separated job titles
    desired_locations = db.Column(db.Text, default="")  # comma-separated or "remote"
    min_salary = db.Column(db.Integer, nullable=True)
    resume_text = db.Column(db.Text, default="")  # parsed resume content
    resume_filename = db.Column(db.String(300), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    search_configs = db.relationship("SearchConfig", backref="user", lazy=True)
    example_jobs = db.relationship("ExampleJob", backref="user", lazy=True)
    resume_variants = db.relationship("ResumeVariant", backref="user", lazy=True)


class ResumeVariant(db.Model):
    """Targeted resume versions for different job types."""

    __tablename__ = "resume_variant"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user_profile.id"), nullable=False)
    label = db.Column(db.String(200), nullable=False)  # e.g. "Narrative Producer"
    target_roles = db.Column(db.Text, default="")  # comma-separated role keywords
    resume_text = db.Column(db.Text, default="")
    cover_letter_text = db.Column(db.Text, default="")  # sample cover letter if available
    filename = db.Column(db.String(300), default="")
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class ExampleJob(db.Model):
    """Jobs the user considers a good fit — used as reference for AI evaluation."""

    __tablename__ = "example_job"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user_profile.id"), nullable=False)
    title = db.Column(db.String(300), nullable=False)
    company = db.Column(db.String(300), default="")
    url = db.Column(db.String(500), default="")
    description = db.Column(db.Text, default="")
    why_good_fit = db.Column(db.Text, default="")  # user's notes on why this fits
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class SearchConfig(db.Model):
    """Saved search configurations for scheduled job searches."""

    __tablename__ = "search_config"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user_profile.id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    search_terms = db.Column(db.Text, nullable=False)  # comma-separated keywords
    boards = db.Column(db.Text, default="indeed,linkedin,glassdoor,zip_recruiter")
    location = db.Column(db.String(200), default="")
    remote_only = db.Column(db.Boolean, default=False)
    results_wanted = db.Column(db.Integer, default=25)
    is_active = db.Column(db.Boolean, default=True)
    interval_hours = db.Column(db.Integer, default=24)  # how often to run
    last_run = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))


class Job(db.Model):
    __tablename__ = "job"

    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(200), default="")  # ID from job board
    source = db.Column(db.String(100), default="")  # indeed, linkedin, manual, etc.
    title = db.Column(db.String(300), nullable=False)
    company = db.Column(db.String(300), default="")
    location = db.Column(db.String(300), default="")
    url = db.Column(db.String(500), default="")
    description = db.Column(db.Text, default="")
    salary_min = db.Column(db.Integer, nullable=True)
    salary_max = db.Column(db.Integer, nullable=True)
    salary_text = db.Column(db.String(200), default="")
    job_type = db.Column(db.String(100), default="")  # full-time, part-time, contract
    is_remote = db.Column(db.Boolean, default=False)
    date_posted = db.Column(db.DateTime, nullable=True)
    date_found = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))

    # Status tracking
    STATUS_NEW = "new"
    STATUS_REVIEWED = "reviewed"
    STATUS_SHORTLISTED = "shortlisted"
    STATUS_APPLIED = "applied"
    STATUS_REJECTED = "rejected"
    STATUS_CLOSED = "closed"

    status = db.Column(db.String(50), default=STATUS_NEW)
    is_still_hiring = db.Column(db.Boolean, nullable=True)  # None = not checked
    last_status_check = db.Column(db.DateTime, nullable=True)

    # AI evaluation
    fit_score = db.Column(db.Integer, nullable=True)  # 0-100
    fit_reasoning = db.Column(db.Text, default="")
    fit_pros = db.Column(db.Text, default="")
    fit_cons = db.Column(db.Text, default="")

    # User notes
    notes = db.Column(db.Text, default="")

    # Relationships
    generated_docs = db.relationship("GeneratedDocument", backref="job", lazy=True)

    # Deduplication
    __table_args__ = (
        db.UniqueConstraint("url", name="uq_job_url"),
    )


class GeneratedDocument(db.Model):
    __tablename__ = "generated_document"

    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    doc_type = db.Column(db.String(50), nullable=False)  # "resume" or "cover_letter"
    content = db.Column(db.Text, default="")  # markdown/text content
    filename = db.Column(db.String(300), default="")  # path to generated file
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
