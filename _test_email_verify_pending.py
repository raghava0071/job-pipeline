#!/usr/bin/env python3
# =============================================================================
# _TEST_EMAIL_VERIFY_PENDING.PY — proves the v1.9.7 fix in
# workday_create_account() against the exact message confirmed live on Spgi:
# "An email has been sent to you. Please verify your account."
# Kept on disk per established precedent.
#
# Two things proved, both against the literal shipped source (not a
# paraphrase):
#   1. The expanded EMAIL_VERIFY_PENDING_PHRASES list (extracted directly
#      from workday_apply_now.py) actually matches Spgi's exact wording —
#      the old list didn't ("sent you an email" vs "has been sent to you").
#   2. The errors-panel branch, given errors containing that exact message,
#      calls the verification-wait path and does NOT call _failure_shot or
#      return False/'exists' — the specific inconsistency reported live
#      (Relx's sign-in deferred cleanly, Spgi's create-account errors-
#      checker hard-failed on the identical underlying state).
# =============================================================================

import re
from pathlib import Path

SRC_FILE = Path(__file__).parent / "workday_apply_now.py"

SPGI_MESSAGE = "An email has been sent to you. Please verify your account."


def _extract_phrase_list() -> list:
    text = SRC_FILE.read_text()
    start = text.index("EMAIL_VERIFY_PENDING_PHRASES = [")
    start += len("EMAIL_VERIFY_PENDING_PHRASES = ")
    end = text.index("]", start) + 1
    literal = text[start:end]
    # Safe to eval — it's a literal list of string constants in our own source.
    return eval(literal)


def _extract_errors_branch() -> str:
    """Pull the literal `if errors: ... else: ... return False` block."""
    text = SRC_FILE.read_text()
    start = text.index("        if errors:\n")
    end = text.index("_failure_shot(page, f\"createacct_{company_key}\")", start)
    end = text.index("return False", end) + len("return False")
    block = text[start:end]
    lines = block.splitlines()
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    strip_n = min(indents)
    return "\n".join(l[strip_n:] if len(l) >= strip_n else l for l in lines)


class _CalledAwaitVerification(Exception):
    pass


class _CalledFailureShot(Exception):
    pass


class _ReturnedExists(Exception):
    pass


def test_phrase_list_matches_spgi_message():
    phrases = _extract_phrase_list()
    matched = [p for p in phrases if p in SPGI_MESSAGE.lower()]
    assert matched, (
        f"EMAIL_VERIFY_PENDING_PHRASES does not match the real Spgi message "
        f"{SPGI_MESSAGE!r} — the exact bug this fix targets. Phrases: {phrases}"
    )
    # Also confirm the OLD phrase list genuinely would have missed it —
    # proves this is a real fix, not a no-op.
    old_phrases = ["check your email", "verify your email", "verification email",
                   "sent you an email", "please check", "confirm your email"]
    old_matched = [p for p in old_phrases if p in SPGI_MESSAGE.lower()]
    assert not old_matched, (
        f"expected the OLD phrase list to miss Spgi's message (that was the bug) "
        f"but it matched via {old_matched} — re-check the premise"
    )
    print(f"✅ PASS: new phrase list matches Spgi's exact message via {matched}; "
          f"old phrase list matched nothing (confirmed real gap, now closed)")


def test_errors_branch_treats_spgi_message_as_pending_not_failure():
    block = _extract_errors_branch()
    # Swap the three possible exits for distinguishable exceptions so the
    # test can assert exactly which branch the real code takes.
    block = block.replace(
        '            print(f"          ↩  Email already registered for {company_key}")\n'
        '            return "exists"',
        '            raise _ReturnedExists()'
    )
    block = block.replace(
        '        _failure_shot(page, f"createacct_{company_key}")\n'
        '        return False',
        '        raise _CalledFailureShot()'
    )

    def fake_await_email_verification():
        raise _CalledAwaitVerification()

    namespace = {
        "errors": [SPGI_MESSAGE],
        "EMAIL_VERIFY_PENDING_PHRASES": _extract_phrase_list(),
        "_await_email_verification": fake_await_email_verification,
        "_failure_shot": lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("_failure_shot should NOT be called for a pending-verification message")
        ),
        "company_key": "spgi",
        "page": None,
        "print": lambda *a, **k: None,
        "_ReturnedExists": _ReturnedExists,
        "_CalledFailureShot": _CalledFailureShot,
    }

    raised = None
    try:
        exec(block, namespace)
    except _CalledAwaitVerification:
        raised = "await_verification"
    except _CalledFailureShot:
        raised = "failure_shot"
    except _ReturnedExists:
        raised = "exists"
    except AssertionError as e:
        raise

    assert raised == "await_verification", (
        f"expected the pending-verification path to run, got: {raised!r}"
    )
    print("✅ PASS: errors-panel branch, given Spgi's exact message, calls "
          "_await_email_verification() — NOT _failure_shot, NOT a hard 'return False'")


def test_errors_branch_still_fails_on_a_real_error():
    """Regression guard: a genuine validation error (unrelated wording)
    must still hard-fail — this fix must not swallow real errors."""
    block = _extract_errors_branch()
    block = block.replace(
        '            print(f"          ↩  Email already registered for {company_key}")\n'
        '            return "exists"',
        '            raise _ReturnedExists()'
    )
    block = block.replace(
        '        _failure_shot(page, f"createacct_{company_key}")\n'
        '        return False',
        '        raise _CalledFailureShot()'
    )

    namespace = {
        "errors": ["Password does not meet the minimum requirements"],
        "EMAIL_VERIFY_PENDING_PHRASES": _extract_phrase_list(),
        "_await_email_verification": lambda: (_ for _ in ()).throw(
            AssertionError("should NOT treat a real validation error as pending-verification")
        ),
        "_failure_shot": lambda *a, **k: None,
        "company_key": "testco",
        "page": None,
        "print": lambda *a, **k: None,
        "_ReturnedExists": _ReturnedExists,
        "_CalledFailureShot": _CalledFailureShot,
    }
    raised = None
    try:
        exec(block, namespace)
    except _CalledFailureShot:
        raised = "failure_shot"
    except _ReturnedExists:
        raised = "exists"
    assert raised == "failure_shot", f"expected a real error to still hard-fail, got: {raised!r}"
    print("✅ PASS: a genuine, unrelated validation error still hard-fails as before (no regression)")


if __name__ == "__main__":
    test_phrase_list_matches_spgi_message()
    test_errors_branch_treats_spgi_message_as_pending_not_failure()
    test_errors_branch_still_fails_on_a_real_error()
    print("\n✅ ALL CHECKS PASSED — Spgi's exact message is now recognized as "
          "success-pending-verification in the errors-panel path too, and "
          "real validation errors still fail correctly.")
