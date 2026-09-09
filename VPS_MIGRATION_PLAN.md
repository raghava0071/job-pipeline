> **STATUS (2026-09-09): declined, on hold.** Raghav decided to keep running on the Mac (carrying the
> laptop, keeping it awake for launchd) rather than move to a VPS. Nothing below was executed. Kept on
> disk in case this gets revisited later — not an active plan right now.

# Runtime migration: Mac/launchd → VPS (roadmap task #6)

Goal: the pipeline keeps running (Greenhouse now, Lever/Ashby/Workday later) without your laptop needing
to be awake. This is a plan to execute, not something already done — provisioning a server means picking
a provider and paying for it, which is your call, not mine.

## 1. Pick a provider

Either is fine; pick on price vs. familiarity.

- **Hetzner CX22** — ~€4.35/month (~$5), 2 vCPU / 4GB RAM / 40GB disk. Cheapest that's still comfortable
  for one Chromium instance. EU-based company (Falkenstein/Helsinki/Ashburn VA datacenters available).
- **DigitalOcean Basic Droplet** — $6/month, 1 vCPU / 1GB RAM (bump to the $12/mo 2GB tier — 1GB is tight
  once Chromium + Playwright + Python are all resident at once). More beginner-friendly docs/UI if you've
  never run a VPS before.

Recommendation: DigitalOcean's $12/mo 2GB droplet if this is your first VPS (better docs, easier
recovery if you lock yourself out); Hetzner CX22 if you're comfortable with SSH already and want it
cheaper. Ubuntu 22.04 LTS on either.

## 2. What moves, and how

Never move secrets through git — `.env`, `data/*.enc`, and the session directories are exactly what
`.gitignore` already excludes for a reason.

- **Code** (`*.py`, `*.sh`, configs): `git clone` your GitHub remote onto the VPS (this is what
  `snapshot.sh --push` is for — make sure your latest commits are pushed first).
- **Secrets** (`.env`, `data/workday_accounts.enc`): `scp` directly from your Mac to the VPS over SSH.
  `PIPELINE_SECRET` inside `.env` MUST come along — without it, `secure_store.py` can't decrypt anything
  it already encrypted, and every saved account becomes unreadable.
- **Session state** (`.greenhouse_session/`, `.workday_session/`): these are Chrome's own persistent
  profile dirs (cookies, local storage). `scp -r` them over too, so the VPS doesn't start from a cold,
  logged-out browser. If they don't come over cleanly, it's not fatal — the pipeline just re-does any
  one-time logins on its first VPS run.
- **Data/logs** (`data/*.json`): copy over once at cutover so `already_applied()`'s dedup history isn't
  reset to zero (that would risk re-applying to companies you already hit). After that, the VPS's copies
  become the live ones.

## 3. Server setup

```bash
sudo apt update && sudo apt install -y python3-pip python3-venv xvfb git
git clone <your-repo-url> job_pipeline && cd job_pipeline
pip install -r requirements.txt --break-system-packages
python3 -m playwright install --with-deps chromium
```

**On `channel="chrome"` (real Google Chrome, tier 1 in `main()`'s browser launch):** a bare VPS won't
have Google Chrome installed, only Playwright's bundled Chromium. That's fine — the code already falls
back to bundled Chromium (tier 3) automatically when the `chrome` channel launch fails; no code change
needed. If you want real Chrome anyway for parity, `apt install google-chrome-stable` (via Google's apt
repo) works the same as on the Mac.

**On `headless=False` needing a screen:** the code deliberately launches non-headless real/bundled Chrome
("no automation-hiding flags... for launch stability, not evasion" — see the comments in
`greenhouse_apply_now.py`/`workday_apply_now.py`). A VPS has no physical display, so that launch would
fail outright without one. `Xvfb` (a virtual framebuffer) fixes this without touching any pipeline code —
wrap the run command:

```bash
xvfb-run --auto-servernum --server-args="-screen 0 1366x900x24" python3 greenhouse_apply_now.py --live --limit 5
```

## 4. Scheduling: launchd → systemd timers

launchd is macOS-only; the Linux equivalent with the closest feature set (retries, logging, "ran even if
missed") is a systemd timer, not plain cron. One unit + one timer per run slot (8AM/12PM/6PM, matching
your existing schedule):

```ini
# /etc/systemd/system/greenhouse-morning.service
[Unit]
Description=Greenhouse morning run

[Service]
Type=oneshot
WorkingDirectory=/home/<user>/job_pipeline
ExecStart=/usr/bin/xvfb-run --auto-servernum python3 greenhouse_apply_now.py --greenhouse-only --live --limit 5
```
```ini
# /etc/systemd/system/greenhouse-morning.timer
[Timer]
OnCalendar=*-*-* 08:00:00
Persistent=true

[Install]
WantedBy=timers.target
```
Repeat for the 12PM/6PM slots, then `systemctl enable --now greenhouse-morning.timer` (and the other two).

## 5. One thing that'll surprise you: Gmail from a new IP

The first time `mail_reader.py` connects via IMAP from the VPS's IP, Google may flag it as a new
sign-in and email/prompt a security check on the app password. Not a blocker — approve it once from
your phone/Mac when it happens, same as setting up a new device normally.

## 6. Cutover — don't do it cold

1. Get the VPS running `--dry-run` in parallel with the Mac still doing the real `--live` runs, for a
   few days. Compare its printed output against what the Mac would have done on the same postings.
2. Once you trust it, flip the Mac's launchd `plist`s off (`launchctl unload ...`) and enable the VPS
   systemd timers for real (`--live`).
3. Keep the Mac able to run manually as a fallback for a couple weeks before you stop thinking about it.

## Cost

~$5–12/month, ongoing, on your card — the one recurring cost this whole project doesn't have today.
