# =============================================================================
# RAGHAVENDRA KARANAM — MASTER PROFILE
# Single source of truth for all job application automation
# DO NOT include Community Dreams Foundation or Mobile Stage Pros
# =============================================================================

PROFILE = {
    "name": "Your Name",
    "title": "Data Engineer",
    "phone": "(555) 555-5555",
    "email": "your.email@example.com",
    "location": "City, ST",
    "linkedin": "linkedin.com/in/yourusername",
    "github": "github.com/raghava0071",
    "work_auth": "F-1 OPT / STEM OPT — No sponsorship required",
    "relocate": False,   # Fixed: was True but COMMON_QA had "No" — now consistent
    "start_date": "Immediately available",
}

SALARY = {
    # Personal acceptable range — never answer outside these bounds
    "min":                    62000,
    "max":                    95000,

    # Role-based targets (used when no salary range is posted in the JD)
    # Set conservatively so we pass screening — negotiate upward at offer stage
    "data_engineer_remote":   78000,
    "data_engineer_local":    72000,
    "data_analyst_remote":    68000,
    "data_analyst_local":     64000,
    "data_scientist_remote":  80000,
    "analytics_engineer":     76000,
    "bi_analyst":             67000,
    "default":                72000,
}

TARGET_ROLES = [
    "Data Engineer",
    "Data Analyst",
    "Data Scientist",
    "Analytics Engineer",
    "BI Analyst",
    "Azure Data Engineer",
    "Cloud Data Engineer",
]

EDUCATION = [
    {
        "degree": "Master of Science — Data Science and Analytics",
        "school": "Florida Atlantic University (FAU)",
        "location": "Boca Raton, FL",
        "graduated": "May 2025",
    }
]

EXPERIENCE = [
    {
        "title": "Data Engineer",
        "company": "Knowvia Tech Inc",
        "duration": "April 2026 – Present",
        "location": "Remote, USA",
        "type": "Full-time",
        "summary": (
            "Building data pipelines, automation systems, and analytics infrastructure "
            "as part of OPT-compliant data engineering role."
        ),
        "bullets": [
            # Lead with the most technical / impressive bullet
            "Designed and deployed end-to-end ETL/ELT pipelines in Python ingesting, "
            "normalizing, and deduplicating 300+ records per run across multiple data "
            "sources into SQLite; integrated automated scoring, data quality checks, "
            "and scheduled reporting for continuous pipeline observability",
            "Built LLM-powered data processing system integrating Claude AI API and "
            "REST APIs with a SQLite answer-caching layer — achieving 79% cache hit "
            "rate and reducing API calls by 3x while maintaining contextual accuracy at scale",
            "Engineered Playwright browser-automation framework for structured data "
            "extraction across multi-step dynamic web forms; handled session management, "
            "anti-detection, and form-state persistence across 40+ search query patterns",
            "Designed normalized PostgreSQL schema for time-series pricing data; wrote "
            "stored procedures and analytical SQL queries surfacing fare anomalies and "
            "booking-window patterns via Amadeus Flight Offers REST API",
            "Built computer vision data pipeline: ingested and labeled 896 CCTV video "
            "clips, engineered behavioral risk-scoring algorithm, and automated timestamped "
            "alert generation with structured CSV event logs for downstream ML training",
            "Implemented structured run logging, per-pipeline data quality monitoring, "
            "and automated email notification system — reducing mean time to detect "
            "pipeline failures from hours to under 5 minutes",
        ],
        "tools": [
            "Python", "SQL", "SQLite", "PostgreSQL", "ETL/ELT Pipelines",
            "REST APIs", "LLM Integration", "Playwright", "Pandas", "Streamlit",
            "GitHub Actions", "Data Quality", "Automated Reporting",
        ],
        "include_always": True,
    },
    {
        "title": "Analytics & Data Specialist",
        "company": "Florida Youth At Risk",
        "duration": "July 2025 – March 2026",
        "location": "Boca Raton, FL (Hybrid)",
        "type": "Part-time",
        "summary": (
            "Built analytics infrastructure and automated reporting pipelines for a "
            "nonprofit, replacing manual processes with data-driven systems."
        ),
        "bullets": [
            "Architected GA4 event-tracking schema with custom dimensions and conversion "
            "funnels; built automated Looker Studio reporting pipeline that eliminated "
            "80% of manual monthly reporting effort",
            "Designed and ran A/B test framework across landing pages — analyzed results "
            "with Python/SQL, driving a 35% improvement in program enrollment conversion",
            "Built SQL queries and data pipelines to aggregate campaign performance across "
            "channels (Google Ads, Search Console, GA4) into unified leadership dashboards",
            "Managed $10K Google Ad Grant data strategy — tracked attribution, optimized "
            "targeting using conversion data, and produced ROI reports for board review",
        ],
        "tools": [
            "Google Analytics 4", "Google Tag Manager", "Google Ads",
            "Google Search Console", "Python", "SQL", "Looker Studio",
        ],
        "include_always": True,
    },
    {
        "title": "Data Analytics Associate",
        "company": "Management Information Systems Association (MISA), FAU",
        "duration": "January 2024 – May 2025",
        "location": "Boca Raton, FL (Hybrid)",
        "type": "Part-time",
        "summary": "",
        "bullets": [
            "Built and maintained GA4 analytics infrastructure with custom event tracking, "
            "conversion funnels, and goal configuration for a university tech association",
            "Developed SQL queries and Python scripts to analyze traffic patterns and "
            "surface high-impact optimization opportunities across web properties",
            "Created data dashboards in Looker Studio tracking engagement KPIs (bounce "
            "rate, session duration, traffic sources, conversion paths) for leadership",
            "Delivered monthly data reports with trend analysis and A/B test findings, "
            "translating raw metrics into actionable recommendations for decision-makers",
        ],
        "tools": [
            "Google Analytics 4", "Google Tag Manager", "Google Search Console",
            "Looker Studio", "SQL", "Python",
        ],
        "include_always": True,
    },
    {
        "title": "Research Intern — IoT & Data Systems",
        "company": "IIITDM Kancheepuram",
        "duration": "January 2023 – April 2023",
        "location": "India (On-site)",
        "type": "Full-time Internship",
        "summary": "",
        "bullets": [
            "Developed a portable device for continuous real-time measurement of integrated "
            "PV (solar) module performance",
            "Collected, structured, and analyzed performance data; compared outputs across "
            "multiple PV module configurations",
        ],
        "tools": ["Arduino IDE", "Microsoft Excel", "Data Collection & Analysis"],
        "include_always": True,
    },
]

