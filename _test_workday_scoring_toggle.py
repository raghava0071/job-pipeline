#!/usr/bin/env python3
# =============================================================================
# _TEST_WORKDAY_SCORING_TOGGLE.PY — proves the USE_CLAUDE_SCORING fix in
# workday_apply_now.py actually branches correctly, instead of just assuming
# the conditional reads right. Kept on disk per established precedent
# (_test_captcha_identity.py, _test_singleton_lock.py, etc.).
#
# Method: extracts the EXACT scoring block (lines 3365-3380 as of this
# writing, re-located by anchor text so it survives future line shifts) out
# of the real workday_apply_now.py source and exec()s it in a controlled
# namespace with cfg/ce/jdp mocked — this runs the literal shipped code,
# not a paraphrase of it. Confirms:
#   1. USE_CLAUDE_SCORING=False  -> jdp.ats_fit_score() is called,
#                                    ce.score_fit() is NOT called
#   2. USE_CLAUDE_SCORING=True   -> ce.score_fit() is called,
#                                    jdp.ats_fit_score() is NOT called
#   3. The threshold used to gate on switches with the same toggle
#      (FIT_THRESHOLD vs ATS_FIT_THRESHOLD)
# =============================================================================

import re
from pathlib import Path

SRC_FILE = Path(__file__).parent / "workday_apply_now.py"


def _extract_scoring_block() -> str:
    """Pull the real scoring conditional straight out of workday_apply_now.py
    by anchoring on the comment/marker text, so this test breaks loudly
    (KeyError-style) if the block ever gets restructured instead of silently
    testing stale code."""
    text = SRC_FILE.read_text()
    start_marker = "# Score fit — Claude (paid) or free ATS keyword match"
    end_marker = "skipped += 1; return"

    marker_pos = text.index(start_marker)
    start = text.rfind("\n", 0, marker_pos) + 1   # back up to start of that line,
                                                    # so its leading whitespace is
                                                    # included like every other line
    end = text.index(end_marker, marker_pos) + len(end_marker)
    block = text[start:end]

    # Dedent — the block lives inside a nested function, exec() needs it
    # at column 0. Find the common leading whitespace and strip it.
    lines = block.splitlines()
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    strip_n = min(indents)
    dedented = "\n".join(l[strip_n:] if len(l) >= strip_n else l for l in lines)
    return dedented


class _FakeCfg:
    def __init__(self, use_claude_scoring: bool):
        self.USE_CLAUDE_SCORING = use_claude_scoring
        self.FIT_THRESHOLD = 60
        self.ATS_FIT_THRESHOLD = 55   # deliberately different from FIT_THRESHOLD
                                       # so the test can prove the RIGHT one got used


class _CallRecorder:
    def __init__(self, name, return_value):
        self.name = name
        self.called = False
        self.return_value = return_value

    def __call__(self, *args, **kwargs):
        self.called = True
        return self.return_value


class _StopExec(Exception):
    """Swaps in for the real block's bare `return` (invalid at exec()'s
    module-level scope) — catching this is just how the test observes that
    the block finished, not part of the branch logic being tested."""
    pass


def _patch_return_for_exec(block: str) -> str:
    # The real block ends in `skipped += 1; return` inside a function.
    # exec() at module level can't use a bare `return`, so swap it for a
    # controlled exception the harness catches — this only changes how the
    # test observes completion, not the branch logic being tested.
    return block.replace("skipped += 1; return", "skipped += 1; raise _StopExec()")


def test_use_claude_scoring_false_calls_ats_fit_score():
    block = _patch_return_for_exec(_extract_scoring_block())
    fake_cfg = _FakeCfg(use_claude_scoring=False)
    fake_ce_score = _CallRecorder("ce.score_fit", {"score": 91, "grade": "A"})
    fake_jdp_score = _CallRecorder("jdp.ats_fit_score", {"score": 61, "grade": "B"})

    class _FakeCE:
        score_fit = staticmethod(fake_ce_score)

    class _FakeJDP:
        ats_fit_score = staticmethod(fake_jdp_score)

    namespace = {
        "cfg": fake_cfg, "ce": _FakeCE(), "jdp": _FakeJDP(),
        "getattr": getattr, "isinstance": isinstance,
        "print": lambda *a, **k: None, "_StopExec": _StopExec,
        "profile_summary": "PROFILE", "jd": "JD TEXT", "title": "Data Engineer",
        "company": "Acme", "scored": 0, "skipped": 0,
    }
    try:
        exec(block, namespace)
    except _StopExec:
        pass

    assert fake_jdp_score.called, "ats_fit_score was NOT called with USE_CLAUDE_SCORING=False"
    assert not fake_ce_score.called, "ce.score_fit WAS called even though USE_CLAUDE_SCORING=False"
    assert namespace["score"] == 61, "score wasn't taken from ats_fit_score's return value"
    assert namespace["_threshold"] == 55, (
        f"threshold should be ATS_FIT_THRESHOLD (55) when USE_CLAUDE_SCORING=False, "
        f"got {namespace['_threshold']}"
    )
    print("✅ PASS: USE_CLAUDE_SCORING=False -> jdp.ats_fit_score() called, "
          "ce.score_fit() NOT called, ATS_FIT_THRESHOLD used")


def test_use_claude_scoring_true_calls_ce_score_fit():
    block = _patch_return_for_exec(_extract_scoring_block())
    fake_cfg = _FakeCfg(use_claude_scoring=True)
    fake_ce_score = _CallRecorder("ce.score_fit", {"score": 91, "grade": "A"})
    fake_jdp_score = _CallRecorder("jdp.ats_fit_score", {"score": 61, "grade": "B"})

    class _FakeCE:
        score_fit = staticmethod(fake_ce_score)

    class _FakeJDP:
        ats_fit_score = staticmethod(fake_jdp_score)

    namespace = {
        "cfg": fake_cfg, "ce": _FakeCE(), "jdp": _FakeJDP(),
        "getattr": getattr, "isinstance": isinstance,
        "print": lambda *a, **k: None, "_StopExec": _StopExec,
        "profile_summary": "PROFILE", "jd": "JD TEXT", "title": "Data Engineer",
        "company": "Acme", "scored": 0, "skipped": 0,
    }
    # score=91 >= FIT_THRESHOLD(60) so the block does NOT hit the early
    # `return` this time — it falls through, which is also a valid, checkable
    # outcome (proves the ce.score_fit path doesn't get skipped/short-circuited).
    exec(block, namespace)

    assert fake_ce_score.called, "ce.score_fit was NOT called with USE_CLAUDE_SCORING=True"
    assert not fake_jdp_score.called, "ats_fit_score WAS called even though USE_CLAUDE_SCORING=True"
    assert namespace["score"] == 91, "score wasn't taken from ce.score_fit's return value"
    assert namespace["_threshold"] == 60, (
        f"threshold should be FIT_THRESHOLD (60) when USE_CLAUDE_SCORING=True, "
        f"got {namespace['_threshold']}"
    )
    print("✅ PASS: USE_CLAUDE_SCORING=True -> ce.score_fit() called, "
          "jdp.ats_fit_score() NOT called, FIT_THRESHOLD used")


if __name__ == "__main__":
    test_use_claude_scoring_false_calls_ats_fit_score()
    test_use_claude_scoring_true_calls_ce_score_fit()
    print("\n✅ ALL CHECKS PASSED — both branches of the real, extracted "
          "workday_apply_now.py scoring block execute correctly.")
