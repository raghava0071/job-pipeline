#!/usr/bin/env python3
# =============================================================================
# _TEST_NO_LIVE_CLAUDE_QA.PY — proves the v1.9.8 fix: the live-Claude call was
# removed entirely from BOTH _smart_fill_questions() and
# _smart_fill_custom_dropdowns(), no `anthropic` import is reachable from
# either function anymore, uncached fields fall straight to the free
# fallback (PROFILE_FALLBACK / first-option), and anything still unanswered
# gets logged to data/stuck_questions.json instead of crashing the job.
#
# Root bug this closes: `_smart_fill_questions()` used to run
# `import anthropic` unconditionally whenever any field reached Layer 4
# uncached, with NO try/except around the import itself — only around the
# API call. On a machine without `anthropic` installed, that raised an
# unhandled ModuleNotFoundError and crashed the whole job mid-form
# (confirmed live on Boeing). Raghav's explicit decision: remove the live
# call entirely rather than install anthropic — no API calls, no cost.
# =============================================================================

import json
import re
import subprocess
import tempfile
from pathlib import Path

SRC_FILE = Path(__file__).parent / "workday_apply_now.py"
SRC = SRC_FILE.read_text()


def test_no_anthropic_import_anywhere_in_file():
    """The whole point: no live Claude call reachable from this file at all.
    Checks actual code lines (ignoring comment lines, which are allowed to
    mention 'import anthropic' in prose explaining what was removed)."""
    code_lines = [l for l in SRC.splitlines() if not l.strip().startswith("#")]
    bad_import = [l for l in code_lines if re.search(r'^\s*import anthropic\b', l)]
    assert not bad_import, (
        f"found a live `import anthropic` statement still in workday_apply_now.py: {bad_import} — "
        "the v1.9.8 removal was supposed to delete every one"
    )
    bad_client = [l for l in code_lines if "anthropic.Anthropic(" in l]
    assert not bad_client, (
        f"found a live anthropic client construction still in workday_apply_now.py: {bad_client}"
    )
    print("✅ PASS: no `import anthropic` statement / `anthropic.Anthropic(` "
          "construction in any code line of workday_apply_now.py — the "
          "live-Claude Q&A dependency is fully gone (only explanatory comment "
          "prose mentions the old import)")


def test_smart_fill_questions_function_body_has_no_claude_layer():
    """Extract the literal _smart_fill_questions() function body and confirm
    the removed block (client.messages.create / CLAUDE_MODEL_FAST prompt for
    field-filling) is gone, while the PROFILE_FALLBACK dict and its loop —
    the thing fields now fall straight through to — are still present."""
    start = SRC.index("def _smart_fill_questions(")
    end = SRC.index("\ndef _smart_fill_custom_dropdowns(")
    body = SRC[start:end]

    assert "client.messages.create" not in body, (
        "a live Claude API call is still present inside _smart_fill_questions()"
    )
    assert "PROFILE_FALLBACK = {" in body, (
        "PROFILE_FALLBACK dict missing — uncached fields would have nothing to fall back to"
    )
    assert "_log_stuck_fields(" in body, (
        "_smart_fill_questions() never calls _log_stuck_fields() — unanswered "
        "fields would silently vanish instead of being logged for follow-up"
    )
    print("✅ PASS: _smart_fill_questions() body has no live Claude call, still "
          "has PROFILE_FALLBACK, and calls _log_stuck_fields() for leftovers")


def test_smart_fill_custom_dropdowns_function_body_has_no_claude_layer():
    start = SRC.index("def _smart_fill_custom_dropdowns(")
    # function runs to end of file section before the next top-level def/EOF
    rest = SRC[start:]
    end_rel = rest.index("\ndef ", 1) if "\ndef " in rest[1:] else len(rest)
    body = rest[:end_rel]

    assert "client.messages.create" not in body, (
        "a live Claude API call is still present inside _smart_fill_custom_dropdowns()"
    )
    assert '["options"][0]' in body, (
        "first-option fallback missing — uncached dropdowns would have nothing "
        "to fall back to without Claude"
    )
    assert "_log_stuck_fields(" in body, (
        "_smart_fill_custom_dropdowns() never calls _log_stuck_fields()"
    )
    print("✅ PASS: _smart_fill_custom_dropdowns() body has no live Claude call, "
          "still has the first-option fallback, and calls _log_stuck_fields()")


def _extract_log_stuck_fields_js_free_python() -> str:
    """Pull the literal _log_stuck_fields() function body (pure Python, no
    JS involved) so we can exec() it directly against a fake cfg/datetime and
    a temp file, proving the real shipped logging logic writes the schema we
    expect and never raises."""
    start = SRC.index("def _log_stuck_fields(")
    end = SRC.index("\ndef _build_profile_context(")
    return SRC[start:end]


