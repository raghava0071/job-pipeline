"""
Throwaway verification script — NOT part of the pipeline, delete after running.

Confirms the new diagnostics added to _pin_captcha_box()'s caller and
_captcha_actually_visible() actually fire, using the same fake-frame harness
pattern already used earlier for the CAPTCHA overlay fix. Checks:
  1. Signal-transition logging fires exactly once per real state change
     (not once per poll tick).
  2. Pin diagnostics print a bounding box / computed style read for the
     pinned bframe + ancestor, and correctly flags a zero-sized element.
  3. The whole flow still ends in a successful "solved" result — the new
     logging doesn't change control flow, only adds visibility.
"""
import sys
import datetime as _real_datetime_module
from unittest.mock import MagicMock

sys.path.insert(0, ".")
import indeed_apply_now as m

# v1.9.3 added a 3-second minimum-time floor before any "solved" declaration
# is trusted, measured against real datetime.now() — which barely advances
# with time.sleep() mocked to a no-op. Fake the clock, advanced 1 simulated
# second per wait-loop tick, so SOLVE_AFTER_CHECKS below still lands past
# the floor instead of being blocked for all 600 ticks until timeout.
class FakeDatetime(_real_datetime_module.datetime):
    _current = _real_datetime_module.datetime(2026, 7, 12, 18, 0, 0)
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

SOLVE_AFTER_CHECKS = 8  # solve after a few polls so re-pin logic gets exercised

class FakeElement:
    def __init__(self, tag, zero_sized=False):
        self.tag = tag
        self.zero_sized = zero_sized
    def evaluate(self, js, *a, **kw):
        if "removeAttribute" in js:
            return None
        if "getBoundingClientRect" in js:
            w, h = (0, 0) if self.zero_sized else (480, 760)
            return {
                "tag": "IFRAME",
                "rect": {"x": 100, "y": 50, "w": w, "h": h},
                "position": "fixed",
                "zIndex": "2147483647",
                "display": "block",
                "visibility": "visible",
            }
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
    def __init__(self, url, parent=None, zero_sized=False):
        self.url = url
        self.parent_frame = parent
        self.page = MagicMock()
        self._element = FakeElement(url, zero_sized=zero_sized)
    def frame_element(self):
        return self._element
    def locator(self, sel):
        fl = MagicMock()
        fl.first = FakeBodyLocatorFirst() if sel == "body" else FakeButtonLocatorFirst()
        return fl
    def evaluate(self, js, *a, **kw):
        # v1.9.3's container-walk query (_read_scoped_token) — this test's
        # frames don't model a shared DOM container, so report "nothing
        # found" and let the real code fall through to the frame-wide
        # fallback below, which this test's state["solved"] flag drives.
        if "ancestorsOf" in js:
            return None
        # v1.9.3's frame-wide fallback token check — {token_len, element_id}.
        if "token_len" in js and "g-recaptcha-response" in js:
            return {"token_len": 60, "element_id": "stub-id"} if state["solved"] else {"token_len": 0, "element_id": None}
        if "g-recaptcha-response" in js:
            return 60 if state["solved"] else 0
        if "aria-checked" in js:
            return False
        if "select all images" in js or "select all squares" in js:
            return False
        if "document.body ? document.body.innerText" in js:
            return ""
        if "kws" in js:
            return None
        return None

# Make the bframe report a zero-sized box on the FIRST pin (simulating the
# "styled but never actually rendered" failure mode), then a real size later.
bframe     = FakeFrame("https://www.recaptcha.net/recaptcha/enterprise/bframe?ar=1", zero_sized=True)
anchor     = FakeFrame("https://www.recaptcha.net/recaptcha/enterprise/anchor?ar=1")
smartapply = FakeFrame("https://smartapply.indeed.com/beta/indeedapply/form/review")
bframe.parent_frame = smartapply

fake_page = MagicMock()
fake_page.frames = [bframe, anchor, smartapply]
fake_page.url = "https://smartapply.indeed.com/beta/indeedapply/form/review"

result = m._check_and_handle_captcha(fake_page, title="Data Analyst", company="Jobot", job_url="https://indeed.com/x")
builtins.print = _orig_print

assert result is True, f"expected solved -> True, got {result!r}"
print(f"[1/1 control] solve flow still completes normally with diagnostics attached — OK")

signal_lines = [l for l in captured_prints if "🔬 captcha signal" in l]
pin_lines = [l for l in captured_prints if "🔬 pin check" in l or "🔬 pin diagnostics" in l]

assert len(signal_lines) >= 1, "expected at least one signal-transition log line"
# Should not print one line per poll tick (600 possible) -- transitions only.
assert len(signal_lines) < 10, f"signal logging looks like it's firing every tick, not on transitions: {len(signal_lines)} lines"
print(f"[2/3] signal-transition logging fired {len(signal_lines)} time(s) (transition-only, not per-tick) — OK")
for l in signal_lines:
    print("       " + l.strip())

assert len(pin_lines) >= 1, "expected at least one pin-diagnostics log line"
zero_size_flagged = any("ZERO-SIZE" in l for l in pin_lines)
assert zero_size_flagged, "expected the zero-sized bframe to be flagged as NOT ACTUALLY RENDERED"
print(f"[3/3] pin diagnostics fired {len(pin_lines)} time(s), correctly flagged the zero-sized element — OK")
for l in pin_lines:
    print("       " + l.strip())

print("\nALL CHECKS PASSED")
