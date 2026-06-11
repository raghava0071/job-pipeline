"""
qa_answers.py — Master Q&A for job application forms.

HOW IT WORKS:
  - Pipeline checks this file FIRST before cache and before Claude API.
  - Keys are lowercased question label text (partial match supported).
  - Add any new question+answer here and it applies to ALL future applications.

PERSONAL DATA:
  - Phone, address, email are loaded from environment variables (set in .env)
  - Never hardcode personal data here — this file is public on GitHub

HOW TO ADD:
  - Find the question label in the terminal output (e.g. 'Will you be able to work on W2?')
  - Add it below with your answer.
  - Restart the pipeline — it will use your answer from now on.
"""

import os as _os
from datetime import date as _date, timedelta as _timedelta

def _today(fmt="%Y-%m-%d"):
    """Always returns today's date — never stale."""
    return _date.today().strftime(fmt)

def _in_two_weeks(fmt="%m/%d/%Y"):
    """Returns today + 14 days in the given format — for date picker availability fields."""
    return (_date.today() + _timedelta(days=14)).strftime(fmt)

def _today_month():
    return _date.today().strftime("%B")   # e.g. "June"

def _today_year():
    return str(_date.today().year)        # e.g. "2026"

def _today_month_num():
    return _date.today().strftime("%m")   # e.g. "06"

def _env(key, default=""):
    """Load from environment — reads .env file if needed."""
    val = _os.environ.get(key, "")
    if not val:
        try:
            env_path = __import__('pathlib').Path.home() / "job_pipeline" / ".env"
            for line in env_path.read_text().splitlines():
                if line.startswith(key + "="):
                    val = line.split("=", 1)[1].strip()
                    _os.environ[key] = val
                    break
        except Exception:
            pass
    return val or default

# ── Load master profile (single source of truth) ─────────────────────────────
try:
    import sys as _sys
    _sys.path.insert(0, str(__import__('pathlib').Path.home() / "job_pipeline"))
    from raghav_profile import PROFILE, SALARY, COMMON_QA, SKILL_YEARS
    _NAME      = PROFILE.get("name", "Raghavendra Karanam")
    _FIRST     = _NAME.split()[0]
    _LAST      = _NAME.split()[-1]
    _EMAIL     = PROFILE.get("email", "raghavendrakaranam30@gmail.com")
    _PHONE     = PROFILE.get("phone", "5618160256").replace("(","").replace(")","").replace(" ","").replace("-","")
    _LOCATION  = PROFILE.get("location", "Delray Beach, FL")
    _CITY      = _LOCATION.split(",")[0].strip()
    _STATE     = _LOCATION.split(",")[1].strip() if "," in _LOCATION else "FL"
    _LINKEDIN  = PROFILE.get("linkedin", "linkedin.com/in/raghavendra-karanam")
    _GITHUB    = PROFILE.get("github", "github.com/raghava0071")
    _EDU_LEVEL = COMMON_QA.get("education_level", "Master's Degree")
    _FIELD     = COMMON_QA.get("field_of_study", "Data Science and Analytics")
    _SCHOOL    = COMMON_QA.get("university", "Florida Atlantic University")
    _GRAD_YEAR = COMMON_QA.get("grad_year", "2025")
except Exception:
    _EMAIL     = "raghavendrakaranam30@gmail.com"
    _PHONE     = "5618160256"
    _FIRST     = "Raghavendra"
    _LAST      = "Karanam"
    _CITY      = "Delray Beach"
    _STATE     = "FL"
    _LINKEDIN  = "linkedin.com/in/raghavendra-karanam"
    _GITHUB    = "github.com/raghava0071"
    _EDU_LEVEL = "Master's Degree"
    _FIELD     = "Data Science and Analytics"
    _SCHOOL    = "Florida Atlantic University"
    _GRAD_YEAR = "2025"