SKILLS = {
    "azure": [
        "Azure Data Factory (ADF)", "ADLS Gen2", "Azure SQL Database",
        "Azure SQL Server", "SSIS", "Azure-SSIS Integration Runtime",
        "Microsoft Azure",
    ],
    "data_engineering": [
        "Apache Spark", "PySpark", "SparkSQL", "Apache Kafka", "Hadoop",
        "ETL/ELT Pipelines", "Data Warehousing", "Schema Design",
        "Data Partitioning", "Real-time Processing", "Batch Processing",
        "Data Lineage", "RBAC", "Performance Tuning",
    ],
    "programming": [
        "Python", "Pandas", "SQL", "MySQL", "PostgreSQL", "T-SQL", "SparkSQL",
    ],
    "cloud": [
        "Microsoft Azure", "Amazon Web Services (AWS)",
        "Google Cloud Platform (GCP)",
    ],
    "databases": [
        "MySQL", "PostgreSQL", "NoSQL", "Database Tuning",
        "Indexing", "Access Control",
    ],
    "analytics": [
        "Power BI", "Google Analytics 4", "Google Tag Manager",
        "Data Studio", "A/B Testing", "KPI Development",
        "SEO Analytics", "Google Ads",
    ],
    "professional": [
        "Root Cause Analysis", "Technical Documentation",
        "Cross-functional Collaboration", "Problem Solving",
        "Agile Teamwork", "Attention to Detail",
    ],
}

# Flat list of ALL skills for ATS matching
ALL_SKILLS_FLAT = [s.lower() for group in SKILLS.values() for s in group]

