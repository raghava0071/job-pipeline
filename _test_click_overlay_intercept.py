#!/usr/bin/env python3
# =============================================================================
# _TEST_CLICK_OVERLAY_INTERCEPT.PY — proves the generic overlay-click fix in
# _click() (workday_apply_now.py) against the ACTUAL captured evidence from
# submit_debug_boeing_20260714_171429.json and submit_debug_relx_20260714_171523.json.
# Kept on disk per established precedent.
#
# Method: extracts the literal JS function body shipped inside _click()'s
# page.evaluate() call (not a paraphrase), runs it for real under Node with a
# minimal fake DOM built directly from each JSON file's own rect + elementAtPoint
# values, and confirms:
#   1. Both real cases are detected as `covered: true`.
#   2. The dispatchEvent call lands on the INTERCEPTING element (the DIV),
#      not the underlying button — the actual fix behavior, not the old
#      blind fallback.
#   3. This works despite the two companies' intercepting elements having
#      completely different class names (css-1n9xe37 vs css-1acu6fs) — no
#      selector/class/attribute is hardcoded anywhere in the checked logic.
# =============================================================================

import json
import re
import subprocess
import tempfile
from pathlib import Path

SRC_FILE = Path(__file__).parent / "workday_apply_now.py"
CRASH_LOGS = Path(__file__).parent / "data" / "crash_logs"


def _extract_click_js() -> str:
    """Pull the literal JS function body out of _click()'s page.evaluate()
    call in the real source — not a rewritten copy — so this test breaks
    loudly if the shipped logic ever changes without the test being updated."""
    text = SRC_FILE.read_text()
    start = text.index('"""(s) => {')
    start += len('"""')
    end = text.index('}"""', start) + 1   # include the closing brace
    return text[start:end]


def _parse_element_at_point(s: str) -> tuple[str, str]:
    """Mirror _diagnose_button()'s own string format: TAG(#id)?(.class)?
    Returns (tag, class) — good enough for these two real captures, neither
    of which has an id component."""
    m = re.match(r"^([A-Z]+)(?:#([^.]+))?(?:\.(.+))?$", s)
    assert m, f"couldn't parse elementAtPoint string: {s!r}"
    tag, _id, cls = m.groups()
    return tag, (cls or "").replace(".", " ")


def _run_against_json(json_path: Path) -> dict:
    data = json.loads(json_path.read_text())
    diag = data["button_diagnosis"]
    assert diag["coveredBySomethingElse"] is True, (
        f"{json_path.name}: expected coveredBySomethingElse=true in the real "
        f"captured data — test setup assumption violated"
    )
    rect = diag["rect"]
    top_tag, top_class = _parse_element_at_point(diag["elementAtPoint"])

    click_js = _extract_click_js()

    # Minimal fake DOM: a button matching the JSON's own rect, and a
    # covering element matching the JSON's own elementAtPoint tag/class.
    # elementFromPoint always returns the covering element (matches the
    # real, confirmed state: something sits on top at that exact point).
    harness = f"""
    const EVENTS = [];
    function makeEl(tag, cls) {{
        return {{
            tagName: tag,
            id: "",
            className: cls,
            _label: tag + (cls ? "." + cls.replace(/ /g, ".") : ""),
            getBoundingClientRect() {{
                return {{ left: {rect["x"]}, top: {rect["y"]}, width: {rect["w"]}, height: {rect["h"]} }};
            }},
            removeAttribute() {{}},
            dispatchEvent(ev) {{ EVENTS.push(this._label); return true; }},
            contains(other) {{ return other === this; }},
        }};
    }}
    const button = makeEl("BUTTON", "");
    button.id = "createAccountSubmitButton";
    const overlay = makeEl({json.dumps(top_tag)}, {json.dumps(top_class)});

    global.document = {{
        querySelector(sel) {{ return button; }},
        elementFromPoint(cx, cy) {{ return overlay; }},
    }};
    global.MouseEvent = function(type, opts) {{ this.type = type; }};

    const fn = {click_js};
    const result = fn("button[data-automation-id='createAccountSubmitButton']");
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


def test_boeing():
    r = _run_against_json(CRASH_LOGS / "submit_debug_boeing_20260714_171429.json")
    assert r["result"]["covered"] is True
    assert r["result"]["clicked"] is True
    button_label = "BUTTON"
    overlay_label = r["top_tag"] + ("." + r["top_class"].replace(" ", ".") if r["top_class"] else "")
    assert overlay_label in r["events"], f"overlay was never clicked: {r}"
    assert button_label not in r["events"], f"button got clicked instead of the overlay: {r}"
    print(f"✅ PASS (boeing): covered=True, dispatchEvent landed on '{overlay_label}' "
          f"(css-1n9xe37), NOT the button — matches confirmed live capture")


def test_relx():
    r = _run_against_json(CRASH_LOGS / "submit_debug_relx_20260714_171523.json")
    assert r["result"]["covered"] is True
    assert r["result"]["clicked"] is True
    button_label = "BUTTON"
    overlay_label = r["top_tag"] + ("." + r["top_class"].replace(" ", ".") if r["top_class"] else "")
    assert overlay_label in r["events"], f"overlay was never clicked: {r}"
    assert button_label not in r["events"], f"button got clicked instead of the overlay: {r}"
    print(f"✅ PASS (relx): covered=True, dispatchEvent landed on '{overlay_label}' "
          f"(css-1acu6fs — a DIFFERENT class than boeing's, proving nothing is "
          f"hardcoded), NOT the button — matches confirmed live capture")


if __name__ == "__main__":
    test_boeing()
    test_relx()
    print("\n✅ ALL CHECKS PASSED — the real, shipped _click() JS correctly "
          "clicks the intercepting element (not the button) for both real "
          "captured cases, using two different overlay class names.")
