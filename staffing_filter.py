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

    for w in getattr(cfg, "STAFFING_CONSULTANCY_COMPANY_WORDS", set()):
        if w in c:
            return True, f"staffing/consultancy company: '{w}'"

    if d:
        for w in getattr(cfg, "STAFFING_CONSULTANCY_DESC_SIGNALS", set()):
            if w in d:
                return True, f"staffing/consultancy language in description: '{w}'"

    return False, ""