PROJECTS = [
    # ── Tier 1: Strongest — show first, picked most often ─────────────────────

    {
        "name": "Real-Time Shoplifting Detection System",
        "tech": "YOLOv8, DeepSORT, ByteTrack, OpenCV, Streamlit, Python",
        "github": "https://github.com/raghava0071/cctv-shoplifting",
        "bullets": [
            "Built end-to-end real-time computer vision pipeline using YOLOv8 pose estimation "
            "and DeepSORT multi-object tracking to detect shoplifting behavior from CCTV footage",
            "Engineered behavioral risk scoring algorithm combining zone detection, item overlap, "
            "occlusion, and movement velocity — generating timestamped alert clips and CSV event logs",
            "Labeled 896 model-generated alert clips and extracted 538 false positives for "
            "hard-negative mining, improving model precision for production deployment",
            "Deployed Streamlit human-review dashboard enabling analysts to validate, tag, "
            "and retrain on flagged detection events",
        ],
        "highlights": "YOLOv8 · DeepSORT · Computer Vision · Real-time ML · Streamlit · OpenCV",
        "include_for": [
            "ml engineer", "data scientist", "computer vision", "machine learning",
            "ai engineer", "data engineer", "analyst", "nlp", "deep learning",
        ],
    },

    {
        "name": "AI-Powered Job Application Pipeline",
        "tech": "Python, Playwright, Claude API (LLM), SQLite, NLP, Browser Automation",
        "github": "https://github.com/raghava0071/job-pipeline",
        "bullets": [
            "Engineered fully automated job application system applying to 300+ positions/day "
            "across LinkedIn, Indeed, and Workday using Playwright browser automation and Claude AI",
            "Built NLP resume tailoring engine that parses job descriptions, scores keyword coverage, "
            "and rewrites bullets per job — achieving verified 95%+ ATS coverage per application",
            "Designed SQLite answer-caching layer with 79% hit rate, reducing LLM API calls 3x "
            "while maintaining contextually accurate form responses at scale",
            "Implemented structured run logging, salary intelligence, and email notification system "
            "with per-job screenshots and recruiter-ready PDF/DOCX resume generation",
        ],
        "highlights": "LLM Integration · Playwright · NLP · Python · SQLite · Automation · CI",
        "include_for": [
            "data engineer", "ml engineer", "software engineer", "data scientist",
            "automation engineer", "ai engineer", "python developer", "backend engineer",
        ],
    },

    {
        "name": "Job Market Intelligence Dashboard",
        "tech": "Python, NLP, TF-IDF, K-Means Clustering, Streamlit, Pandas, Jupyter",
        "github": "https://github.com/raghava0071/job-market-intelligence-dashboard",
        "bullets": [
            "Built real-time job market analytics platform using NLP and K-Means clustering "
            "to surface skill demand trends across 10,000+ live job postings",
            "Applied TF-IDF vectorization to group job descriptions by required skills, "
            "identifying emerging technology demand by role and location",
            "Designed interactive Streamlit dashboard for exploring salary distributions, "
            "skill gap analysis, and market saturation across data science roles",
            "Automated live data ingestion pipeline via job posting API for continuous "
            "market intelligence monitoring",
        ],
        "highlights": "NLP · TF-IDF · K-Means · Streamlit · Data Pipeline · Market Analytics",
        "include_for": [
            "data scientist", "data analyst", "analytics engineer", "data engineer",
            "bi analyst", "ml engineer", "nlp", "product analyst",
        ],
    },

    # ── Tier 2: Strong — shown for analytics/SQL/BI roles ─────────────────────

    {
        "name": "Ad Click-Through Rate Prediction (AUC 0.98)",
        "tech": "Python, Scikit-learn, Logistic Regression, Pandas, EDA, Google Colab",
        "github": "https://github.com/raghava0071/ad-targeting-ctr-prediction",
        "bullets": [
            "Trained Logistic Regression model on 1M+ ad impression records achieving AUC of 0.98 "
            "for predicting ad click-through rates from behavioral and demographic signals",
            "Conducted full EDA identifying key CTR predictors: time-of-day, device type, and "
            "user segment; applied SMOTE for class imbalance handling",
            "Engineered 15+ features from raw behavioral signals; published as reproducible "
            "NSDC Data Science project with full Jupyter notebook documentation",
        ],
        "highlights": "Logistic Regression · AUC 0.98 · Feature Engineering · EDA · Scikit-learn",
        "include_for": [
            "data scientist", "data analyst", "ml engineer", "analytics engineer",
            "marketing analyst", "data engineer", "quantitative analyst",
        ],
    },

    {
        "name": "Flight Fare Intelligence Engine",
        "tech": "PostgreSQL, PLpgSQL, Python, Amadeus Flight API, SQL Analytics",
        "github": "https://github.com/raghava0071/flight-fare-intel",
        "bullets": [
            "Built SQL-first flight price analysis engine using PostgreSQL stored procedures "
            "and PLpgSQL to track fare fluctuation patterns across routes and booking windows",
            "Designed normalized time-series schema for price data; wrote analytical queries "
            "to surface optimal booking windows, fare anomalies, and route-level pricing trends",
            "Integrated Amadeus Flight Offers API for real-time price ingestion and snapshot "
            "tracking into the PostgreSQL data warehouse",
        ],
        "highlights": "PostgreSQL · PLpgSQL · SQL Analytics · REST API Integration · Data Warehouse",
        "include_for": [
            "data engineer", "data analyst", "sql developer", "analytics engineer",
            "database engineer", "bi analyst", "etl developer",
        ],
    },

    {
        "name": "Amadeus Flight Price Tracker",
        "tech": "Python, Amadeus API, Pandas, Jupyter, GitHub Actions, CI/CD",
        "github": "https://github.com/raghava0071/amadeus-flight-price-fluctuations",
        "bullets": [
            "Built reproducible flight price tracking pipeline scanning MIA↔BOM route date pairs "
            "via Amadeus API, surfacing cheapest booking combinations and fare anomalies",
            "Automated chart generation and dataset validation using Python scripts with "
            "CI/CD workflow via GitHub Actions for continuous data freshness",
            "Designed CSV-backed watchlist schema for date-pair price tracking; produced "
            "route-level charts and deep links to Google Flights and Skyscanner",
        ],
        "highlights": "Amadeus API · Python · Pandas · GitHub Actions · CI/CD · Price Analytics",
        "include_for": [
            "data analyst", "data engineer", "analytics engineer", "python developer",
            "api developer", "etl developer",
        ],
    },

    # ── Tier 3: Supporting — shown for BI/nonprofit/R/pipeline roles ──────────

    {
        "name": "Florida Lottery Data Pipeline (Automated)",
        "tech": "R, GitHub Actions, CI/CD, PDF Parsing, Data Cleaning, renv",
        "github": "https://github.com/raghava0071/florida-picks-project",
        "bullets": [
            "Built automated daily data pipeline in R that fetches, parses, and cleans "
            "Florida Lottery Pick 2 results from PDF into structured CSV — running via GitHub Actions CI/CD",
            "Implemented defensive PDF parsing with regex fallback; pipeline runs lint, "
            "snapshot, and integration tests on every push with zero manual intervention",
            "Designed reproducible project structure using renv for dependency locking and "
            "R Markdown for automated daily reporting outputs",
        ],
        "highlights": "R · GitHub Actions · CI/CD · PDF Parsing · Automated Pipeline · Data Cleaning",
        "include_for": [
            "data engineer", "data analyst", "etl developer", "analytics engineer",
            "r developer", "pipeline engineer", "reporting analyst",
        ],
    },

    {
        "name": "Nonprofit Outreach Analytics Dashboard (FLIC)",
        "tech": "Python, Streamlit, Pandas, Data Quality Monitoring, CSV Pipeline",
        "github": "https://github.com/raghava0071/FLIC-outreach-analytics-prototype",
        "bullets": [
            "Designed end-to-end outreach analytics platform for a nonprofit organization: "
            "data cleaning pipeline → structured metrics → interactive Streamlit dashboard",
            "Built participation and engagement tracking across county, program type, and language; "
            "added volunteer activity analysis and data quality monitoring snapshot",
            "Delivered stakeholder-ready reporting views with interactive filters for county "
            "and program type — replacing manual spreadsheet reporting",
        ],
        "highlights": "Streamlit · Python · Pandas · Data Quality · Nonprofit Analytics · Dashboard",
        "include_for": [
            "data analyst", "bi analyst", "analytics engineer", "reporting analyst",
            "data scientist", "business analyst", "nonprofit", "dashboard",
        ],
    },
]

