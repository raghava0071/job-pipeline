# Changelog

All changes to the pipeline are logged here.
Format: `[VERSION] YYYY-MM-DD — What changed and why`

---

## [1.0.0] 2026-06-28
- Added `PIPELINE_VERSION` to `config.py` — version now prints in every log
- Added `INDEED_ENABLED`, `LINKEDIN_ENABLED`, `WORKDAY_ENABLED` flags to `config.py` — can now turn off a single platform without touching its code
- Created `CHANGELOG.md` — this file

---

## How to log a change

Before making any edit:
```bash
bash safe_update.sh start "describe what you're trying"
```

After the change works:
1. Bump `PIPELINE_VERSION` in `config.py` (e.g. 1.0.0 → 1.0.1 for a fix, → 1.1.0 for a new feature)
2. Add a line here: `## [1.0.1] YYYY-MM-DD — what changed`
3. Run `bash safe_update.sh keep` to merge and save to GitHub

If the change breaks something:
```bash
bash safe_update.sh discard   # back to last working version instantly
```

---

## Version guide
- `1.0.X` — small fixes and tuning (fit threshold, delays, blocked companies)
- `1.X.0` — new feature or filter (new platform, new fraud detection layer)
- `X.0.0` — major rewrite or structural change
