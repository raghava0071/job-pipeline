# =============================================================================
# RAGHAV_PROFILE.EXAMPLE.PY — Candidate Profile Template
#
# HOW TO USE:
#   1. Copy this file:  cp raghav_profile.example.py raghav_profile.py
#   2. Fill in YOUR real information in raghav_profile.py
#   3. raghav_profile.py is gitignored — your personal data stays local
#
# This file is the single source of truth for ALL personal data used by the
# pipeline: resume content, form-filling answers, salary targets, Q&A defaults.
# =============================================================================

PROFILE = {
    "name":       "YOUR FULL NAME",
    "title":      "Data Engineer",                 # default job title on resume
    "phone":      "5551234567",                    # digits only, no dashes
    "email":      "your.email@gmail.com",
    "location":   "City, ST",                     # e.g. "Austin, TX"
    "linkedin":   "linkedin.com/in/your-handle",
    "github":     "github.com/your-username",
    "work_auth":  "US Citizen",                   # or "F-1 OPT / STEM OPT — No sponsorship required"
    "relocate":   True,
    "start_date": "Immediately available",
}

SALARY = {
    # Acceptable range — pipeline never answers outside these bounds
    "min": 65000,
    "max": 110000,

    # Role-based targets (used when no salary range is posted in the JD)
    "data_engineer_remote":  85000,
    "data_engineer_local":   78000,
    "data_analyst_remote":   72000,
    "data_analyst_local":    67000,
    "data_scientist_remote": 88000,
    "analytics_engineer":    80000,
    "bi_analyst":            70000,
    "default":               78000,
}

TARGET_ROLES = [
    "Data Engineer",
    "Data Analyst",
    "Data Scientist",
    "Analytics Engineer",
    "BI Analyst",
    "Azure Data Engineer",
    "Cloud Data Engineer",
    # Add more roles as needed
]

EDUCATION = [
    {
        "degree":    "Master of Science — Data Science and Analytics",  # or your degree
        "school":    "Your University Name",
        "location":  "City, ST",
        "graduated": "May 2025",
    }
]

EXPERIENCE = [
    {
        "title":    "Data Engineer",
        "company":  "Your Company Name",
        "duration": "January 2024 – Present",
        "location": "City, ST (Remote)",
        "type":     "Full-time",
        "summary":  "Brief summary of role.",
        "bullets": [
            "Built end-to-end ETL pipelines in Python ingesting data from X sources into Y warehouse",
            "Engineered Z automation reducing manual work by N%",
            # Add 4-6 strong, metric-driven bullets
        ],
        "tools": ["Python", "SQL", "Spark", "AWS", "dbt"],
        "include_always": True,
    },
    # Add more experience blocks as needed
]

SKILLS = {
    "cloud": [
        "Microsoft Azure", "Amazon Web Services (AWS)", "Google Cloud Platform (GCP)",
    ],
    "data_engineering": [
        "Apache Spark", "PySpark", "Apache Kafka", "Airflow",
        "ETL/ELT Pipelines", "Data Warehousing", "dbt",
    ],
    "programming": [
        "Python", "SQL", "Pandas", "PostgreSQL", "MySQL",
    ],
    "analytics": [
        "Power BI", "Tableau", "Looker Studio", "Google Analytics 4",
    ],
    "professional": [
        "Root Cause Analysis", "Technical Documentation", "Agile Teamwork",
    ],
}

# Flat list of ALL skills for ATS matching
ALL_SKILLS_FLAT = [s.lower() for group in SKILLS.values() for s in group]

PROJECTS = [
    {
        "name":    "Your Project Name",
        "tech":    "Python, SQL, Spark, dbt",
        "github":  "https://github.com/your-username/your-project",
        "bullets": [
            "What you built and why it matters",
            "Key technical decisions and results (include metrics)",
        ],
        "highlights": "Key Tech · Another Tech · Result",
        "include_for": [
            "data engineer", "data analyst", "analytics engineer",
        ],
    },
]

REFERENCES = [
    {
        "name":         "Reference Name",
        "title":        "Their Title",
        "company":      "Their Company",
        "relationship": "Manager / Colleague / Supervisor",
        "email":        "their.email@company.com",
    },
]

COMMON_QA = {
    # Work authorization
    "authorized":       "Yes",
    "work_authorized":  "Yes",
    "sponsorship":      "No",            # change to "Yes" if you need sponsorship
    "visa_status":      "US Citizen",    # or "F-1 STEM OPT" etc.
    "citizenship":      "Yes",

    # Job preferences
    "relocate":         "Yes",
    "remote_ok":        "Yes",
    "start_date":       "2 weeks",
    "notice_period":    "2 weeks",

    # Salary (override with SALARY dict above where possible)
    "salary_expected":  "80000",
    "hourly_rate":      "45",

    # Contact & address  ← pull from .env in production, not here
    "street_address":   "YOUR_STREET_ADDRESS",
    "city":             "YOUR_CITY",
    "state":            "YOUR_STATE_ABBR",
    "zip":              "YOUR_ZIP",
    "country":          "United States of America",
    "phone":            "5551234567",

    # Education
    "education_level":  "Master's Degree",
    "field_of_study":   "Data Science and Analytics",
    "university":       "Your University Name",
    "grad_year":        "2025",
    "gpa":              "3.8",

    # Current job
    "current_employer": "Your Current Company",
    "current_title":    "Data Engineer",
    "years_experience": "3",
}

SKILL_YEARS = {
    "python":           "4",
    "sql":              "4",
    "pandas":           "4",
    "data engineering": "3",
    "etl":              "3",
    "postgresql":       "3",
    "aws":              "2",
    "azure":            "2",
    "spark":            "2",
    "pyspark":          "2",
    "kafka":            "2",
    "dbt":              "2",
    "power bi":         "2",
    "tableau":          "2",
    "machine learning": "2",
    "docker":           "2",
    "rest api":         "3",
    # Add skills and years as needed
}