REFERENCES = [
    {
        "name":         "Edlyn",
        "title":        "President",
        "company":      "Florida Youth at Risk (FYAR)",
        "relationship": "Supervisor / Organizational Lead",
        "email":        "edlyn@fyar.org",
    },
    {
        "name":         "Christina Grant",
        "title":        "Program & Performance Oversight",
        "company":      "Florida Youth at Risk (FYAR)",
        "relationship": "Project Supervisor",
        "email":        "cmrflorida@gmail.com",
    },
    {
        "name":         "Orfelina Rivera",
        "title":        "HR",
        "company":      "School District of Palm Beach County",
        "relationship": "Professional Reference",
        "email":        "Orfelina.Rivera@palmbeachschools.org",
    },
]

COMMON_QA = {
    # Work authorization
    "authorized":       "Yes",
    "work_authorized":  "Yes",
    "sponsorship":      "No",
    "visa_status":      "F-1 STEM OPT",
    "citizenship":      "No — F-1 OPT / STEM OPT",
    # Job preferences
    "relocate":         "No",
    "willing_relocate": "No",
    "remote_ok":        "Yes",
    "work_mode":        "Open to Remote, Hybrid, or On-site",
    "start_date":       "2 weeks",
    "notice_period":    "2 weeks",
    "source":           "Indeed / LinkedIn / Online Job Board",
    # Salary
    "salary_expected":  "70000",
    "hourly_rate":      "40",
    # Contact & address
    "street_address":   "123 Main St",
    "city":             "City",
    "state":            "FL",
    "zip":              "33484",
    "country":          "United States of America",
    "phone":            "5555555555",
    "phone_formatted":  "(555) 555-5555",
    "phone_type":       "Mobile",
    "phone_country":    "United States of America (+1)",
    # Education
    "education_level":  "Master's Degree",
    "field_of_study":   "Data Science and Analytics",
    "university":       "Florida Atlantic University (FAU)",
    "grad_year":        "2025",
    "gpa":              "3.8",
    # Current job
    "current_employer": "Knowvia Tech Inc",
    "current_title":    "Data Engineer",
    "current_start":    "04/2026",
    "years_experience": "3",   # Fixed: was "2" but data/Python/SQL experience is 3-4 yrs
    # Years of experience by area
    "yoe_engineering":  "3",
    "yoe_analytics":    "3",
    "yoe_python":       "4",
    "yoe_sql":          "4",
    "yoe_data":         "3",
    "yoe_ml":           "2",
    "yoe_cloud":        "2",
    "yoe_spark":        "2",
    "yoe_etl":          "3",
}

