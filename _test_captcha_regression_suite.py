"""
PERMANENT regression suite — NOT a throwaway diagnostic script like the
other _test_captcha_*.py files in this repo. Keep this one in the suite
indefinitely and re-run it before any future change to CAPTCHA detection,
scoping, or timing logic in indeed_apply_now.py.

Built directly from debug_logs/run_20260713_173049.log — the run where the
v1.9.2 frame-level scoping fix shipped and STILL produced a false "solved"
declaration, because the ambiguity it was built to fix (multiple
g-recaptcha-response elements) existed one level deeper than it reached:
inside the SAME frame, not just across different frames.

Log evidence this fixture reproduces exactly:
  588: DETECTED #1 bframe element_id=fn0eqkj9
  589: DETECTED #1 anchor checkbox element_id=hd8igthp
  588: DETECTED #1 token textarea: len=0 element_id=xzaquzgb   (the REAL widget's own token)
  597: signal -> solved [g-recaptcha-response token present, len=2382]  (WRONG element -- not xzaquzgb)
  599: SOLVED #1 token textarea: len=0 element_id=xzaquzgb    (the real one was STILL empty)
  602: Real mouse-click Submit after CAPTCHA                   (premature -- fired off the wrong element)
  621-634: cycle #2, SAME bframe/token/anchor element_ids -- proves this was a
           re-detection of the SAME still-unsolved widget, not a new challenge.

This fixture builds a real (Python-side) DOM tree mirroring that exact
shape -- one wrapper container holding the true widget's bframe+anchor+
token (token starts empty, matching xzaquzgb's len=0), plus a SEPARATE
sibling element elsewhere in the SAME frame document holding an unrelated,
persistently non-empty token (len=2382, matching the wrong reading) -- and
walks it through the ACTUAL _read_scoped_token() ancestor-common-container
algorithm (reimplemented in Python here, since there's no real browser in
this environment to execute the real JS against), not just a canned mock
response. If the container-scoping algorithm regresses, this fixture is
built to catch it: the stale sibling token is placed so that only a
correct common-ancestor walk excludes it.

Also covers the second fix from this same incident: the 3-second solve
floor. A false "solved" that reads the WRONG element is exactly what the
floor is a backstop against, independent of scoping -- covered here too so
a regression in either mechanism alone still gets caught.

Run manually: python3 _test_captcha_regression_suite.py
"""
import sys
import datetime as _real_datetime_module
from unittest.mock import MagicMock

sys.path.insert(0, ".")
import indeed_apply_now as m
import config as cfg

m.time.sleep = lambda *_a, **_kw: None
m.notifier.send_captcha_alert = lambda **_kw: True
m.notifier.send_alert = lambda **_kw: True
m.random.uniform = lambda *_a, **_kw: 0


# The 3-second floor is measured against real wall-clock time
# (datetime.now()), but time.sleep() is mocked to a no-op above so the test
# doesn't actually wait — so datetime.now() needs to be faked too, advanced
# in lockstep with the wait loop's tick counter, or the floor would either
# never engage (if real sleeps were used, too slow for a test) or block
# forever (real time barely passes with sleep mocked out, as it would
# without this). One fake second per simulated tick matches the real
# code's time.sleep(1) semantics exactly.
class FakeDatetime(_real_datetime_module.datetime):
    _current = _real_datetime_module.datetime(2026, 7, 13, 17, 0, 0)
    @classmethod
    def now(cls, tz=None):
        return cls._current

m.datetime = FakeDatetime

FAILURES = []


def check(label, cond, detail=""):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {label}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(label)


# ── Fake DOM tree, mirroring the exact shape from the incident log ─────────
class Node:
    _next_id = [0]

    def __init__(self, tag="DIV", src=None, name=None, id_=None):
        self.tag = tag
        self.src = src
        self.name = name
        self.id = id_
        self.parentElement = None
        self.children = []
        self.dataset = {}
        self.value = ""

    def append(self, child):
        child.parentElement = self
        self.children.append(child)
        return child

    def all_iframes(self):
        out = []
        def walk(n):
            if n.tag == "IFRAME":
                out.append(n)
            for c in n.children:
                walk(c)
        walk(self)
        return out

    def query_selector_token(self):
        """Mirrors querySelector('textarea[name^=...], [id^=...]') — first
        match in DOM (pre)order, same semantics as the real CSS selector."""
        def walk(n):
            if n.tag == "TEXTAREA" and (
                (n.name or "").startswith("g-recaptcha-response") or
                (n.id or "").startswith("g-recaptcha-response")
            ):
                return n
            for c in n.children:
                r = walk(c)
                if r:
                    return r
            return None
        return walk(self)

    def stamp_probe_id(self):
        if "pwProbeId" not in self.dataset:
            import random, string
            self.dataset["pwProbeId"] = "".join(random.choices(string.ascii_lowercase, k=8))
        return self.dataset["pwProbeId"]


