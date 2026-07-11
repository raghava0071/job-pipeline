# =============================================================================
# STAFFING_FILTER.PY — Skip staffing agencies and consulting firms
#
# User preference, not fraud detection: these are frequently real, legitimate
# employers (TCS, Accenture, Robert Half, etc.) — Raghav just doesn't want to
# work through a staffing agency or land on a client-placement/bench-style
# consulting engagement. Checked independently of config.COMPANY_WHITELIST,
# which only vouches for "not a scam" — being whitelisted there does NOT
# exempt a company from this check.
#
# Used by both indeed_apply_now.py and linkedin_apply_now.py so the rule is
# defined once and stays identical across platforms.
# =============================================================================

import config as cfg


def is_staffing_or_consultancy(company: str, description: str = ""):
    """
    Return (True, reason) if this looks like a staffing agency or
    consulting/IT-services firm rather than a direct employer.

    Checks company name against STAFFING_CONSULTANCY_COMPANY_WORDS and, if a
    description is provided, also checks for staffing/consulting-style
    language (e.g. "on behalf of our client"). Both lists live in config.py.
    """
    if not getattr(cfg, "SKIP_STAFFING_CONSULTANCY", True):
        return False, ""

    c = (company or "").lower().strip()
    d = (description or "").lower()

    # Explicit exceptions, checked FIRST — 2026-07-10, Raghav asked to allow
    # the big, well-known Indian IT majors (TCS, Infosys, Wipro, Cognizant,
    # HCL, Tech Mahindra) again. Their brand names were removed from the
    # generic keyword list below, but a plain substring check alone isn't
    # enough: "Tata Consultancy Services" still contains the generic word
    # "consultancy" (which needs to stay generic, to keep catching real
    # unnamed consulting firms), so without this explicit allowlist check
    # TCS would still get blocked by that unrelated match. Checked before the
    # generic word loop so these names always win regardless of what generic
    # keyword their official name happens to contain.
    for allowed in getattr(cfg, "STAFFING_CONSULTANCY_EXPLICIT_ALLOW", set()):
        if allowed in c:
            return False, ""

    for w in getattr(cfg, "STAFFING_CONSULTANCY_COMPANY_WORDS", set()):
        if w in c:
            return True, f"staffing/consultancy company: '{w}'"

    if d:
        for w in getattr(cfg, "STAFFING_CONSULTANCY_DESC_SIGNALS", set()):
            if w in d:
                return True, f"staffing/consultancy language in description: '{w}'"

    return False, ""
