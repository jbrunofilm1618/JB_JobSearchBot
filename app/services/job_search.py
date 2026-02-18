"""Job search service using python-jobspy to search multiple boards."""

from datetime import datetime, timezone
from jobspy import scrape_jobs
from app import db
from app.models import Job, SearchConfig


BOARD_MAP = {
    "indeed": "indeed",
    "linkedin": "linkedin",
    "glassdoor": "glassdoor",
    "zip_recruiter": "zip_recruiter",
}


def run_search(config: SearchConfig) -> list[Job]:
    """Execute a search configuration and return newly found jobs."""
    boards = [
        BOARD_MAP[b.strip()]
        for b in config.boards.split(",")
        if b.strip() in BOARD_MAP
    ]
    if not boards:
        boards = ["indeed"]

    search_terms = [t.strip() for t in config.search_terms.split(",") if t.strip()]
    new_jobs = []

    for term in search_terms:
        try:
            results = scrape_jobs(
                site_name=boards,
                search_term=term,
                location=config.location or None,
                results_wanted=config.results_wanted,
                is_remote=config.remote_only,
                country_indeed="USA",
            )
        except Exception as e:
            print(f"Search error for '{term}': {e}")
            continue

        for _, row in results.iterrows():
            job_url = str(row.get("job_url", "")) or ""
            if not job_url:
                continue

            # Skip duplicates
            existing = Job.query.filter_by(url=job_url).first()
            if existing:
                continue

            job = Job(
                external_id=str(row.get("id", "")),
                source=str(row.get("site", "")),
                title=str(row.get("title", "")),
                company=str(row.get("company", "")),
                location=str(row.get("location", "")),
                url=job_url,
                description=str(row.get("description", "")),
                salary_text=_build_salary_text(row),
                salary_min=_safe_int(row.get("min_amount")),
                salary_max=_safe_int(row.get("max_amount")),
                job_type=str(row.get("job_type", "")),
                is_remote=bool(row.get("is_remote", False)),
                date_posted=_parse_date(row.get("date_posted")),
            )
            db.session.add(job)
            new_jobs.append(job)

    config.last_run = datetime.now(timezone.utc)
    db.session.commit()
    return new_jobs


def add_manual_job(title, company, url, description, location="", is_remote=False):
    """Add a job manually (e.g. from a URL the user pastes in)."""
    existing = Job.query.filter_by(url=url).first() if url else None
    if existing:
        return existing

    job = Job(
        source="manual",
        title=title,
        company=company,
        url=url,
        description=description,
        location=location,
        is_remote=is_remote,
    )
    db.session.add(job)
    db.session.commit()
    return job


def _safe_int(val):
    try:
        return int(float(val)) if val and str(val) != "nan" else None
    except (ValueError, TypeError):
        return None


def _parse_date(val):
    if val is None or str(val) == "NaT" or str(val) == "nan":
        return None
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val))
    except (ValueError, TypeError):
        return None


def _build_salary_text(row):
    min_amt = _safe_int(row.get("min_amount"))
    max_amt = _safe_int(row.get("max_amount"))
    interval = str(row.get("interval", ""))
    if min_amt and max_amt:
        return f"${min_amt:,} - ${max_amt:,} {interval}".strip()
    if min_amt:
        return f"${min_amt:,}+ {interval}".strip()
    if max_amt:
        return f"Up to ${max_amt:,} {interval}".strip()
    return ""
