#!/usr/bin/env python3
# =============================================================================
# _TEST_CREATEACCT_OVERLAY_INTERCEPT.PY — proves the v1.9.6 fix inside
# workday_create_account()'s own Method 1 click-exception handler (NOT the
# shared _click() helper, which v1.9.5 already fixed but which this function
# never calls). Kept on disk per established precedent.
#
# Confirmed gap: submit_debug_boeing_20260714_173647.json shows the EXACT
# same coveredBySomethingElse: true signature persisting live AFTER v1.9.5
# shipped, because workday_create_account() has its own bespoke Method 1/2/3
# click escalation that never goes through _click() at all.
#
# Method: extracts the literal JS shipped inside Method 1's `except` block
# (not a paraphrase), runs it under Node against fake DOM state built
# directly from THREE real JSON files: the new
# submit_debug_boeing_20260714_173647.json (the one that proved the gap),
# plus the two originals (boeing 171429, relx 171523) for regression
# coverage — confirming the newly-added code path handles all three the
# same way _click() already does.
# =============================================================================

import json
import re
import subprocess
import tempfile
from pathlib import Path

SRC_FILE = Path(__file__).parent / "workday_apply_now.py"
CRASH_LOGS = Path(__file__).parent / "data" / "crash_logs"


def _extract_overlay_js() -> str:
    """Pull the literal JS out of workday_create_account()'s Method 1
    exception handler — anchored on text unique to THIS block (not
    _click()'s copy, which has different surrounding Python but nearly
    identical JS) so the test fails loudly if this specific code moves or
    is removed."""
    text = SRC_FILE.read_text()
    anchor = 'overlay_diag = submit_btn.evaluate("""'
    start = text.index(anchor) + len(anchor)
    end = text.index('""")', start)   # stop right before the closing triple-quote
    return text[start:end]


def _parse_element_at_point(s: str) -> tuple[str, str]:
    m = re.match(r"^([A-Z]+)(?:#([^.]+))?(?:\.(.+))?$", s)
    assert m, f"couldn't parse elementAtPoint string: {s!r}"
    tag, _id, cls = m.groups()
    return tag, (cls or "").replace(".", " ")


def _run_against_json(json_path: Path) -> dict:
    data = json.loads(json_path.read_text())
    diag = data["button_diagnosis"]
    assert diag["coveredBySomethingElse"] is True, (
        f"{json_path.name}: expected coveredBySomethingElse=true — test setup assumption violated"
    )
    rect = diag["rect"]
    top_tag, top_class = _parse_element_at_point(diag["elementAtPoint"])

    overlay_js = _extract_overlay_js()

    harness = f"""
    const EVENTS = [];
    function makeEl(tag, cls) {{
        return {{
            tagName: tag, id: "", className: cls,
            _label: tag + (cls ? "." + cls.replace(/ /g, ".") : ""),
            getBoundingClientRect() {{
                return {{ left: {rect["x"]}, top: {rect["y"]}, width: {rect["w"]}, height: {rect["h"]} }};
            }},
            dispatchEvent(ev) {{ EVENTS.push(this._label); return true; }},
            contains(other) {{ return other === this; }},
        }};
    }}
    const button = makeEl("BUTTON", "");
    const overlay = makeEl({json.dumps(top_tag)}, {json.dumps(top_class)});
    global.document = {{ elementFromPoint(cx, cy) {{ return overlay; }} }};
    global.MouseEvent = function(type, opts) {{ this.type = type; }};

    // This is exactly what Playwright's Locator.evaluate(js) does: calls
    // the function with the matched element bound to the first arg.
    const fn = {overlay_js};
    const result = fn(button);
    console.log(JSON.stringify({{ result, events: EVENTS }}));
    """

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(harness)
        tmp_path = f.name
    try:
        proc = subprocess.run(["node", tmp_path], capture_output=True, text=True, timeout=10)
    finally:
        Path(tmp_path).unlink()

    assert proc.returncode == 0, f"node harness failed:\nstdout={proc.stdout}\nstderr={proc.stderr}"
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    return {"json_file": json_path.name, "top_tag": top_tag, "top_class": top_class, **out}


def _check(json_name: str):
    r = _run_against_json(CRASH_LOGS / json_name)
    assert r["result"]["covered"] is True, r
    assert r["result"]["clicked"] is True, r
    overlay_label = r["top_tag"] + ("." + r["top_class"].replace(" ", ".") if r["top_class"] else "")
    assert overlay_label in r["events"], f"overlay was never clicked: {r}"
    assert "BUTTON" not in r["events"], f"button got clicked instead of the overlay: {r}"
    print(f"✅ PASS ({json_name}): covered=True, dispatchEvent landed on '{overlay_label}', "
          f"NOT the button — the new workday_create_account() code path now handles this")


def test_boeing_new_capture():
    # The exact capture that PROVED the gap (post-v1.9.5, still failing) —
    # the one that matters most here.
    _check("submit_debug_boeing_20260714_173647.json")


def test_boeing_original_capture():
    _check("submit_debug_boeing_20260714_171429.json")


def test_relx_original_capture():
    _check("submit_debug_relx_20260714_171523.json")


if __name__ == "__main__":
    test_boeing_new_capture()
    test_boeing_original_capture()
    test_relx_original_capture()
    print("\n✅ ALL CHECKS PASSED — workday_create_account()'s own Method 1 "
          "exception handler now correctly clicks the intercepting element "
          "for all three real captured cases, including the one that proved "
          "the v1.9.5 gap.")
