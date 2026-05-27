# =============================================================================
# RAGHAVENDRA KARANAM — MASTER PROFILE
# Single source of truth for all job application automation
# DO NOT include Community Dreams Foundation or Mobile Stage Pros
# =============================================================================

PROFILE = {
    "name": "Raghavendra Karanam",
    "title": "Data Engineer",
    "phone": "(561) 816-0256",
    "email": "raghavendrakaranam30@gmail.com",
    "location": "Delray Beach, FL",
    "linkedin": "linkedin.com/in/raghavendra-karanam",
    "github": "github.com/raghawa0071",
    "work_auth": "F-1 OPT / STEM OPT — No sponsorship required",
    "relocate": True,
    "start_date": "Immediately available",
}

SALARY = {
    "data_engineer_remote":   "$80,000 – $90,000",
    "data_engineer_local":    "$72,000 – $82,000",
    "data_analyst_remote":    "$65,000 – $75,000",
    "data_analyst_local":     "$58,000 – $68,000",
    "data_scientist_remote":  "$80,000 – $92,000",
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
            "Design and maintain enterprise-grade data pipelines and cloud infrastructure "
            "powering analytics, reporting, and machine learning workflows."
        ),
        "bullets": [
            "Architect and deploy end-to-end ETL/ELT pipelines using Apache Spark, Kafka, and "
            "Hadoop for real-time and batch processing at scale",
            "Engineer cloud-native data solutions on AWS, Azure, and GCP, integrating data lakes, "
            "warehouses, and streaming platforms",
            "Administer and tune SQL and NoSQL databases through schema optimization, indexing, "
            "and query refinement for low-latency data retrieval",
            "Build automated data quality frameworks to enforce consistency and accuracy across "
            "all ingestion and transformation pipelines",
            "Collaborate cross-functionally with analytics, product, and engineering teams to "
            "translate business needs into scalable architecture",
            "Implement monitoring and alerting systems to maintain platform availability and "
            "rapid incident resolution",
            "Optimize processing through intelligent partitioning, pipeline parallelization, "
            "and infrastructure improvements",
        ],
        "tools": [
            "Python", "PySpark", "SparkSQL", "Apache Spark", "Apache Kafka", "Hadoop",
            "Azure Data Factory", "ADLS Gen2", "Azure SQL Database", "AWS", "GCP",
            "ETL/ELT", "Data Warehousing", "NoSQL", "SQL", "MySQL", "PostgreSQL",
        ],
        "include_always": True,
    },
    {
        "title": "Web Developer & SEO/Analytics Volunteer",
        "company": "Florida Youth At Risk",
        "duration": "July 2025 – Present",
        "location": "Boca Raton, FL (Hybrid)",
        "type": "Part-time Volunteer",
        "summary": (
            "Redesigned and maintain the organization's website, integrating analytics and "
            "data-driven strategies for digital engagement."
        ),
        "bullets": [
            "Integrated Google Analytics 4 (GA4) with custom event tracking and conversion "
            "goals, enabling data-driven leadership decisions",
            "Manage Google Ads campaigns under the $10K Google Ad Grant, optimizing ad spend, "
            "targeting, and conversion rates",
            "Build and A/B test landing pages for donations, volunteer sign-ups, and youth "
            "program promotions",
            "Analyze GA4 traffic and engagement data to surface actionable insights for "
            "content and campaign strategy",
        ],
        "tools": [
            "Google Analytics 4", "Google Ads", "Google Search Console", "SEO", "WordPress",
        ],
        "include_always": False,
        "include_for_roles": ["Data Analyst", "BI Analyst", "Analytics Engineer"],
    },
    {
        "title": "GA4 Website Analyst",
        "company": "Management Information Systems Association (MISA), FAU",
        "duration": "January 2024 – May 2025",
        "location": "Boca Raton, FL (Hybrid)",
        "type": "Part-time",
        "summary": "",
        "bullets": [
            "Configured and maintained GA4 with custom event tracking, funnels, and "
            "conversion goals",
            "Analyzed website traffic to identify high-impact optimization opportunities, "
            "improving session quality and engagement",
            "Built GA4 dashboards tracking bounce rate, session duration, traffic sources, "
            "and conversion paths",
            "Conducted A/B testing and UX analysis to improve landing page performance and "
            "visitor retention",
            "Generated monthly performance reports translating analytics into actionable "
            "recommendations for leadership",
        ],
        "tools": [
            "Google Analytics 4", "Google Tag Manager", "Google Search Console",
            "Data Studio", "SQL", "Python",
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

COMMON_QA = {
    "authorized":       "Yes",
    "sponsorship":      "No",
    "relocate":         "Yes",
    "start_date":       "Immediately / As soon as possible",
    "work_mode":        "Open to Remote, Hybrid, or On-site",
    "source":           "Indeed / LinkedIn / Online Job Board",
    "citizenship":      "No — F-1 OPT / STEM OPT",
    "education_level":  "Master's Degree",
    "field_of_study":   "Data Science and Analytics",
    "university":       "Florida Atlantic University (FAU)",
    "grad_year":        "2025",
    "yoe_engineering":  "1 year",
    "yoe_analytics":    "2+ years",
}