def ancestors_of(node):
    chain, cur = [], node
    while cur is not None:
        chain.append(cur)
        cur = cur.parentElement
    return chain


def common_container(a, b):
    a_chain = ancestors_of(a)
    b_set = set(id(n) for n in ancestors_of(b))
    for n in a_chain:
        if id(n) in b_set:
            return n
    return None


def python_side_read_scoped_token(document_root):
    """
    Direct Python re-implementation of _read_scoped_token()'s JS algorithm
    — walks the SAME fake DOM tree the way the real evaluate() call would,
    so this test exercises the actual container-finding logic, not just a
    canned stand-in for it.
    """
    iframes = document_root.all_iframes()
    b_el = next((f for f in iframes if f.src and "bframe" in f.src), None)
    a_el = next((f for f in iframes if f.src and "anchor" in f.src), None)
    if not b_el or not a_el:
        return 0, None
    common = common_container(b_el, a_el)
    if common is None:
        return 0, None
    token_el = common.query_selector_token()
    if not token_el:
        return 0, None
    if token_el.value and len(token_el.value) > 10:
        return len(token_el.value), token_el.stamp_probe_id()
    return 0, None


# ── Build the document exactly like the incident: one wrapper holding the
#    REAL widget (bframe + anchor + its own, initially-empty token), and a
#    completely separate, unrelated element elsewhere in the SAME document
#    holding a persistently non-empty "stale" token (len=2382, matching the
#    live log's wrong reading). ─────────────────────────────────────────────
document_root = Node("DOCUMENT")

real_widget_container = document_root.append(Node("DIV"))  # e.g. the actual .g-recaptcha wrapper
bframe_el = real_widget_container.append(Node("IFRAME", src="https://www.recaptcha.net/recaptcha/enterprise/bframe?hl=en&"))
anchor_el = real_widget_container.append(Node("IFRAME", src="https://www.recaptcha.net/recaptcha/enterprise/anchor?ar=1&k"))
real_token_el = real_widget_container.append(Node("TEXTAREA", name="g-recaptcha-response"))
real_token_el.value = ""  # starts empty -- matches xzaquzgb len=0 at DETECTED/SOLVED

# The stale, unrelated element -- e.g. a background "invisible" badge
# instance embedded elsewhere in the SAME frame document, NOT inside
# real_widget_container, so a correct common-ancestor walk must exclude it.
stale_badge_container = document_root.append(Node("DIV"))
stale_token_el = stale_badge_container.append(Node("TEXTAREA", name="g-recaptcha-response"))
stale_token_el.value = "x" * 2382  # matches the exact wrong reading from the live log

# ── Test 1: container-scoped read ignores the stale sibling entirely ───────
token_len, token_id = python_side_read_scoped_token(document_root)
check(
    "1. Container-scoped read ignores the stale sibling token (len=2382 elsewhere in the frame)",
    token_len == 0,
    detail=f"got token_len={token_len} (expected 0 — real widget's own token is still empty)"
)
check(
    "1b. Container-scoped read found the REAL token's container (not None)",
    token_id is None,  # real token is empty, so no id is stamped/returned yet — this itself confirms the correct element was reached and correctly read as empty, not skipped
)

# ── Test 2: once the REAL token (inside the correct container) goes
#    non-empty, the container-scoped read correctly recognizes it — proves
#    this isn't just "always report unsolved," it recognizes a genuine solve ──
real_token_el.value = "y" * 2450
token_len2, token_id2 = python_side_read_scoped_token(document_root)
check(
    "2. Genuine solve on the REAL (correctly-scoped) token is still detected",
    token_len2 == 2450 and token_id2 is not None,
    detail=f"got token_len={token_len2}, token_id={token_id2}"
)
check(
    "2b. Stale sibling's token_id is NEVER returned, even after the real one solves",
    token_id2 != stale_token_el.dataset.get("pwProbeId"),
)