QA = {
    # ── Workday-specific fields ───────────────────────────────────────────────
    # Country phone code MUST come before generic "phone" keys — order matters
    "country / territory phone code": "United States of America (+1)",
    "country phone code":             "United States of America (+1)",
    "country/territory phone code":   "United States of America (+1)",
    "country phone":                  "United States of America (+1)",
    "phone country code":             "United States of America (+1)",
    "phone country":                  "United States of America (+1)",
    "country code":                   "United States of America (+1)",

    "phone device type":
        "Mobile",

    "phone extension":
        "",      # leave blank — extension is not a phone number

    "phone type":
        "Mobile",

    "how did you hear about us":
        "Indeed",

    "how did you hear about this position":
        "Indeed",

    "how did you learn about this opportunity":
        "Indeed",

    "how did you learn about this job":
        "Indeed",

    "how did you find this job":
        "Indeed",

    "how did you find out about":
        "Indeed",

    "source":
        "Indeed",

    "referred by":
        "",

    # ── Experience level questions ────────────────────────────────────────────
    "do you have more than 10 years of experience":
        "No",

    "more than 10 years of experience":
        "No",

    "do you have 10+ years":
        "No",

    "10 or more years of experience":
        "No",

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
    # Range: $60,000 – $90,000+ depending on role/location.
    # salary_helper.py picks the exact number dynamically from the JD.
    # These are fallback defaults only used when salary_helper is not called.
    "what is the best rate you are looking for":
        "75000",

    "desired rate":
        "75000",

    "expected rate":
        "75000",

    "hourly rate":
        "38",

    "desired hourly rate":
        "38",

    "expected hourly rate":
        "38",

    "hourly pay":
        "38",

    "rate per hour":
        "38",

    "salary expectation":
        "75000",

    "desired salary":
        "75000",

    "expected salary":
        "75000",

    "desired pay":
        "75000",

    "expected compensation":
        "75000",

    "what are your salary expectations":
        "75000",

    "minimum salary":
        "60000",

    "what wagesalary are you looking for please specify hour":
        "$75,000 yearly or $38/hour",

    "salary range":
        "60000-90000",

    "compensation expectations":
        "75000",

    # ── Availability (job start / notice period) ──────────────────────────────
    # Text fields → "2 weeks"
    "notice period":            "2 weeks",
    "how soon can you start":   "2 weeks",
    "when can you start":       "2 weeks",

    # Date picker fields → MM/DD/YYYY format (today + 14 days)
    # These labels trigger a calendar/date widget — must be a real date, not "2 weeks"
    "what is your desired start date": _in_two_weeks("%m/%d/%Y"),
    "desired start date":       _in_two_weeks("%m/%d/%Y"),
    "date available":           _in_two_weeks("%m/%d/%Y"),
    "when would you be available to begin work": _in_two_weeks("%m/%d/%Y"),
    "when are you available to start": _in_two_weeks("%m/%d/%Y"),
    "available start date":     _in_two_weeks("%m/%d/%Y"),
    "available to start":       _in_two_weeks("%m/%d/%Y"),
    "earliest start date":      _in_two_weeks("%m/%d/%Y"),
    "earliest available date":  _in_two_weeks("%m/%d/%Y"),
    "availability date":        _in_two_weeks("%m/%d/%Y"),

    # ── Essential functions / physical requirements ───────────────────────────
    "are you able to perform the essential functions": "Yes",
    "essential functions of the job":                  "Yes",
    "perform the essential functions":                 "Yes",
    "with or without reasonable accommodation":        "Yes",
    "reasonable accommodation":                        "Yes",
    "able to perform":                                 "Yes",
    "physically able":                                 "Yes",
    "meet the physical requirements":                  "Yes",

    # ── Experience with platforms / tools (default Yes) ───────────────────────
    # When a form asks "Do you have experience with X?" → Yes
    "do you have experience with":      "Yes",
    "do you have experience in":        "Yes",
    "have you worked with":             "Yes",
    "are you familiar with":            "Yes",
    "familiarity with":                 "Yes",
    "proficiency with":                 "Yes",
    "have you used":                    "Yes",
    "experience using":                 "Yes",
    "experience working with":          "Yes",

    # ── Work history dates (MM/YYYY format) ───────────────────────────────────
    # "Start Date" / "End Date" on Indeed work history forms = current job dates
    "start date":               "04/2026",   # Knowvia Tech start: April 2026
    "end date":                 "Present",   # current job, still working

    # ── Education dates (MM/YYYY or MM/DD/YYYY) ───────────────────────────────
    "start date *":             "08/2023",   # FAU M.S. started Aug 2023
    "end date *":               "05/2025",   # FAU M.S. graduated May 2025
    "graduation date":          "05/2025",
    "graduation year":          "2025",
    "year graduated":           "2025",

    # ── Today's date fields (EEOC signature, consent forms) ───────────────────
    # Always returns today's real date — never stale
    "today's date *":           _today("%m/%d/%Y"),
    "today's date":             _today("%m/%d/%Y"),
    "today's date":             _today("%m/%d/%Y"),
    "today date":               _today("%m/%d/%Y"),
    "current date":             _today("%m/%d/%Y"),
    "date *":                   _today("%m/%d/%Y"),
    "signature date":           _today("%m/%d/%Y"),
    "date signed":              _today("%m/%d/%Y"),

    # ── Month / Year dropdowns (EEOC date pickers) ────────────────────────────
    "month":                    _today_month(),     # e.g. "June"
    "year":                     _today_year(),      # e.g. "2026"

    # ── Location / Relocation ─────────────────────────────────────────────────
    "willing to relocate":
        "Yes",

    "open to relocation":
        "Yes",

    "are you willing to relocate":
        "Yes",

    "able to relocate":
        "Yes",

    "willing and able to relocate":
        "Yes",

    # ── Commuting ────────────────────────────────────────────────────────────
    "are you comfortable commuting to this job":
        "Yes",

    "comfortable commuting":
        "Yes",

    "commuting to this job's location":
        "Yes",

    "commute to this job":
        "Yes",

    "able to commute":
        "Yes",

    # ── Remote engagement ────────────────────────────────────────────────────
    "are you ok for the remote engagement":
        "Yes",

    "ok for the remote engagement":
        "Yes",

    "remote engagement":
        "Yes",

    # ── Remote / Hybrid ───────────────────────────────────────────────────────
    "open to remote":
        "Yes",

    "comfortable working remotely":
        "Yes",

    "preferred work arrangement":
        "Remote",

    # ── Name & Contact — all pulled from raghav_profile.py ───────────────────
    "first name *":         _FIRST,
    "first name":           _FIRST,
    "last name *":          _LAST,
    "last name":            _LAST,
    "full name":            f"{_FIRST} {_LAST}",
    "name":                 f"{_FIRST} {_LAST}",

    "type phone number":    _PHONE,
    "phone number":         _PHONE,
    "phone *":              _PHONE,
    "phone":                _PHONE,
    "mobile":               _PHONE,
    "mobile number":        _PHONE,
    "cell phone":           _PHONE,
    "contact number":       _PHONE,

    "email address *":      _EMAIL,
    "email address":        _EMAIL,
    "email *":              _EMAIL,
    "email":                _EMAIL,
    "e-mail":               _EMAIL,
    "e-mail address":       _EMAIL,
    "your email":           _EMAIL,
    "your email address":   _EMAIL,
    "contact email":        _EMAIL,
    "best email":           _EMAIL,

    "linkedin url":         f"https://{_LINKEDIN}",
    "linkedin profile":     f"https://{_LINKEDIN}",
    "linkedin":             f"https://{_LINKEDIN}",
    "github":               f"https://{_GITHUB}",
    "portfolio":            f"https://{_LINKEDIN}",
    "website blog or portfolio": f"https://{_LINKEDIN}",

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
    # UUID and q_ prefix labels default to Yes for work authorization questions
    "q_":
        "Yes",

    # NOTE: Do NOT add generic "yes" or "no" keys here — they partial-match
    # EEOC/disability/gender labels and fill them with wrong answers.

    # ── EEOC — Disability ─────────────────────────────────────────────────────
    # Raghava does NOT have a disability
    "i do not wish to self-identify":
        "I do not wish to self-identify",

    "i don't wish to self-identify":
        "I do not wish to self-identify",

    "do not wish to self-identify":
        "I do not wish to self-identify",

    "no, i do not have a disability and have not had one in the past":
        "No, I do not have a disability and have not had one in the past",

    "yes, i have a disability, or have had one in the past":
        "",  # never select this

    "eeocDisabledQuestion":
        "No, I do not have a disability and have not had one in the past",

    "eeocdisabledquestion":
        "No, I do not have a disability and have not had one in the past",

    "disability status":
        "No, I do not have a disability and have not had one in the past",

    "do you have a disability":
        "No",

    "disability":
        "No, I do not have a disability and have not had one in the past",

    # ── EEOC — Gender / Sex ───────────────────────────────────────────────────
    # Answer Male regardless of whether the question has a * (required marker)
    # The pipeline sends the answer string and the form filler selects the matching option
    "gender":
        "Male",

    "sex":
        "Male",

    "gender identity":
        "Male",

    "what is your gender":
        "Male",

    "male":
        "Male",

    "man":
        "Male",

    "female":
        "",  # never select

    "non-binary":
        "",  # never select

    "prefer not to say":
        "Prefer not to say",

    "i prefer not to say":
        "Prefer not to say",

    # ── EEOC — Race / Ethnicity ───────────────────────────────────────────────
    # Raghava is Asian
    "race":
        "Asian (Not Hispanic or Latino)",

    "ethnicity":
        "Asian (Not Hispanic or Latino)",

    "race/ethnicity":
        "Asian (Not Hispanic or Latino)",

    "racial category":
        "Asian (Not Hispanic or Latino)",

    "what is your race":
        "Asian (Not Hispanic or Latino)",

    "asian":
        "Asian (Not Hispanic or Latino)",

    "ethnicities":
        "No, I am not Hispanic or Latino",

    "hispanic or latino":
        "No",

    "are you hispanic or latino":
        "No",

    "hispanic":
        "No",

    # ── EEOC — Veteran Status ─────────────────────────────────────────────────
    # Raghava is NOT a veteran
    "veteran":
        "I am not a protected veteran",

    "veteran status":
        "I am not a protected veteran",

    "are you a veteran":
        "No",

    "protected veteran":
        "I am not a protected veteran",

    "i am not a protected veteran":
        "I am not a protected veteran",

    "i identify as one or more of the classifications of protected veteran listed above":
        "",  # never select this

    "veteranstatuses":
        "I am not a protected veteran",

    "veteranquestion":
        "No",

    # ── Onsite / Work location ────────────────────────────────────────────────
    # Raghava CAN work onsite — answer Yes to onsite questions
    "are you able to work onsite":
        "Yes",

    "able to work on site":
        "Yes",

    "can you work onsite":
        "Yes",

    "willing to work onsite":
        "Yes",

    "comfortable working onsite":
        "Yes",

    "work on-site":
        "Yes",

    "onsite work":
        "Yes",

    "hybrid work":
        "Yes",

    "remote work primarily":
        "Yes",

    "remote work only - unwilling to relocate":
        "",  # never check — willing to relocate/work onsite

    "in-office":
        "Yes",

    "able to work in the office":
        "Yes",

    "work from the office":
        "Yes",

    # ── Demographics consent / signature ─────────────────────────────────────
    "i consent":
        "I consent",

    "signature":
        "I consent",

    "i do not consent":
        "",  # never select

    "save my answers for pre-filling":
        "Save my answers for pre-filling",

    # ── Years of Experience ───────────────────────────────────────────────────
    # Includes internship experience in India + US experience
    "years of experience":              "3",
    "how many years of experience":     "3",
    "years of relevant experience":     "3",
    "total years of experience":        "3",
    "years of professional experience": "3",
    "total experience":                 "3",
    "overall experience":               "3",
    "total it experience":              "3",
    "total work experience":            "3",
    "total working experience":         "3",
    "total working experience in usa":  "1",  # US-only experience
    "experience in usa":                "1",
    "us experience":                    "1",
    "experience in us":                 "1",
    "years of experience in the us":    "1",
    "work experience in the united states": "1",

    # Skill-specific years (loaded from SKILL_YEARS in raghav_profile.py)
    "years of experience with python":      "3",
    "python experience":                    "3",
    "years of experience with sql":         "4",
    "sql experience":                       "4",
    "years of experience with spark":       "2",
    "years of experience with pyspark":     "2",
    "years of experience with azure":       "2",
    "years of experience with aws":         "1",
    "years of experience with power bi":    "2",
    "years of experience with tableau":     "1",
    "years of experience with databricks":  "1",
    "years of experience with snowflake":   "1",
    "years of experience with kafka":       "2",
    "years of experience with airflow":     "1",
    "years of experience with dbt":         "1",
    "years of experience with pandas":      "3",
    "years of experience with machine learning": "2",
    "years of experience with excel":       "4",
    "years of experience with git":         "3",

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

    # ── EEO / Diversity ───────────────────────────────────────────────────────
    # Authoritative values are set in the EEO block above. Only non-duplicate
    # keys remain here to avoid Python's last-wins override behavior.
    "veteran status":
        "I am not a protected veteran",

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

    # ── Security Questions (Workday asks these on new device/login) ──────────
    "work email":                   "raghavendrakaranam30@gmail.com",
    "what was your work email":     "raghavendrakaranam30@gmail.com",
    "previous work email":          "raghavendrakaranam30@gmail.com",
    "your work email":              "raghavendrakaranam30@gmail.com",
    "email address used":           "raghavendrakaranam30@gmail.com",
    "what city were you born":      "Hyderabad",
    "city were you born":           "Hyderabad",
    "born in":                      "Hyderabad",
    "mother's maiden name":         "Karanam",
    "name of your first pet":       "Tommy",
    "first pet":                    "Tommy",
    "elementary school":            "St. Mary's",
    "name of the street":           "Military Trl",
    "street you grew up on":        "Military Trl",

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
    "address line 1":       "14401 S Military Trl",
    "address line 1 *":     "14401 S Military Trl",
    "address line1":        "14401 S Military Trl",
    "street address":       "14401 S Military Trl",
    "street address *":     "14401 S Military Trl",
    "address* *":           "14401 S Military Trl",
    "address *":            "14401 S Military Trl",
    "address":              "14401 S Military Trl",
    "address line 2":       "",
    "address line 2 *":     "",
    "apt suite":            "",

    "country":              "United States of America",
    "country *":            "United States of America",
    "country/region":       "United States of America",
    "country / region":     "United States of America",
    "country/territory":    "United States of America",

    "city* *":          _CITY,
    "city *":           _CITY,
    "city*":            _CITY,
    "city":             _CITY,

    "state/province *": _STATE,
    "state/province":   _STATE,
    "state* *":         _STATE,
    "state *":          _STATE,
    "state*":           _STATE,
    "state":            _STATE,

    "postal code* *":
        _env("HOME_ZIP", "33484"),

    "postal code *":
        _env("HOME_ZIP", "33484"),

    "postal code*":
        _env("HOME_ZIP", "33484"),

    "postal/zip *":
        _env("HOME_ZIP", "33484"),

    "postal/zip":
        _env("HOME_ZIP", "33484"),

    "zip code":
        _env("HOME_ZIP", "33484"),

    "zip *":
        _env("HOME_ZIP", "33484"),

    "zip":
        _env("HOME_ZIP", "33484"),

    "desired salary* *":
        "70000",

    "desired salary *":
        "70000",

    # ── LinkedIn-specific field labels (shorter, no asterisk) ────────────────
    "city, state":
        _env("HOME_CITY_STATE", "Delray Beach, FL"),

    "city,state":
        _env("HOME_CITY_STATE", "Delray Beach, FL"),

    "city":
        _env("HOME_CITY", ""),

    "zip":
        _env("HOME_ZIP", ""),

    "what is your gpa":
        "3.8",

    "gpa":
        "3.8",

    "grade point average":
        "3.8",

    "university grade point":
        "3.8",

    "what city and state do you currently reside":  f"{_CITY}, {_STATE}",
    "current city and state":                        f"{_CITY}, {_STATE}",
    "city and state":                                f"{_CITY}, {_STATE}",
    "current location":                              f"{_CITY}, {_STATE}",
    "city, state":                                   f"{_CITY}, {_STATE}",
    "city,state":                                    f"{_CITY}, {_STATE}",

    # ── Education — from raghav_profile.py ───────────────────────────────────
    "most recent school education institution":      _SCHOOL,
    "school":                                        _SCHOOL,
    "university":                                    _SCHOOL,
    "college":                                       _SCHOOL,
    "highest level of education":                    _EDU_LEVEL,
    "education level":                               _EDU_LEVEL,
    "degree":                                        _EDU_LEVEL,
    "degree type":                                   _EDU_LEVEL,
    "field of study":                                _FIELD,
    "major":                                         _FIELD,
    "graduation year":                               _GRAD_YEAR,
    "year of graduation":                            _GRAD_YEAR,
    "when did you graduate":                         _GRAD_YEAR,

    # ── Work Authorization — specific option labels ───────────────────────────
    # F-1 STEM OPT = temporary work authorization, not permanent
    "i have an active temporary work permit":
        "I have an active temporary work permit.",

    # Don't select "permanently authorized" — Raghava is on OPT (temporary)
    # This key (31 chars) beats "authorized to work" (18 chars) in partial matching
    "authorized to work permanently":
        "",

    # Don't select "I am not eligible" — Raghava IS eligible to work
    "i am not eligible":
        "",

    # This question appears when "No" is incorrectly selected for work auth.
    # Give it a proper answer in case it appears.
    "no, but i am eligible to apply for a work visa within six months":
        "No, but I am eligible to apply for a work visa within six months",

    # ── Notice Period — specific key beats "current employer" in partial match ──
    # "notice period you need to give" (31 chars) > "current employer" (16 chars)
    "notice period you need to give":
        "2 weeks",

    # ── Relatives at company — specific key beats "full name" (9 chars) ──────
    # Raghava has no relatives at any of these companies
    "relatives full name and department":
        "N/A",

    "list your relatives full name":
        "N/A",

    "please list your relatives":
        "N/A",

    "do you have any relatives":
        "No",

    "any relatives employed":
        "No",

    "relatives working at":
        "No",

    # ── Reference fields — specific keys beat "phone"/"email" partial matches ─
    # Full reference info: Name, Relationship, Email, Phone, Years known
    "list the information about your first professional reference":
        "1. Edlyn | President, Florida Youth at Risk (FYAR) | Supervisor | edlyn@fyar.org | (561) 245-7890 | 2 years",

    "list the information about your second professional reference":
        "2. Christina Grant | Program & Performance Oversight, FYAR | Project Supervisor | cmrflorida@gmail.com | (561) 354-6789 | 2 years",

    "list the information about your third professional reference":
        "3. Orfelina Rivera | HR, School District of Palm Beach County | Manager | Orfelina.Rivera@palmbeachschools.org | (561) 434-5678 | 1 year",

    # ── Why you left previous jobs ────────────────────────────────────────────
    "we are interested in why you left each of your last few jobs":
        "I left each position to pursue growth opportunities. At the university, I transitioned after completing my graduate research to enter industry. I am actively seeking a role where I can apply my data engineering expertise at greater scale and impact.",

    "why you left each of your last few jobs":
        "I left each position to pursue growth opportunities and advance my data engineering career.",

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
    # NOTE: "no" is intentionally empty — setting "No" here causes the filler to
    # click both the Yes and No radio buttons in sequence, and "No" wins last.
    # Leave "no" empty so only the "Yes" option is selected for unnamed Yes/No groups.
    "yes":
        "Yes",

    "no":
        "",

    # ── GPA ──────────────────────────────────────────────────────────────────
    "what is your gpa":
        "3.8",

    "gpa":
        "3.8",

    "grade point average":
        "3.8",

    "university grade point average":
        "3.8",

    "what is your university grade point average 40 gpa scale":
        "3.8",

    # ── Current / Recent Employment ───────────────────────────────────────────
    "recent employer":
        "Knowvia Tech Inc",

    "most recent employer":
        "Knowvia Tech Inc",

    "current employer":
        "Knowvia Tech Inc",

    "name of your current or most recent company":
        "Knowvia Tech Inc",

    "current company":
        "Knowvia Tech Inc",

    "employer":
        "Knowvia Tech Inc",

    "recent job title":
        "Data Engineer",

    "most recent job title":
        "Data Engineer",

    "current job title":
        "Data Engineer",

    "current position":
        "Data Engineer",

    "current role":
        "Data Engineer",

    # ── Other Income ──────────────────────────────────────────────────────────
    "will you have other sources of income":
        "Yes",

    "while working at":
        "Yes",

    "other sources of income":
        "Yes",

    "additional income":
        "Yes",

    # ── Hourly Rate ───────────────────────────────────────────────────────────
    # Authoritative hourly rate values are set in the salary block above (38/hr).
    # Only unique keys remain here.
    "what is your desired compensation in usd please specify hour":
        "$38/hour — open to discussion based on role and location",

    "what is your desired compensation":
        "70000",

    # ── References ────────────────────────────────────────────────────────────
    "please list two references and their contact information":
        "1. Edlyn — President, Florida Youth at Risk (FYAR) | Supervisor | edlyn@fyar.org\n"
        "2. Christina Grant — Program & Performance Oversight, Florida Youth at Risk (FYAR) | Project Supervisor | cmrflorida@gmail.com",

    "references":
        "1. Edlyn, President, Florida Youth at Risk (FYAR), edlyn@fyar.org\n"
        "2. Christina Grant, Program & Performance Oversight, FYAR, cmrflorida@gmail.com\n"
        "3. Orfelina Rivera, HR, School District of Palm Beach County, Orfelina.Rivera@palmbeachschools.org",

    "list of references":
        "Edlyn, President FYAR, edlyn@fyar.org | Christina Grant, FYAR, cmrflorida@gmail.com | Orfelina Rivera, Palm Beach Schools, Orfelina.Rivera@palmbeachschools.org",

    "reference 1":
        "Edlyn — President, Florida Youth at Risk | edlyn@fyar.org",

    "reference 2":
        "Christina Grant — Program & Performance Oversight, FYAR | cmrflorida@gmail.com",

    "reference name":
        "Edlyn",

    "reference email":
        "edlyn@fyar.org",

    "reference phone":
        "",

    "reference title":
        "President, Florida Youth at Risk",

    "reference company":
        "Florida Youth at Risk (FYAR)",

    "reference relationship":
        "Supervisor",
}


import re as _re
_UUID_RE = _re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', _re.I)

def get_answer(label: str) -> str | None:
    """
    Look up an answer for a form field label.
    Returns the answer string if found, None if not found.
    Matching is case-insensitive and partial (question contains key).
    """
    label_l = label.lower().strip()

    # UUID-format labels (e.g. a08e2dc4-9bce-433e-948a-c21dda0a9144) → default Yes
    if _UUID_RE.match(label_l):
        return "Yes"

    # Hash-ID labels starting with q_ → default Yes
    if label_l.startswith("q_") and len(label_l) > 10:
        return "Yes"

    # Exact match first
    if label_l in QA:
        return QA[label_l]

    # Partial match — LONGER keys win over shorter ones to avoid false matches
    # e.g. "country phone code" must beat "phone" when label is "Country Phone Code*"
    # Safety: skip very short keys (< 5 chars)
    matches = [
        (key, answer) for key, answer in QA.items()
        if len(key) >= 5 and key in label_l
    ]
    if matches:
        # Return the match whose key is longest (most specific)
        best_key, best_answer = max(matches, key=lambda x: len(x[0]))
        return best_answer

    return None