# Per-skill experience years — Claude uses this to answer
# "How many years of X experience?" questions accurately
SKILL_YEARS = {
    "python":           "4",
    "sql":              "4",
    "r":                "2",
    "java":             "2",
    "javascript":       "2",
    "data engineering": "3",
    "data analysis":    "3",
    "analytics":        "3",
    "machine learning": "2",
    "deep learning":    "2",
    "nlp":              "2",
    "etl":              "3",
    "elt":              "3",
    "data pipelines":   "3",
    "data warehouse":   "3",
    "aws":              "2",
    "azure":            "2",
    "gcp":              "2",
    "cloud":            "2",
    "spark":            "2",
    "pyspark":          "2",
    "kafka":            "2",
    "airflow":          "2",
    "dbt":              "2",
    "snowflake":        "2",
    "databricks":       "2",
    "power bi":         "2",
    "tableau":          "2",
    "looker":           "1",
    "docker":           "2",
    "kubernetes":       "1",
    "tensorflow":       "2",
    "pytorch":          "2",
    "scikit-learn":     "3",
    "pandas":           "4",
    "numpy":            "4",
    "git":              "4",
    "postgresql":       "3",
    "mysql":            "3",
    "mongodb":          "2",
    "rest api":         "3",
    "fastapi":          "2",
    "flask":            "2",
    "django":           "1",
}