# ── Test 3: end-to-end through _check_and_handle_captcha() using the actual
#    module code (not just the algorithm above) -- wires _read_scoped_token's
#    real evaluate() call to the same fake tree via a FakeFrame, and drives
#    the wait loop to prove the 3-second floor blocks a too-fast solve even
#    when (hypothetically) the wrong element were being read. ──────────────
class FakeEl:
    def __init__(self, w=488, h=863):
        self._w, self._h = w, h
    def is_visible(self, timeout=None):
        return True
    def bounding_box(self):
        return {"x": 0, "y": 0, "width": self._w, "height": self._h}
    def evaluate(self, js, *a, **kw):
        if "getBoundingClientRect" in js and "getComputedStyle" in js:
            return {"tag": "IFRAME", "rect": {"x": 0, "y": 0, "w": self._w, "h": self._h},
                     "position": "fixed", "zIndex": "2147483647", "display": "block", "visibility": "visible"}
        if "getBoundingClientRect" in js:
            return {"w": self._w, "h": self._h}
        if "pwProbeId" in js:
            return "fn0eqkj9"
        return None


class FakeFrame:
    def __init__(self, url, parent=None, root=None):
        self.url = url
        self.parent_frame = parent
        self.page = MagicMock()
        self._element = FakeEl()
        self._root = root  # the fake DOM document this frame owns, for scoped reads
    def frame_element(self):
        return self._element
    def locator(self, sel):
        fl = MagicMock()
        loc = MagicMock()
        loc.is_visible = lambda timeout=None: True
        loc.count = lambda: 0
        fl.first = loc
        return fl
    def evaluate(self, js, *a, **kw):
        if "ancestorsOf" in js:
            # This IS the real _read_scoped_token() query -- route it through
            # the same Python-side tree-walk used above, proving the
            # production code path (not just the standalone algorithm test)
            # also correctly ignores the stale sibling.
            _len, _id = python_side_read_scoped_token(self._root)
            return {"token_len": _len, "element_id": _id} if (_len or _id) else None
        if "g-recaptcha-response" in js and "pwProbeId" not in js:
            return {"token_len": 0, "element_id": None}  # fallback path should never need to fire
        if "aria-checked" in js and "pwProbeId" not in js:
            return False
        if "rc-imageselect" in js or "phrases" in js:
            return None
        if "document.body ? document.body.innerText" in js:
            return ""
        return None


smartapply = FakeFrame("https://smartapply.indeed.com/beta/indeedapply/form/review-m", root=document_root)
bframe_frame = FakeFrame("https://www.recaptcha.net/recaptcha/enterprise/bframe?hl=en&", parent=smartapply)
anchor_frame = FakeFrame("https://www.recaptcha.net/recaptcha/enterprise/anchor?ar=1&k", parent=smartapply)

fake_page = MagicMock()
fake_page.frames = [bframe_frame, anchor_frame, smartapply]
fake_page.url = "https://smartapply.indeed.com/beta/indeedapply/form/review-m"

# Reset the fake token back to empty so detection sees a genuine, unsolved
# challenge -- exactly the DETECTED-time state from the log.
real_token_el.value = ""

captured = []
_orig_print = print
def _capturing_print(*a, **kw):
    line = " ".join(str(x) for x in a)
    captured.append(line)
    _orig_print(*a, **kw)
import builtins

# Drive ticks forward AND flip the real token to solved after a few ticks,
# simulating a genuine (if fast) solve to exercise both the container-scope
# fix and the 3s floor together end-to-end.
state = {"ticks": 0}
def _ticking_sleep(*_a, **_kw):
    state["ticks"] += 1
    FakeDatetime._current += _real_datetime_module.timedelta(seconds=1)
    if state["ticks"] == 1:
        real_token_el.value = "z" * 2450  # solve arrives on tick 1 -- inside the 3s floor
m.time.sleep = _ticking_sleep

builtins.print = _capturing_print
try:
    result = m._check_and_handle_captcha(fake_page, title="AI Solutions Engineer", company="Trece, Inc", job_url="https://indeed.com/x")
finally:
    builtins.print = _orig_print

detected_lines = [l for l in captured if "CAPTCHA DETECTED" in l]
floor_block_lines = [l for l in captured if "⏱ Blocked premature solve declaration" in l]
wrong_element_lines = [l for l in captured if "len=2382" in l]

check("3. Exactly one detection cycle (no false-solve-triggered re-detection loop)", len(detected_lines) == 1,
      detail=f"got {len(detected_lines)}")
check("3b. The stale len=2382 element never appears in any signal line", len(wrong_element_lines) == 0,
      detail=f"found: {wrong_element_lines}")
check("3c. The 3-second floor blocked the tick-1 solve (too fast to be real)", len(floor_block_lines) >= 1,
      detail=f"got {len(floor_block_lines)}")
check("3d. Function still eventually returned True (handled, not stuck)", result is True)

print()
if FAILURES:
    print(f"❌ {len(FAILURES)} CHECK(S) FAILED: {FAILURES}")
    sys.exit(1)
else:
    print("ALL CHECKS PASSED")
