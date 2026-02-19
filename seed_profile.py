"""Seed script to populate Jonathan Bruno's profile, example jobs, and search configs."""

import os
import shutil
from app import create_app, db
from app.models import UserProfile, ExampleJob, SearchConfig, ResumeVariant

app = create_app()

GLOBAL_RESUME_TEXT = """Jonathan Bruno
jbrunofilm@icloud.com | 310.880.9268 | jonathan-bruno.com | Salinas, CA

CREATIVE DIRECTOR & VIDEO PRODUCTION STRATEGIST with 17+ years leading video production from concept through delivery. Proven ability to own creative vision for high-stakes launches, lead creative teams, and maintain coherence across campaigns, events, digital experiences, and thought leadership content. Background spans technology leadership (Apple), agency founding (Kensington Creative), and culturally significant storytelling (Academy Award-winning immersive work).

EXPERIENCE

PRODUCTION LEAD | CREATIVE ART DIRECTOR
Apple Inc., Worldwide Sales | Cupertino, CA | Sep 2022 - Nov 2025
- Executive Communications: Directed 7 internal sell-in videos for senior leadership (Tim Cook approval) to greenlight next-generation AI tools transforming WW Sales; served as lead script writer and voice of creative team through pre-recorded presentations
- AI Upskill Platform: Led production of 60-video AI learning platform across 3 knowledge levels and career paths; collaborated with AI Innovation team and instructional designers on curriculum development; soft launch Q4 2024, all-employee launch Q1 2025
- Production Volume: Delivered 75 yearly sales summit recordings, 50+ bespoke sales enablement stories annually, and quarterly product updates; led crews of 15-30 plus in-house team of 20 creatives; 6-12 week turnarounds from brief to publish
- Video Prototyping: Pioneered video-based prototyping to visualize AI products before app design existed; vision explorations influenced 2 customer-facing apps, including on-device sales tools that achieved double-digit conversion increase
- Technical Translation: Met with program leaders to understand business needs and technology; transformed complex AI capabilities into product pitches, scripts, and explainer videos using live action, 3D, and motion graphics
- Budget Performance: Delivered 15-20% under budget consistently while maintaining quality; built scalable processes to meet rapid launch cadence

VIDEO LEAD FILMMAKER
Apple Inc., Worldwide Sales | Cupertino, CA | Dec 2017 - Sep 2022
- Digital Transformation: Led creative transition of in-person sales summits and conferences into digital experiences during 2020, resulting in three orders of magnitude increase in reach while maintaining brand coherence
- Studio Infrastructure: Built in-house video studio from ground up: equipment selection, set design, workflow development, and vendor ecosystem
- End-to-End Production: Managed full production lifecycle from concept through post-production: training videos, testimonials, promotional content, and event materials

FOUNDER | CREATIVE DIRECTOR
Intrepid Media LLC | Salinas, CA | Nov 2025 - Present
- Creative production consultancy specializing in video strategy, brand development, and AI-integrated workflows for businesses scaling their content operations

CO-FOUNDER | CREATIVE DIRECTOR
Kensington Creative Inc. | Los Angeles, CA | Apr 2013 - Dec 2017
- Founded and built creative agency; led creative for L'Oreal, Taco Bell, 7Up, Warner Bros, A&E, ABC across campaigns, digital content, and brand initiatives; improved margins 25%
- Directed launch campaigns and promotional content; managed crews of 15-50; owned creative decisions from brief through delivery

DIRECTOR OF PHOTOGRAPHY
Freelance | Los Angeles, CA | Jun 2009 - Dec 2017
- DP and camera operator across 100+ productions including features, TV series, commercials, music videos, and VR content
- Clients include Warner Bros, NBC Universal, Sony Pictures, Netflix, Amazon Studios, Disney, A&E, Starz, MTV, Lifetime, Riot Games, AT&T, Uber, L'Oreal, VEVO

SELECT PROJECTS
- Carne y Arena (Performance Capture Lead) - Alejandro Gonzalez Inarritu | Academy Award-winning immersive VR installation
- God of War, Uncharted 4, The Last of Us 2 (Mocap Video Lead) - Sony Interactive Entertainment
- Bone Tomahawk (A Camera Operator & 2nd Unit DP) - Kurt Russell-led Western feature | 91% Rotten Tomatoes

EXPERTISE
Creative Leadership: Brand vision and stewardship, creative strategy, team building and mentorship, cross-functional collaboration, executive stakeholder communication
Writing & Editorial: Script writing, executive presentations, video narration, brand voice development
Production: Hands-on DP and camera operation; lighting and audio; compressed timelines; VR/360, performance capture, 3D/stereoscopic, virtual volume production
Post-Production: Editorial direction, color grading (DaVinci Resolve), sound design supervision; hands-on editing (Premiere Pro, After Effects)
Technical: Adobe Creative Suite, DaVinci Resolve, Figma, Keynote, Frame.io, Airtable; AI workflow implementation (Runway, Midjourney, ChatGPT, Claude, Claude Code)

EDUCATION
Chapman University - Bachelor of Fine Arts, Film & Media Production (Cinematography Emphasis), Minor in Philosophy
Eastman Kodak Grant (2009, 2010) | Panavision Grant (2009, 2010)
"""


