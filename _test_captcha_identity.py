"""
Throwaway verification script — NOT part of the pipeline, kept on disk per
Raghav's request (not deleted after running, same as _test_pin_diagnostics.py).

Confirms the new _log_captcha_identity_snapshot() diagnostic:
  1. Fires once at "DETECTED" and once at "SOLVED" for the same CAPTCHA event.
  2. The token textarea's stamped probe_id is IDENTICAL between the DETECTED
     and SOLVED snapshots when it's the same underlying DOM node (simulated
     via a stateful FakeElement that mimics the real dataset-stamping JS:
     stamps a random id on first read, returns the SAME id on every
     subsequent read -- exactly what el.dataset.pwProbeId does in a real
     browser).
  3. The detection sequence number (#1, #2, ...) increments correctly and
     labels both snapshots for the same event with the same number.
"""
import sys
import datetime as _real_datetime_module
from unittest.mock import MagicMock

sys.path.insert(0, ".")
import indeed_apply_now as m

# v1.9.3 added a 3-second minimum-time floor before any "solved" declaration
# is trusted, measured against real datetime.now() — which barely advances
# with time.sleep() mocked to a no-op. Fake the clock, advanced 1 simulated
# second per wait-loop tick (matching the real code's time.sleep(1) calls),
# so SOLVE_AFTER_CHECKS below still lands past the floor instead of being
# blocked for all 600 ticks until timeout.
class FakeDatetime(_real_datetime_module.datetime):
    _current = _real_datetime_module.datetime(2026, 7, 12, 20, 0, 0)
    @classmethod
    def now(cls, tz=None):
        return cls._current

m.datetime = FakeDatetime

def _ticking_sleep(*_a, **_kw):
    FakeDatetime._current += _real_datetime_module.timedelta(seconds=1)
m.time.sleep = _ticking_sleep

state = {"visibility_checks": 0, "solved": False}
captured_prints = []
_orig_print = print
def _capturing_print(*a, **kw):
    captured_prints.append(" ".join(str(x) for x in a))
    _orig_print(*a, **kw)
import builtins
builtins.print = _capturing_print

SOLVE_AFTER_CHECKS = 3

class StatefulTokenElement:
    """Simulates a real DOM node: el.dataset.pwProbeId is stamped once and
    stays fixed across every later read of the SAME element -- this is what
    the real JS does, so this Python object stands in for "the same node"."""
    def __init__(self):
        self.probe_id = None
    def stamp_and_read(self):
        if self.probe_id is None:
            import random, string
            self.probe_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))
        return self.probe_id

token_el = StatefulTokenElement()  # ONE node, reused across every check -- as real Google JS would

class FakeElement:
    def __init__(self, tag):
        self.tag = tag
    def evaluate(self, js, *a, **kw):
        if "removeAttribute" in js:
            return None
        return None
    def is_visible(self, timeout=None):
        state["visibility_checks"] += 1
        visible = state["visibility_checks"] < SOLVE_AFTER_CHECKS
        if not visible:
            state["solved"] = True
        return visible
    def bounding_box(self):
        return {"x": 0, "y": 0, "width": 10, "height": 10}

class FakeBodyLocatorFirst:
    def is_visible(self, timeout=None):
        return state["visibility_checks"] < SOLVE_AFTER_CHECKS

class FakeButtonLocatorFirst:
    def count(self): return 0
    def is_visible(self, timeout=None): return False