def test_log_stuck_fields_writes_expected_schema_and_never_raises():
    import types
    from datetime import datetime as _dt

    func_src = _extract_log_stuck_fields_js_free_python()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        class FakeCfg:
            BASE_DIR = tmp_path

        namespace = {
            "cfg": FakeCfg,
            "json": json,
            "datetime": _dt,
            "print": lambda *a, **k: None,
        }
        exec(func_src, namespace)
        log_stuck_fields = namespace["_log_stuck_fields"]

        fields = [
            {"label": "How did you hear about us?", "type": "button", "options": ["A", "B"]},
            {"label": "Preferred pronoun", "type": "text", "options": []},
        ]
        log_stuck_fields(fields, "Data Analyst", "TestCo", "_smart_fill_questions")

        out_file = tmp_path / "data" / "stuck_questions.json"
        assert out_file.exists(), "_log_stuck_fields() did not create data/stuck_questions.json"
        data = json.loads(out_file.read_text())
        assert isinstance(data, list) and len(data) == 1
        entry = data[0]
        assert entry["company"] == "TestCo"
        assert entry["job_title"] == "Data Analyst"
        assert entry["source"] == "_smart_fill_questions"
        assert [f["label"] for f in entry["fields"]] == [
            "How did you hear about us?", "Preferred pronoun"
        ]
        assert "manual answer" in entry["status"]

        # Empty list must be a true no-op (no file created) — confirms the
        # common case (nothing stuck) never touches disk or raises.
        log_stuck_fields([], "X", "Y", "_smart_fill_questions")
        data2 = json.loads(out_file.read_text())
        assert len(data2) == 1, "empty fields list should not have appended anything"

        # A broken write path must not raise — swallow and print only.
        class BrokenCfg:
            BASE_DIR = tmp_path / "nonexistent" / "\0bad"  # invalid path char
        namespace["cfg"] = BrokenCfg
        try:
            log_stuck_fields(fields, "X", "Y", "_smart_fill_questions")
        except Exception as e:
            raise AssertionError(f"_log_stuck_fields() raised instead of swallowing: {e}")

    print("✅ PASS: _log_stuck_fields() writes the expected schema, is a true "
          "no-op for an empty list, and never raises even on a broken path")


def test_no_crash_simulation_uncached_field_falls_to_profile_fallback():
    """End-to-end-ish proof of the actual bug scenario: build a minimal stand-in
    for the post-cache-layers state (one uncached field with no cache match,
    matching a PROFILE_FALLBACK keyword) and confirm the literal shipped
    PROFILE_FALLBACK loop (extracted, not reimplemented) resolves it WITHOUT
    ever importing anthropic — i.e. the exact crash scenario (anthropic
    missing) cannot occur because nothing in this path imports it."""
    start = SRC.index("    _smart_salary = _pick_salary(jd_text, job_title)")
    end = SRC.index("_log_stuck_fields(_still_stuck, job_title, company, \"_smart_fill_questions\")") \
          + len("_log_stuck_fields(_still_stuck, job_title, company, \"_smart_fill_questions\")")
    block = SRC[start:end]
    lines = block.splitlines()
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    strip_n = min(indents)
    block = "\n".join(l[strip_n:] if len(l) >= strip_n else l for l in lines)

    assert "import anthropic" not in block, "uncached-field resolution path still imports anthropic"

    calls = []
    namespace = {
        "_pick_salary": lambda jd, jt: "$70,000",
        "jd_text": "", "job_title": "Data Analyst", "company": "TestCo",
        "uncached": [{"label": "Are you legally authorized to work in the US?", "type": "radio"}],
        "answers": {},
        "_claude_ans": None,
        "print": lambda *a, **k: None,
        "_log_stuck_fields": lambda fields, jt, c, src: calls.append((fields, jt, c, src)),
    }
    exec(block, namespace)

    answers = namespace["answers"]
    assert answers.get("Are you legally authorized to work in the US?") == "Yes", (
        f"expected PROFILE_FALLBACK to resolve the work-authorization field for free, got: {answers}"
    )
    assert calls and calls[0][0] == [], (
        f"expected _log_stuck_fields to be called with an empty stuck list "
        f"(field WAS resolved by fallback), got: {calls}"
    )
    print("✅ PASS: an uncached field with no anthropic available resolves via "
          "PROFILE_FALLBACK with zero import of anthropic, and _log_stuck_fields "
          "is called with nothing stuck (field was answered for free)")


if __name__ == "__main__":
    test_no_anthropic_import_anywhere_in_file()
    test_smart_fill_questions_function_body_has_no_claude_layer()
    test_smart_fill_custom_dropdowns_function_body_has_no_claude_layer()
    test_log_stuck_fields_writes_expected_schema_and_never_raises()
    test_no_crash_simulation_uncached_field_falls_to_profile_fallback()
    print("\n✅ ALL CHECKS PASSED — live-Claude dependency fully removed from "
          "Workday question-answering; uncached fields resolve via free "
          "PROFILE_FALLBACK/first-option, unanswered ones are logged to "
          "data/stuck_questions.json, and none of this can crash on a missing "
          "anthropic package.")