def seed():
    with app.app_context():
        # --- Profile ---
        profile = UserProfile.query.first()
        if not profile:
            profile = UserProfile()
            db.session.add(profile)

        profile.name = "Jonathan Bruno"
        profile.email = "jbrunofilm@icloud.com"
        profile.phone = "310.880.9268"
        profile.location = "Salinas, CA"
        profile.summary = (
            "Creative Director & Video Production Strategist with 17+ years "
            "leading video production from concept through delivery. Proven ability "
            "to own creative vision for high-stakes launches, lead creative teams, "
            "and maintain coherence across campaigns, events, digital experiences, "
            "and thought leadership content. Background spans technology leadership "
            "(Apple), agency founding (Kensington Creative), and culturally "
            "significant storytelling (Academy Award-winning immersive work). "
            "Deeply interested in the societal implications of AI and committed "
            "to helping communicate what beneficial AI can become."
        )
        profile.skills = (
            "Creative Direction, Video Production, Script Writing, "
            "Executive Communications, Brand Strategy, Team Leadership, "
            "Cinematography, Color Grading, Motion Graphics Direction, "
            "AI Workflow Implementation, Technical Translation, "
            "Agency & Vendor Management, Budget Management, "
            "Adobe Creative Suite, DaVinci Resolve, Figma, Keynote, "
            "Frame.io, Airtable, Premiere Pro, After Effects, "
            "VR/360, Performance Capture, 3D/Stereoscopic, "
            "Claude, Claude Code, Runway, Midjourney"
        )
        profile.experience_years = 17
        profile.desired_titles = (
            "Creative Director, Video Producer, Senior Video Producer, "
            "Production Lead, Video Director, Narrative Producer, "
            "Video Production Manager, Head of Video, "
            "Director of Video Production, Content Director, "
            "Creative Production Lead, Video Strategist"
        )
        profile.desired_locations = "Bay Area, San Francisco, Remote, Salinas CA, Los Angeles"
        profile.min_salary = 150000
        profile.resume_text = GLOBAL_RESUME_TEXT.strip()
        profile.resume_filename = "Jonathan_Bruno_Resume.pdf"

        db.session.commit()
        print(f"Profile created/updated: {profile.name} (id={profile.id})")

        # --- Copy resume files to uploads/ ---
        uploads_dir = app.config["UPLOAD_FOLDER"]
        resume_files = [
            "Jonathan_Bruno_Resume.pdf",
            "Jonathan_Bruno_Resume.docx",
            "Jonathan_Bruno_Resume_Anthropic.pdf",
            "Jonathan_Bruno_Resume_Anthropic_VideoDirector.pdf",
            "Jonathan_Bruno_Resume_SrVideoProducer_Apple.pdf",
            "Jonathan_Bruno_Resume_SrVideoProducer_Apple.docx",
            "Resume_NarrativeProducer.docx",
            "Resume_ProductLaunches.docx",
            "CoverLetter_NarrativeProducer.docx",
            "CoverLetter_ProductLaunches.docx",
        ]
        base_dir = os.path.dirname(os.path.abspath(__file__))
        for fname in resume_files:
            src = os.path.join(base_dir, fname)
            dst = os.path.join(uploads_dir, fname)
            if os.path.exists(src) and not os.path.exists(dst):
                shutil.copy2(src, dst)
                print(f"  Copied {fname} -> uploads/")

        # --- Example Good-Fit Jobs ---
        existing_examples = ExampleJob.query.filter_by(user_id=profile.id).all()
        existing_titles = {e.title for e in existing_examples}

        example_jobs = [
            {
                "title": "Video Producer, Product Launches",
                "company": "Anthropic",
                "url": "",
                "description": (
                    "Video Producer role focused on producing product launch videos, "
                    "managing agency/vendor ecosystems, receiving briefs from marketing "
                    "stakeholders, and delivering high-quality content on time and on budget "
                    "for an AI company building safe, beneficial AI."
                ),
                "why_good_fit": (
                    "8 years at Apple doing exactly this: receiving briefs from stakeholders, "
                    "managing agency ecosystems, delivering product launch content. "
                    "Deep understanding of production operations and vendor management. "
                    "Passion for AI and specifically Anthropic's mission."
                ),
            },
            {
                "title": "Video Narrative Producer",
                "company": "Anthropic",
                "url": "",
                "description": (
                    "Producer role focused on narrative storytelling about AI research "
                    "and the people behind it. Documentary-style content, thought leadership "
                    "videos, and creative storytelling that makes complex AI topics accessible "
                    "to diverse audiences."
                ),
                "why_good_fit": (
                    "Core strength is walking into unfamiliar rooms, earning trust with experts, "
                    "finding the story, and bringing it to life. Did this at Apple translating "
                    "AI capabilities into compelling video content. Academy Award-winning "
                    "immersive work demonstrates narrative range. Documentary series for Booksy. "
                    "Deep personal interest in AI's societal implications."
                ),
            },
            {
                "title": "Senior Video Producer",
                "company": "Apple",
                "url": "",
                "description": (
                    "Senior video production role at a major tech company managing end-to-end "
                    "video pipeline for global organization. Post-production oversight, "
                    "DAM/CMS management, vendor ecosystem, production workflow design."
                ),
                "why_good_fit": (
                    "8 years at Apple in this exact function. Built the studio, vendor roster, "
                    "and production pipeline from scratch. Deep knowledge of Apple's production "
                    "standards and workflows. Managed 100+ annual productions."
                ),
            },
        ]

        for ej in example_jobs:
            if ej["title"] not in existing_titles:
                example = ExampleJob(user_id=profile.id, **ej)
                db.session.add(example)
                print(f"  Added example job: {ej['title']} at {ej['company']}")

        db.session.commit()

        # --- Search Configurations ---
        existing_configs = SearchConfig.query.filter_by(user_id=profile.id).all()
        existing_config_names = {c.name for c in existing_configs}

        search_configs = [
            {
                "name": "Video Producer - Tech Companies",
                "search_terms": "video producer, senior video producer, video production manager",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "San Francisco Bay Area",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Creative Director - Video/Content",
                "search_terms": "creative director video, creative director content, head of video",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "San Francisco Bay Area",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Video Producer - Remote",
                "search_terms": "video producer remote, creative producer remote, narrative producer",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "",
                "remote_only": True,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Production Lead - AI/Tech",
                "search_terms": "production lead AI, video director tech, content director technology",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "San Francisco Bay Area",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Video Producer - Monterey County",
                "search_terms": "video producer, senior video producer, video production manager",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "Monterey County, CA",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Creative Director - Monterey County",
                "search_terms": "creative director video, creative director content, head of video",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "Monterey County, CA",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Production Lead - Monterey County",
                "search_terms": "production lead, video director, content director, creative producer",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "Monterey County, CA",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            # --- Fortune 500 / Major Tech Company Searches ---
            {
                "name": "Fortune 500 - Video/Creative (FAANG)",
                "search_terms": "video producer Apple, video producer Google, video producer Meta, creative director Amazon, video producer Netflix",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Fortune 500 - Video/Creative (AI Companies)",
                "search_terms": "video producer Anthropic, video producer OpenAI, creative director Salesforce, video producer Adobe, video producer Microsoft",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Fortune 500 - Video/Creative (Media & Entertainment)",
                "search_terms": "video producer Disney, video producer Warner Bros, creative director Sony, video producer Paramount, video producer NBC Universal",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Fortune 500 - Video/Creative (Tech & SaaS)",
                "search_terms": "video producer Nvidia, creative director Uber, video producer Airbnb, video producer Spotify, video producer LinkedIn",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
            {
                "name": "Fortune 500 - Video/Creative (Finance & Consulting)",
                "search_terms": "video producer Goldman Sachs, creative director McKinsey, video producer JPMorgan, video producer Deloitte, video producer Accenture",
                "boards": "indeed,linkedin,glassdoor,zip_recruiter,google",
                "location": "",
                "remote_only": False,
                "results_wanted": 25,
                "interval_hours": 12,
            },
        ]

        for sc in search_configs:
            if sc["name"] not in existing_config_names:
                config = SearchConfig(user_id=profile.id, **sc)
                db.session.add(config)
                print(f"  Added search config: {sc['name']}")

        db.session.commit()

        # --- Resume Variants ---
        existing_variants = ResumeVariant.query.filter_by(user_id=profile.id).all()
        existing_labels = {v.label for v in existing_variants}

        def _read_docx(filename):
            from docx import Document as DocxDocument
            path = os.path.join(base_dir, filename)
            if not os.path.exists(path):
                return ""
            doc = DocxDocument(path)
            return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

        variants = [
            {
                "label": "Narrative Producer",
                "target_roles": "narrative producer, documentary producer, storytelling, editorial, content producer",
                "resume_text": _read_docx("Resume_NarrativeProducer.docx"),
                "cover_letter_text": _read_docx("CoverLetter_NarrativeProducer.docx"),
                "filename": "Resume_NarrativeProducer.docx",
            },
            {
                "label": "Product Launch Producer",
                "target_roles": "video producer, production manager, product launch, agency management, vendor management",
                "resume_text": _read_docx("Resume_ProductLaunches.docx"),
                "cover_letter_text": _read_docx("CoverLetter_ProductLaunches.docx"),
                "filename": "Resume_ProductLaunches.docx",
            },
            {
                "label": "Creative Director (Anthropic)",
                "target_roles": "creative director, brand, AI communication, thought leadership, creative strategy",
                "resume_text": _read_docx("Jonathan_Bruno_Resume.docx"),
                "cover_letter_text": "",
                "filename": "Jonathan_Bruno_Resume_Anthropic.pdf",
            },
            {
                "label": "Video Director (Anthropic)",
                "target_roles": "video director, director, launch videos, keynote, campaigns, directing",
                "resume_text": "",  # PDF — text stored at profile level
                "cover_letter_text": "",
                "filename": "Jonathan_Bruno_Resume_Anthropic_VideoDirector.pdf",
            },
            {
                "label": "Senior Video Producer (Apple-style)",
                "target_roles": "senior video producer, post-production, DAM, CMS, pipeline, workflow, technical producer",
                "resume_text": _read_docx("Jonathan_Bruno_Resume_SrVideoProducer_Apple.docx"),
                "cover_letter_text": "",
                "filename": "Jonathan_Bruno_Resume_SrVideoProducer_Apple.docx",
            },
        ]

        for v in variants:
            if v["label"] not in existing_labels:
                variant = ResumeVariant(user_id=profile.id, **v)
                db.session.add(variant)
                print(f"  Added resume variant: {v['label']}")

        db.session.commit()
        print("\nSeed complete!")


if __name__ == "__main__":
    seed()
