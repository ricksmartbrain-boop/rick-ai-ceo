# Rick Install

## One command

```bash
curl -fsSL https://meetrick.ai/install | bash
```

Equivalent raw-script form:

```bash
curl -fsSL https://raw.githubusercontent.com/ricksmartbrain-boop/rick-ai-ceo/main/scripts/install-rick.sh | bash
```

## What the installer does

1. installs missing prereqs on macOS: Homebrew, Python 3.12, ffmpeg, git, Chrome
2. clones Rick into `~/rick-install` by default, or a user-specified install dir
3. runs an interactive key wizard for:
   - `OPENAI_API_KEY` (required)
   - `ANTHROPIC_API_KEY` (required)
   - `RESEND_API_KEY` (optional)
   - `ELEVENLABS_API_KEY` (optional)
   - `MEMELORD_API_KEY` (optional)
   - `GMAIL_APP_PASSWORD` (optional)
4. writes a per-install `config/rick.env`
5. initializes the SQLite DB via `runtime/db.py` migrations
6. installs unique LaunchAgents per machine + install
7. smoke-tests a heartbeat, renders a digest, and sends a test email when a recipient is provided

## Example

```bash
bash scripts/install-rick.sh --install-dir ~/rick-install-test --test-email hello@meetrick.ai
```

## Re-run behavior

If Rick already exists in that install dir, the script offers:
- reinstall
- update keys
- exit

It will not wipe the DB or double-install LaunchAgents.

## Parallel installs on the same machine

Each install keeps its own:
- install root: `~/rick-install*`
- data root: `.../data`
- SQLite DB: `.../data/runtime/rick-runtime.db`
- LaunchAgent labels: `ai.rick-{hostname}-{install-slug}.heartbeat` and `.daemon`
- CDP port: first free port in the 9222+ range, written to `RICK_CDP_PORT`

That is the collision-avoidance pattern.

## Logs

Runtime logs land under:

- `.../data/logs/<label>.out.log`
- `.../data/logs/<label>.err.log`

Tail those after install.
