"""AI-powered job fit evaluation service."""

import json
from app import db
from app.models import Job, UserProfile, ExampleJob
from app.services.ai_client import ask


def evaluate_job(job: Job, profile: UserProfile) -> dict:
    """Score a job's fit against the user's profile. Returns updated job."""
    example_jobs = ExampleJob.query.filter_by(user_id=profile.id).all()
    examples_text = ""
    if example_jobs:
        examples_text = "\n\n## Example Jobs the User Considers a Good Fit:\n"
        for ex in example_jobs:
            examples_text += f"\n### {ex.title} at {ex.company}\n"
            if ex.description:
                examples_text += f"{ex.description[:500]}\n"
            if ex.why_good_fit:
                examples_text += f"User's notes on why it's a good fit: {ex.why_good_fit}\n"

    system = (
        "You are a career advisor AI. Evaluate how well a job posting matches "
        "a candidate's profile. Be honest and specific. Return valid JSON only."
    )

    prompt = f"""Evaluate this job posting against the candidate's profile.

## Candidate Profile:
- Name: {profile.name}
- Skills: {profile.skills}
- Years of experience: {profile.experience_years}
- Desired titles: {profile.desired_titles}
- Desired locations: {profile.desired_locations}
- Minimum salary: {profile.min_salary or 'Not specified'}
- Summary: {profile.summary}

## Resume Content:
{profile.resume_text[:3000] if profile.resume_text else 'Not provided'}
{examples_text}

## Job Posting:
- Title: {job.title}
- Company: {job.company}
- Location: {job.location}
- Remote: {job.is_remote}
- Salary: {job.salary_text or 'Not listed'}
- Type: {job.job_type}
- Description:
{job.description[:4000] if job.description else 'No description available'}

Return a JSON object with these fields:
- "score": integer 0-100 (100 = perfect match)
- "reasoning": string (2-3 sentence overall assessment)
- "pros": list of strings (specific reasons this is a good fit)
- "cons": list of strings (specific concerns or mismatches)
"""

    try:
        raw = ask(prompt, system=system)
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        if text.startswith("json"):
            text = text[4:].strip()

        data = json.loads(text)
    except (json.JSONDecodeError, Exception) as e:
        return {
            "score": None,
            "reasoning": f"Evaluation failed: {e}",
            "pros": [],
            "cons": [],
        }

    job.fit_score = int(data.get("score", 0))
    job.fit_reasoning = data.get("reasoning", "")
    job.fit_pros = json.dumps(data.get("pros", []))
    job.fit_cons = json.dumps(data.get("cons", []))
    job.status = Job.STATUS_REVIEWED
    db.session.commit()

    return data


def batch_evaluate(profile: UserProfile, jobs: list[Job] | None = None):
    """Evaluate multiple jobs. If jobs is None, evaluate all un-scored new jobs."""
    if jobs is None:
        jobs = Job.query.filter(
            Job.fit_score.is_(None), Job.status == Job.STATUS_NEW
        ).all()

    results = []
    for job in jobs:
        result = evaluate_job(job, profile)
        results.append((job, result))
    return results
