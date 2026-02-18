"""Check whether a job posting is still active/hiring."""

import requests
from datetime import datetime, timezone
from app import db
from app.models import Job


def check_job_status(job: Job) -> bool | None:
    """Check if a job URL is still live. Returns True/False/None (if unsure)."""
    if not job.url:
        return None

    try:
        resp = requests.head(
            job.url,
            timeout=15,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            },
        )
        # If we get a 200, the page is still up
        if resp.status_code == 200:
            job.is_still_hiring = True
        elif resp.status_code == 404:
            job.is_still_hiring = False
            job.status = Job.STATUS_CLOSED
        elif resp.status_code == 403:
            # Many job boards block HEAD requests; try GET
            resp = requests.get(
                job.url,
                timeout=15,
                allow_redirects=True,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    )
                },
            )
            if resp.status_code == 200:
                body = resp.text.lower()
                closed_indicators = [
                    "this job is no longer available",
                    "position has been filled",
                    "this listing has expired",
                    "job not found",
                    "no longer accepting applications",
                ]
                if any(indicator in body for indicator in closed_indicators):
                    job.is_still_hiring = False
                    job.status = Job.STATUS_CLOSED
                else:
                    job.is_still_hiring = True
            else:
                job.is_still_hiring = None
        else:
            job.is_still_hiring = None

    except requests.RequestException:
        job.is_still_hiring = None

    job.last_status_check = datetime.now(timezone.utc)
    db.session.commit()
    return job.is_still_hiring


def batch_check_status(jobs: list[Job] | None = None):
    """Check status for a batch of jobs."""
    if jobs is None:
        jobs = Job.query.filter(
            Job.status.notin_([Job.STATUS_CLOSED, Job.STATUS_REJECTED])
        ).all()

    results = []
    for job in jobs:
        status = check_job_status(job)
        results.append((job, status))
    return results
