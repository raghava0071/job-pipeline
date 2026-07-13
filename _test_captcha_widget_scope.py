"""
Throwaway verification script — NOT part of the pipeline, kept on disk per
Raghav's established precedent (same as the other _test_*.py files: not
deleted after use).

Verifies the v1.9.2 fix: the live 09:14 AM run showed the authoritative
"solved" token check picking up a STALE, unrelated g-recaptcha-response
element (constant len=2361) from some other frame on the page, while the
ACTUAL challenge's own token (independently confirmed via the identity-
snapshot diagnostic) stayed len=0 the whole time. This caused the pipeline
to declare "solved" ~1s after every pin and auto-click Submit prematurely,
which is what was disturbing the live widget and causing it to reshape/
reset every cycle (6 cycles observed before Raghav killed the run).

Checks:
  1. A frame OTHER than the bframe's own parent has a persistent non-empty
     token from the start (the exact stale-widget shape observed live) —
     confirms the scoped check does NOT get fooled by it, and correctly
     keeps reporting "still showing" while the real widget's own token
     (scoped via _find_captcha_scope_frame) stays empty.
  2. Once the SCOPED (correct) token genuinely goes non-empty, "solved"
     correctly fires — proves the fix doesn't just always return "unsolved,"
     it still recognizes a genuine solve on the right element.
  3. The consolidated CAPTCHA EVENT SUMMARY block prints once, with the
     expected fields (Detected/Pinned/Poll history/Outcome/Elapsed),
     instead of requiring the scattered per-tick lines to be pieced
     together.
  4. No blind periodic re-pin fires while the same bframe node is stable
     and still on-screen — only the single initial pin.
"""
import sys
from unittest.mock import MagicMock

sys.path.insert(0, ".")
import indeed_apply_now as m

m.time.sleep = lambda *_a, **_kw: None
m.notifier.send_captcha_alert = lambda **_kw: True
m.notifier.send_alert = lambda **_kw: True
m.random.uniform = lambda *_a, **_kw: 0

captured = []
_orig_print = print
def _capturing_print(*a, **kw):
    line = " ".join(str(x) for x in a)
    captured.append(line)
    _orig_print(*a, **kw)
import builtins

SOLVE_AFTER_TICKS = 4
state = {"ticks": 0}


class FakeEl:
    def __init__(self, w=488, h=863, visible=True):
        self._w, self._h, self._visible = w, h, visible
    def is_visible(self, timeout=None):
        return self._visible
    def bounding_box(self):
        return {"x": 0, "y": 0, "width": self._w, "height": self._h}
    def evaluate(self, js, *a, **kw):
        if "getBoundingClientRect" in js and "getComputedStyle" in js:
            return {"tag": "IFRAME", "rect": {"x": 0, "y": 0, "w": self._w, "h": self._h},
                     "position": "fixed", "zIndex": "2147483647", "display": "block", "visibility": "visible"}
        if "getBoundingClientRect" in js:
            return {"w": self._w, "h": self._h}
        if "pwProbeId" in js:
            if not hasattr(self, "_probe_id"):
                import random, string
                self._probe_id = "".join(random.choices(string.ascii_lowercase, k=8))
            return self._probe_id
        if "removeAttribute" in js:
            return None
        return None


class FakeFrame:
    def __init__(self, url, parent=None, token_len_fn=None, aria_checked_fn=None, body_visible=True):
        self.url = url
        self.parent_frame = parent
        self.page = MagicMock()
        self._element = FakeEl()
        self._token_len_fn = token_len_fn or (lambda: 0)
        self._aria_checked_fn = aria_checked_fn or (lambda: False)
        self._body_visible = body_visible
    def frame_element(self):
        return self._element
    def locator(self, sel):
        fl = MagicMock()
        loc = MagicMock()
        loc.is_visible = lambda timeout=None: self._body_visible
        loc.count = lambda: 0  # no real Submit button found -> forces JS-click fallback path
        fl.first = loc
        return fl
    def evaluate(self, js, *a, **kw):
        if "g-recaptcha-response" in js and "pwProbeId" not in js:
            return self._token_len_fn()
        if "aria-checked" in js and "pwProbeId" not in js:
            return self._aria_checked_fn()
        if "rc-imageselect" in js:
            return None
        if "phrases" in js:
            return None
        if "document.body ? document.body.innerText" in js:
            return ""
        if "kws" in js:  # JS-click submit fallback query
            return None
        return None


smartapply = FakeFrame("https://smartapply.indeed.com/beta/indeedapply/form/review-m",
                        token_len_fn=lambda: (2400 if state["ticks"] >= SOLVE_AFTER_TICKS else 0))
bframe = FakeFrame("https://www.recaptcha.net/recaptcha/enterprise/bframe?hl=en&", parent=smartapply)
anchor = FakeFrame("https://www.recaptcha.net/recaptcha/enterprise/anchor?ar=1&k", parent=smartapply)
# The stale, unrelated frame: NOT the bframe's parent, persistent non-empty
# token from the very start -- this is the exact shape from the live log.
stale = FakeFrame("https://smartapply.indeed.com/beta/indeedapply/form/questions",
                   token_len_fn=lambda: 2361)

fake_page = MagicMock()
fake_page.frames = [bframe, anchor, smartapply, stale]
fake_page.url = "https://smartapply.indeed.com/beta/indeedapply/form/review-m"

# Drive the tick counter forward on every time.sleep() call inside the wait
# loop (the loop calls time.sleep(1) once per iteration).
_orig_sleep = m.time.sleep
def _ticking_sleep(*_a, **_kw):
    state["ticks"] += 1
m.time.sleep = _ticking_sleep

builtins.print = _capturing_print
try:
    result = m._check_and_handle_captcha(fake_page, title="AI Systems Engineer", company="Aeronace", job_url="https://indeed.com/x")
finally:
    builtins.print = _orig_print

repin_lines = [l for l in captured if "🔁 Re-pinning" in l]
pin_lines = [l for l in captured if "🔲 CAPTCHA window pinned" in l]
summary_lines = [l for l in captured if "CAPTCHA EVENT SUMMARY" in l]
solved_lines = [l for l in captured if "✅ CAPTCHA solved!" in l]
outcome_lines = [l for l in captured if "Outcome:" in l]

assert result is True, f"expected True (handled), got {result!r}"
print(f"[1/4] scoped token check ignored the stale frame's len=2361 the whole time "
      f"(only the correctly-scoped smartapply frame's own token mattered) — OK")

assert len(solved_lines) == 1, f"expected exactly 1 genuine solve, got {len(solved_lines)}: {solved_lines}"
print(f"[2/4] genuine solve on the SCOPED token still correctly detected once it "
      f"actually went non-empty (tick {SOLVE_AFTER_TICKS}) — OK")

assert len(summary_lines) == 1, f"expected exactly 1 consolidated summary block, got {len(summary_lines)}"
assert len(outcome_lines) == 1 and "Outcome:" in outcome_lines[0]
print(f"[3/4] consolidated CAPTCHA EVENT SUMMARY block printed once — OK")
for l in captured:
    if "│" in l or "CAPTCHA EVENT SUMMARY" in l or "└─" in l:
        print("       " + l.strip())

assert len(pin_lines) == 1, f"expected exactly 1 initial pin, got {len(pin_lines)}"
assert len(repin_lines) == 0, f"expected NO re-pins (same node, stable, on-screen the whole time), got {len(repin_lines)}: {repin_lines}"
print(f"[4/4] no blind re-pins fired while the bframe node stayed the same and on-screen — OK")

print("\nALL CHECKS PASSED")
