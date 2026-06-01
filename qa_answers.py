"""
qa_answers.py — Master Q&A for job application forms.

HOW IT WORKS:
  - Pipeline checks this file FIRST before cache and before Claude API.
  - Keys are lowercased question label text (partial match supported).
  - Add any new question+answer here and it applies to ALL future applications.

HOW TO ADD:
  - Find the question label in the terminal output (e.g. 'Will you be able to work on W2?')
  - Add it below with your answer.
  - Restart the pipeline — it will use your answer from now on.
"""

QA = {
    # ── Work Authorization ────────────────────────────────────────────────────
    "will you be able to work on our w2":
        "Yes, I am able to work on W2. I am currently on F-1 STEM OPT and am fully authorized to work without requiring any sponsorship.",

    "are you able to work on w2":
        "Yes, I am able to work on W2. I am currently on F-1 STEM OPT.",

    "are you comfortable working on w2":
        "Yes",

    "can you work on w2":
        "Yes",

    "work on a w2":
        "Yes",

    "are you legally authorized to work in the united states":
        "Yes",

    "are you authorized to work in the us":
        "Yes",

    "authorized to work":
        "Yes",

    # Sponsorship questions MUST come before "visa status" key
    # because "visa status" appears inside sponsorship question text
    "will you now or in the future require sponsorship for employment visa status":
        "No",

    "do you now or will you in the future require sponsorship":
        "No",

    "will you require sponsorship":
        "No",

    "require visa sponsorship":
        "No",

    "require sponsorship":
        "No",

    "need sponsorship":
        "No",

    "sponsorship for employment":
        "No",

    # ── Visa / Immigration ────────────────────────────────────────────────────
    "what is your visa":
        "F-1 STEM OPT",

    "visa status":
        "F-1 STEM OPT",

    "visa type":
        "F-1 STEM OPT",

    "current visa":
        "F-1 STEM OPT",

    "immigration status":
        "F-1 STEM OPT",

    "work authorization type":
        "F-1 STEM OPT",

    # ── Salary / Rate ─────────────────────────────────────────────────────────
    "what is the best rate you are looking for":
        "85000",

    "desired rate":
        "85000",

    "expected rate":
        "85000",

    "hourly rate":
        "45",

    "salary expectation":
        "85000",

    "desired salary":
        "85000",

    "expected salary":
        "85000",

    "desired pay":
        "85000",

    "expected compensation":
        "85000",

    "what are your salary expectations":
        "85000",

    "minimum salary":
        "75000",

    # ── Availability / Start Date ─────────────────────────────────────────────
    "date available":
        "2 weeks",

    "when can you start":
        "2 weeks",

    "start date":
        "2 weeks",

    "earliest start date":
        "2 weeks",

    "notice period":
        "2 weeks",

    "how soon can you start":
        "2 weeks",

    "available to start":
        "2 weeks",

    # ── Location / Relocation ─────────────────────────────────────────────────
    "willing to relocate":
        "No",

    "open to relocation":
        "No",

    "are you willing to relocate":
        "No",

    # ── Remote / Hybrid ───────────────────────────────────────────────────────
    "open to remote":
        "Yes",

    "comfortable working remotely":
        "Yes",

    "preferred work arrangement":
        "Remote",

    # ── Name & Contact ────────────────────────────────────────────────────────
    "first name *":
        "Raghavendra",

    "first name":
        "Raghavendra",

    "last name *":
        "Karanam",

    "last name":
        "Karanam",

    "full name":
        "Raghavendra Karanam",

    "type phone number":
        "5613017799",

    "phone number":
        "5613017799",

    "phone *":
        "5613017799",

    "phone":
        "5613017799",

    "mobile":
        "5613017799",

    "cell phone":
        "5613017799",

    "email":
        "raghavendrakaranam30@gmail.com",

    # ── Indeed Profile Visibility ─────────────────────────────────────────────
    "employers can find you on indeed":
        "Employers can find you on Indeed",

    "employers can't find you on indeed":
        "Employers can find you on Indeed",

    "scoutability":
        "Employers can find you on Indeed",

    "profile visibility":
        "Employers can find you on Indeed",

    # ── Generic Yes/No radio groups (NBI style hash-labeled groups) ───────────
    # When radio group label is a hash ID like q_c5f64fa31efee6ca83f42779e68d8f29
    # and options are just Yes/No, default to Yes for work-related questions
    "q_":
        "Yes",

    # Individual Yes/No options on radio pages — fill Yes by default
    "yes":
        "Yes",

    # ── Years of Experience ───────────────────────────────────────────────────
    "years of experience":
        "2",

    "how many years of experience":
        "2",

    "years of relevant experience":
        "2",

    # ── Cover Letter / Additional Info ────────────────────────────────────────
    "cover letter":
        "I am a passionate Data Engineer with hands-on experience in Python, SQL, Azure, and ETL pipelines. I am excited about this opportunity and confident I can contribute immediately.",

    "additional information":
        "I am currently on F-1 STEM OPT and fully authorized to work in the US without sponsorship. I am available to start within 2 weeks.",

    "tell us about yourself":
        "I am a Data Engineer with expertise in Python, SQL, Azure Data Factory, Apache Spark, and ETL/ELT pipeline development. I hold a Master's in Data Science and am passionate about building scalable data solutions.",

    "why do you want to work here":
        "I am excited about this opportunity to apply my data engineering skills and contribute to your team's data infrastructure goals.",

    "why are you interested":
        "This role aligns perfectly with my background in data engineering, Python, SQL, and cloud platforms. I am eager to bring my skills to your team.",

    # ── EEO / Diversity (all prefer not to say) ───────────────────────────────
    "gender":
        "Prefer not to say",

    "ethnicity":
        "Prefer not to say",

    "race":
        "Prefer not to say",

    "disability":
        "I don't wish to answer",

    "veteran status":
        "I am not a protected veteran",

    "are you a veteran":
        "No",

    # ── LinkedIn / Portfolio ──────────────────────────────────────────────────
    "linkedin":
        "https://www.linkedin.com/in/raghavendra-karanam",

    "linkedin url":
        "https://www.linkedin.com/in/raghavendra-karanam",

    "linkedin profile":
        "https://www.linkedin.com/in/raghavendra-karanam",

    "portfolio":
        "https://www.linkedin.com/in/raghavendra-karanam",

    "website":
        "https://www.linkedin.com/in/raghavendra-karanam",

    "github":
        "",

    # ── Background Check / Drug Test ──────────────────────────────────────────
    "background check":
        "Yes",

    "consent to background check":
        "Yes",

    "drug test":
        "Yes",

    "willing to undergo drug test":
        "Yes",

    # ── Miscellaneous ─────────────────────────────────────────────────────────
    "how did you hear about us":
        "Indeed",

    "how did you find this position":
        "Indeed",

    "referral":
        "Indeed",

    "are you 18 or older":
        "Yes",

    "are you over 18":
        "Yes",

    "us citizen":
        "No",

    "are you a us citizen":
        "No",

    "green card":
        "No",

    "permanent resident":
        "No",

    # ── Location / Address ────────────────────────────────────────────────────
    # These exact label patterns match what Indeed form fields show
    "address* *":
        "7330 W Atlantic Ave Apt 215",

    "address *":
        "7330 W Atlantic Ave Apt 215",

    "address":
        "7330 W Atlantic Ave Apt 215",

    "street address":
        "7330 W Atlantic Ave Apt 215",

    "city* *":
        "Delray Beach",

    "city *":
        "Delray Beach",

    "city*":
        "Delray Beach",

    "state/province *":
        "Florida",

    "state/province":
        "Florida",

    "state* *":
        "Florida",

    "state *":
        "Florida",

    "state*":
        "Florida",

    "postal code* *":
        "33444",

    "postal code *":
        "33444",

    "postal code*":
        "33444",

    "postal/zip *":
        "33444",

    "postal/zip":
        "33444",

    "zip code":
        "33444",

    "zip *":
        "33444",

    "desired salary* *":
        "85000",

    "desired salary *":
        "85000",

    # ── LinkedIn-specific field labels (shorter, no asterisk) ────────────────
    "city":
        "Delray Beach",

    "zip":
        "33444",

    "what is your gpa":
        "3.8",

    "gpa":
        "3.8",

    "grade point average":
        "3.8",

    "university grade point":
        "3.8",

    "what city and state do you currently reside":
        "Delray Beach, FL",

    "current city and state":
        "Delray Beach, FL",

    "city and state":
        "Delray Beach, FL",

    "current location":
        "Delray Beach, FL",

    # ── Reason for leaving ────────────────────────────────────────────────────
    "reasons for leaving previous employers":
        "I am seeking new opportunities to grow my data engineering skills and contribute to more impactful projects. Each transition has been driven by a desire for greater responsibilities and technical growth.",

    "reason for leaving":
        "Seeking career growth and new opportunities in data engineering.",

    "why did you leave":
        "Seeking career growth and new opportunities.",

    "why are you leaving":
        "I am looking for new opportunities to apply and grow my data engineering expertise.",

    "if no previous employment":
        "I am seeking new opportunities to grow my data engineering skills and contribute to impactful projects.",

    # ── Hash-ID radio groups ──────────────────────────────────────────────────
    "q_":
        "Yes",

    # Individual radio option labels
    "yes":
        "Yes",

    "no":
        "No",
}


def get_answer(label: str) -> str | None:
    """
    Look up an answer for a form field label.
    Returns the answer string if found, None if not found.
    Matching is case-insensitive and partial (question contains key).
    """
    label_l = label.lower().strip()
    # Exact match first
    if label_l in QA:
        return QA[label_l]
    # Partial match — key appears anywhere in label
    for key, answer in QA.items():
        if key in label_l:
            return answer
    return None