class FakeFrame:
    def __init__(self, url, parent=None):
        self.url = url
        self.parent_frame = parent
        self.page = MagicMock()
        self._element = FakeElement(url)
    def frame_element(self):
        return self._element
    def locator(self, sel):
        fl = MagicMock()
        fl.first = FakeBodyLocatorFirst() if sel == "body" else FakeButtonLocatorFirst()
        return fl
    def evaluate(self, js, *a, **kw):
        # v1.9.3 added _read_scoped_token() (container-walk query, marked by
        # "ancestorsOf" in its JS) ahead of the plain frame-wide token check
        # in the real code — this test isn't exercising that container walk
        # (see _test_captcha_regression_suite.py for that), so it reports
        # "nothing found" here, which correctly makes the real code fall
        # through to the frame-wide fallback path below instead.
        if "ancestorsOf" in js:
            return None
        # Identity-snapshot JS asks for {value_len, value_preview, probe_id}
        # — distinct key shape from the newer token-check paths below, so
        # match on "value_len" specifically rather than the generic
        # "g-recaptcha-response" substring both now share.
        if "value_len" in js:
            return {
                "value_len": 88 if state["solved"] else 0,
                "value_preview": "03AGdBq27abc" if state["solved"] else "",
                "probe_id": token_el.stamp_and_read(),
            }
        # v1.9.3's frame-wide fallback token check — {token_len, element_id}.
        if "token_len" in js and "g-recaptcha-response" in js:
            return (
                {"token_len": 88, "element_id": token_el.stamp_and_read()}
                if state["solved"] else
                {"token_len": 0, "element_id": None}
            )
        if "aria-checked" in js:
            if "pwProbeId" in js:
                return None  # no anchor checkbox in this mock
            return False
        if "select all images" in js or "select all squares" in js:
            return False
        if "document.body ? document.body.innerText" in js:
            return ""
        if "kws" in js:
            return None
        return None

bframe     = FakeFrame("https://www.recaptcha.net/recaptcha/enterprise/bframe?ar=1")
anchor     = FakeFrame("https://www.recaptcha.net/recaptcha/enterprise/anchor?ar=1")
smartapply = FakeFrame("https://smartapply.indeed.com/beta/indeedapply/form/review")
bframe.parent_frame = smartapply

fake_page = MagicMock()
fake_page.frames = [bframe, anchor, smartapply]
fake_page.url = "https://smartapply.indeed.com/beta/indeedapply/form/review"

result = m._check_and_handle_captcha(fake_page, title="Data Analyst", company="Jobot", job_url="https://indeed.com/x")
builtins.print = _orig_print

assert result is True, f"expected solved -> True, got {result!r}"

identity_lines = [l for l in captured_prints if "🔬 [" in l]
detected_token_lines = [l for l in identity_lines if "[DETECTED #1] token textarea" in l]
solved_token_lines   = [l for l in identity_lines if "[SOLVED #1] token textarea" in l]

assert len(detected_token_lines) == 1, f"expected exactly 1 DETECTED token line, got {detected_token_lines}"
assert len(solved_token_lines) == 1, f"expected exactly 1 SOLVED token line, got {solved_token_lines}"
print("[1/3] DETECTED and SOLVED snapshots both fired, tagged #1 — OK")
print("       " + detected_token_lines[0].strip())
print("       " + solved_token_lines[0].strip())

# Extract element_id=... from both lines and confirm they match (same node).
import re
det_id = re.search(r"element_id=(\S+)", detected_token_lines[0]).group(1)
sol_id = re.search(r"element_id=(\S+)", solved_token_lines[0]).group(1)
assert det_id == sol_id, f"expected SAME element_id across DETECTED and SOLVED (same underlying node in this mock), got {det_id} vs {sol_id}"
print(f"[2/3] token element_id identical across DETECTED and SOLVED ({det_id}) — confirms this diagnostic correctly identifies 'same node' — OK")

assert "len=0" in detected_token_lines[0], f"expected token len=0 at DETECTED time (must be, or the function would never have printed DETECTED at all), got: {detected_token_lines[0]}"
assert "len=88" in solved_token_lines[0], f"expected non-zero token len at SOLVED time, got: {solved_token_lines[0]}"
print("[3/3] token was empty at DETECTED, non-empty at SOLVED — matches expected real-solve shape in this mock — OK")

print("\nALL CHECKS PASSED")
