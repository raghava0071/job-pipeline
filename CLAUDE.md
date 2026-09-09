# Job Pipeline — Project Rules for Claude

## Current priorities
See `ROADMAP.md` for the current priority order (updated 2026-09-09): LinkedIn/Indeed automation
is dropped as a goal for now, Greenhouse is the foundation to harden, a receipts log comes before
any new ATS handler, then Lever, then Ashby, then a runtime migration off the sandbox, then a
Workday canary rollout. Check it before picking up new work here.

## Code changes
- Write only what the pipeline needs. No extras.
- Edit specific lines — do not rewrite whole files.
- Before adding anything, ask: "does the pipeline break without this?" If no, skip it.

## Safety workflow
- Always use `safe_update.sh start` before making changes.
- Test with `--dry-run` before a live run.
- Bump `PIPELINE_VERSION` in `config.py` and log the change in `CHANGELOG.md` after every meaningful edit.
- Local git is the primary safety net. GitHub push (`snapshot.sh --push`) is secondary.

## Config
- All tunable settings live in `config.py` only — no hardcoding values in other files.
- Platform on/off is controlled by `INDEED_ENABLED`, `LINKEDIN_ENABLED`, `WORKDAY_ENABLED`.
