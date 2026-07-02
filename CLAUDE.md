# Job Pipeline — Project Rules for Claude

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
