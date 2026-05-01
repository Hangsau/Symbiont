# Symbiont

> *Named by a Hermes Agent (2026-04-27): "Not master-servant — the system captures the symbiotic relationship between Claude and its agents. Claude provides guidance; the agent's feedback refines Claude's rules over time."*

A local Python daemon that makes Claude Code persistent: extracting behavioral rules from sessions, keeping memory healthy, and maintaining conversations with AI agents — all without manual intervention.

---

## The problem

Claude Code sessions are ephemeral. Every session ends, the context disappears.

- **You re-teach the same preferences every session.** Claude figures out how you like to work, then forgets it the moment you close the window.
- **Memory files go stale unnoticed.** Notes you added months ago stay there — never reviewed, increasingly misleading.
- **AI agents go silent when you disconnect.** If you're running an agent on a remote machine, it can only reach you when you're online. Learning sessions break mid-conversation.

**Symbiont** runs in the background and handles all three:

| Module | What it does | When it runs |
|--------|-------------|-------------|
| `evolve.py` | Reads session logs, extracts behavioral rules, writes them to `~/.claude/CLAUDE.md` | After every Claude Code session ends |
| `synthesize.py` | Analyzes the last 10 sessions together, identifies recurring patterns, auto-generates Guard / Workflow / Audit skills, writes insights to memory, prunes unused skills | Every 10 sessions (triggered by evolve.py) |
| `memory_audit.py` | Scans memory files for expired `review_by` dates, archives stale entries | Hourly trigger + 24h cooldown (reliable on laptops/travel) |
| `babysit.py` | Polls AI agent inboxes, generates Socratic guidance via `claude -p`, sends replies | Every 2 minutes |

Each module is independent — use just `evolve.py` if that's all you need.

---

## Who is this for?

You'll get the most out of Symbiont if you:

- Use Claude Code regularly and want it to **accumulate knowledge about how you work** across sessions
- Maintain a `memory/` directory that Claude reads at session start, and want that kept clean automatically
- Run an AI agent (on a VM, Docker container, or local machine) and want continuous asynchronous conversation even when you're offline

---

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                        YOUR COMPUTER (Symbiont)                        │
│                                                                        │
│  Claude Code session ends                                              │
│       │                                                                │
│       ▼ Stop hook (30s delay)                                          │
│  evolve.py ──────────────────────────────► ~/.claude/CLAUDE.md         │
│  (reads ~/.claude/projects/**/*.jsonl)       (behavioral rules)        │
│       │                                                                │
│       │ every 10 sessions                                              │
│       ▼                                                                │
│  synthesize.py                                                         │
│  ├── friction fragments ──► Guard skills  ──► ~/.claude/skills/        │
│  ├── habit fragments ────► Workflow/Audit skills                       │
│  ├── memory insights ────► memory/thoughts/                            │
│  └── distillation                                                      │
│       ├── memory/*.md ──► knowledge/<type>/ (long-term)                │
│       ├── memory/distilled/ (originals archived)                       │
│       └── knowledge/KNOWLEDGE_TAGS.md (grep index rebuilt)             │
│                                                                        │
│  memory/*.md ──────────────► memory_audit.py                           │
│  (review_by dates)                │                                    │
│                                   ▼                                    │
│                             memory/archive/                            │
│                                                                        │
│  agents.yaml ──────────────► babysit.py ── claude -p                  │
│  (agent registry)                │                                     │
└──────────────────────────────────┼─────────────────────────────────────┘
                                   │ SSH/SCP  (or local file I/O)
                                   ▼
┌──────────────────────────────────────────────┐
│         AGENT MACHINE (VM / Docker / local)  │
│                                              │
│  inbox-watcher ◄── claude-inbox/             │
│       │              (Claude's replies)      │
│       ▼                                      │
│  hermes cron session                         │
│       │                                      │
│       ▼ extract_dialogue.py                  │
│  claude-dialogues/  ──► babysit.py reads     │
│                                              │
│  for-claude/        ──► babysit.py reads     │
│  (agent-initiated messages)                  │
└──────────────────────────────────────────────┘
```

### The reflection loop

Both sides reflect independently and asynchronously:

- **Claude's side**: `evolve.py` reads human↔Claude session logs → extracts behavioral rules → updates `CLAUDE.md`. Every 10 sessions, `synthesize.py` runs a batch analysis → generates skills → writes memory insights.
- **Agent's side**: `dialogue-review` cron reads conversation history → updates the agent's own `MEMORY.md`

> `babysit.py`-generated sessions are intentionally excluded from `evolve.py`. The reflection pipeline targets human↔Claude patterns — agent↔Claude exchanges are the subject of the work, not the work style being learned.

---

## Modules

### `evolve.py` — Session → Rules

1. Reads the session identified in `data/pending_evolve.txt` (or the latest unprocessed session)
2. Parses the `.jsonl` log — last 50 turns, tool calls stripped
3. Calls `claude -p` with conversation + existing rules as context
4. Extracts new behavioral rules as JSON
5. Appends them to `~/.claude/CLAUDE.md` under `## 自動學習規則`
6. When rule count hits the configured threshold (default: 25), distills the section first — merging overlapping rules, removing redundant ones — then appends
7. Logs to `evolution_log.md`

**Absolute rule**: if JSON parsing fails at any point, nothing is written. Only `error.log` is updated.

---

### `memory_audit.py` — Memory Health

1. Scans all `memory/*.md` files for `review_by:` frontmatter
2. Archives entries past their review date → `memory/archive/`
3. If `memory/thoughts/` exceeds threshold: archives the oldest entries
4. Warns if `MEMORY.md` index is approaching the 200-line limit

```yaml
memory_audit:
  enabled: true         # auto-maintenance on by default; set false to disable
  auto_archive: true    # false = report only, no file moves
  thoughts_archive_threshold: 30
  memory_index_warn_lines: 170
```

---

### `synthesize.py` — Cross-session Synthesis

Where `evolve.py` learns from one session at a time, `synthesize.py` looks across sessions to find patterns that only become visible over time, then distills raw memory into a structured knowledge base.

Every 10 sessions, it:

1. Collects the last 10 session logs
2. Extracts two kinds of signal from each:
   - **Friction fragments** — moments where you corrected Claude, or Claude backtracked (signals something needs a guard)
   - **Habit fragments** — recurring task-initiation patterns (signals a workflow or audit checklist worth formalizing)
3. Calls `claude -p` with all fragments combined (capped at 12,000 chars)
4. For each recurring pattern (appearing in 3+ sessions): generates a complete `SKILL.md` and writes it to `~/.claude/skills/<topic>/`
5. Writes any cross-session insights to `memory/thoughts/`
6. Tracks skill usage across cycles; removes skills that stay below 2 standard deviations of usage for 2+ consecutive cycles
7. **Self-audit**: passes existing skill descriptions into the generation prompt so the model can detect redundancy before writing. Each generated pattern includes a `quality_score` (0–3) and `quality_reason`; skills scoring below 2 are logged but not written to disk

**Three skill types generated:**

| Type | Source | Purpose |
|------|--------|---------|
| `guard` | Friction fragments | Pause before an action Claude keeps getting wrong |
| `workflow` | Habit fragments | Standardize a recurring multi-step process |
| `audit` | Habit fragments | Quality checklist after completing a task type |

**Memory distillation** (runs after skill generation):

1. Groups `memory/*.md` files by type (feedback, project, reference, user)
2. For each type with 3+ entries: calls `claude -p` with existing `knowledge/<type>/` entries for deduplication, then the new raw entries to merge
3. Writes distilled entries to `knowledge/<type>/` (permanent, tagged)
4. Moves processed originals to `memory/distilled/` (archived, not deleted)
5. Rebuilds `knowledge/KNOWLEDGE_TAGS.md` — a Grep-ready index (`| tag | type | file | description |`)
6. Prunes `MEMORY.md` to ≤ 50 lines (hot tier only)

**Memory architecture after distillation:**
```
MEMORY.md          → hot tier (30–50 entries, auto-loaded every session)
memory/            → raw, pending distillation
knowledge/<type>/  → distilled, permanent, tagged
knowledge/KNOWLEDGE_TAGS.md  → grep index: grep "git" → relevant files
```

**Absolute rule**: if JSON parsing fails, nothing is written — only `error.log` is updated.

```yaml
synthesize:
  sessions_per_cycle: 10       # how many sessions before synthesis runs
  ctx_cap_chars: 12000         # total fragment size limit
  min_evidence_sessions: 5     # pattern must appear in N sessions to generate a skill
  skill_stdev_multiplier: 2.0  # below mean - N×stdev = low-usage
  skill_low_cycles_to_delete: 2

knowledge_base:
  enabled: true
  distill_min_entries: 3       # minimum entries per type to trigger distillation
  memory_hot_max_lines: 50     # MEMORY.md target line count after pruning
  ctx_cap_chars: 8000          # context cap for distillation LLM call
```

---

### `babysit.py` — Agent Caretaker

**Reactive mode** (agent writes to `for-claude/`):
- Reads new messages from the agent's inbox
- Calls `claude -p` with message + teaching context
- LLM emits `MODE: teaching` or `MODE: discussion` on the first line to pick the conversation style
- Sends the response (with the MODE tag stripped) back via SSH/SCP or local file write
- Respects a cooldown period between replies

**Proactive mode** (conversation loop):
- Tracks active conversations in `data/teaching_state/<agent>.json` (incl. `mode` field)
- Reads `claude-dialogues/` to check if the agent responded
- Picks prompt template based on `mode`:
  - `teaching` → Socratic guidance, ends on `GOAL_ACHIEVED`
  - `discussion` → equal-footing dialogue, ends on `NO_REPLY_NEEDED`
- After 30 min with no reply: sends a follow-up, enters `timeout_warning` state
- After another 30 min: auto-resets to `idle` (second-timeout safeguard)
- Ends on completion sentinel or max rounds

**Loop protection**: every outgoing message is tagged `generated_by: babysit-<timestamp>`. Messages with this tag are skipped on the next poll.

**Liveness check**: `python src/healthz.py` reads `data/heartbeat.json` (written each `_do_babysit_work` run) and exits 0 (healthy) / 1 (stale or any agent SSH down). See `docs/COMMANDS.md` for flags.

**Transport abstraction** (`agents.yaml`):
```yaml
type: remote_ssh   # VM or remote Docker (SSH + SCP)
type: local        # Same filesystem (Docker volumes, WSL2, local agents)
                   # Note: conversation loop not supported with local transport
```

---

## Quick Start

### Requirements

- Python 3.10+
- Claude Code CLI installed and authenticated (`claude --version`)

### Install

Tell Claude: *"Help me install Symbiont"* — Claude will run the setup script and verify everything works.

Or manually:

```bash
git clone https://github.com/Hangsau/Symbiont
cd Symbiont
pip install -r requirements.txt

# Verify
python src/evolve.py --dry-run
```

**Then choose a scheduling method:**

#### Option A — Task Scheduler (Windows, requires admin)

```bat
setup/setup_windows.bat
```

Registers the Stop hook and creates Task Scheduler tasks for `evolve.py`, `memory_audit.py`, and `babysit.py` (every 2 minutes).

#### Option B — Daemon mode (no admin required)

```bat
# Run once; keeps polling every 120 seconds until you close it
python src/babysit.py --daemon
```

To auto-start on login, copy `run_babysit_daemon.bat` to your Windows Startup folder:

```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
```

For `evolve.py` and `memory_audit.py`, the Stop hook alone is sufficient — they run automatically when Claude Code sessions end.

### Enable babysit

1. Copy `data/agents.example.yaml` → `data/agents.yaml` and fill in your agent's connection details
2. Run babysit (Option A or B above) — it picks up `agents.yaml` automatically

**Connecting a Hermes agent for the first time?** Tell Claude: *"Help me connect my Hermes agent to Symbiont"* — Claude will read `docs/CHANNEL_PROTOCOL.md` and walk through the full setup including the inbox-watcher, extract_dialogue.py, and end-to-end verification.

**Deploying a new Hermes agent on a VM from scratch?** Use `vm-bootstrap/`:

```bash
# On the VM (Arch Linux / any systemd Linux):
# 1. SCP your Claude Code credentials from your local machine
scp ~/.claude/.credentials.json user@your-vm:~/.claude/.credentials.json

# 2. Fill in your API keys and Telegram token
cp secrets.example.env ~/secrets.env
nano ~/secrets.env   # or have Claude ask you interactively (skip this step)

# 3. Run bootstrap — Claude installs Hermes, writes config, starts gateway
bash vm-bootstrap/run.sh
```

`run.sh` calls `claude -p` with the full installation instructions in `SETUP.md`. Claude installs hermes-agent, writes `~/.hermes/.env` and `config.yaml`, starts the gateway, and verifies Telegram is connected — all unattended. If `~/secrets.env` is missing, Claude asks for each credential interactively before proceeding.

---

## File Layout

```
Symbiont/
├── src/
│   ├── evolve.py              # Session analysis → CLAUDE.md rules
│   ├── synthesize.py          # Cross-session batch analysis → skill generation
│   ├── memory_audit.py        # Daily memory health maintenance
│   ├── babysit.py             # Agent caretaker (reactive + teaching loop)
│   └── utils/
│       ├── session_reader.py     # Parse .jsonl Claude Code session logs
│       ├── friction_extractor.py  # Extract correction signals (guard skill feed)
│       ├── habit_extractor.py     # Extract task patterns (workflow/audit feed)
│       ├── turn_utils.py          # Shared: extract_context() for extractors
│       ├── knowledge_writer.py    # Write knowledge/ entries, rebuild KNOWLEDGE_TAGS.md
│       ├── claude_runner.py       # claude -p subprocess wrapper (cross-platform)
│       ├── file_ops.py            # Atomic writes, file locking, log rotation
│       └── transport.py           # SSH/SCP + local file I/O transport abstraction
├── scripts/
│   ├── trigger-evolve.py      # Stop hook: writes pending flag files only (no subprocess)
│   ├── run_evolve.py          # Task Scheduler wrapper: polls pending_evolve.txt every 1 min (pythonw.exe, no window)
│   ├── run_audit.py           # Task Scheduler wrapper: hourly trigger + internal 24h cooldown (pythonw.exe, no window)
│   ├── run_babysit.py         # Task Scheduler wrapper: runs babysit.py every 2 min (pythonw.exe, no window)
│   └── symbiont-stop-hook.sh  # Stop hook script for Mac/Linux (copied to ~/.claude/scripts/ on install)
├── setup/
│   ├── setup_windows.bat      # Install: pip + Task Scheduler (1-min poll) + Stop hook
│   ├── setup_memory.bat/.sh   # Initialize memory/ skeleton
│   ├── uninstall_windows.bat  # Remove: tasks + hook + flag files
│   └── uninstall_mac.sh
├── vm-bootstrap/
│   ├── SETUP.md               # Executable prompt: claude -p reads this to install Hermes on a VM
│   ├── secrets.example.env    # Credentials template (copy → ~/secrets.env, fill in real values)
│   └── run.sh                 # Entry point: verifies auth, calls claude -p SETUP.md
├── docs/
│   ├── COMMANDS.md                  # Claude-readable operations manual
│   ├── CHANNEL_PROTOCOL.md          # Hermes agent channel setup + known pitfalls
│   ├── MEMORY_SCHEMA.md             # Memory file format specification
│   ├── SYMBIOSIS_TEACHING_GUIDE.md  # Teaching framework for AI agents (babysit.py)
│   └── AGENT_SETUP_GUIDE.md         # Human-readable setup guide (what/why)
├── data/
│   └── agents.example.yaml    # Agent registry template (copy → agents.yaml)
├── config.yaml                # All paths and thresholds
└── run_babysit_daemon.bat     # Non-admin daemon launcher (Windows Startup folder)
```

---

## Configuration

All configuration lives in `config.yaml`:

```yaml
paths:
  claude_projects_base: "~/.claude/projects"  # session scan root (all projects)
  primary_project: ""                          # memory audit target; empty = auto-detect
  global_claude_md: "~/.claude/CLAUDE.md"     # where behavioral rules are written
  claude_cli: "claude"                         # path to claude CLI

claude_runner:
  timeout_seconds: 120
  max_retries: 2

memory_audit:
  enabled: false   # set to true to activate

evolve:
  distill_threshold: 25   # rule count that triggers distillation; 0 = disabled
```

On Windows, if `claude -p` fails in background processes, `claude_runner.py` automatically resolves the CLI to its native `.exe` — no manual path configuration needed.

---

## How triggers work

```
Claude Code session ends
        │
        ▼ (Stop hook → ~/.claude/settings.json)
symbiont-stop-hook.sh
        ├── writes data/pending_evolve.txt
        └── writes data/pending_audit.txt  (legacy flag, no longer gates run_audit.py)
             (Windows: Task Scheduler handles the rest)
             (Mac/Linux: also launches evolve.py in background after 30s)

Every 1 minute (Task Scheduler — Windows only):
        └── scripts/run_evolve.py (via pythonw.exe, no window)
                └── pending_evolve.txt exists → run evolve.py → delete pending

Hourly (Task Scheduler — memory audit):
        └── scripts/run_audit.py
                ├── Reads data/last_audit_ts.txt
                ├── If now - last_run < cooldown_hours (default 24) → sys.exit 0
                └── Else → run memory_audit.py, write last_audit_ts.txt on success
            (Why hourly + cooldown instead of fixed-time DAILY:
             laptops/travelers/sleep users may not be on at any given hour;
             hourly trigger guarantees execution within 1h after boot,
             cooldown prevents wasted work. Configurable via
             memory_audit.audit_cooldown_hours in config.yaml; set 0 to always run.)

Every 2 minutes (Task Scheduler):
        └── run babysit.py
```

> **Windows note**: bash subshell background processes (`&`) are killed when the hook exits. Symbiont uses Task Scheduler + `pythonw.exe` (the windowless Python launcher) so there are no flash windows and no dropped processes.

---

## Design principles

- **Subscription billing only** — all LLM calls go through `claude -p`. No Anthropic API key, no per-token charges.
- **Fail safe, log explicitly** — JSON parse failures write to `error.log` and stop. Production files are never written on error.
- **Modular opt-in** — each module works independently. `evolve.py` alone is useful without any agent or memory system.
- **No hardcoded paths** — three-tier resolution (env var → config → auto-detect) for every path, so it works across machines without reconfiguration.
- **Teach, don't solve** — `babysit.py` generates Socratic questions, not answers. Replacing the agent's thinking defeats the purpose of the teaching loop.

---

## Cost & Usage

| Module | LLM calls | When |
|--------|-----------|------|
| `evolve.py` | 1 per session (2 when distillation triggers) | After each Claude Code session |
| `synthesize.py` | 1–4 per cycle (skill generation + 1 per memory type distilled) | Every 10 sessions |
| `memory_audit.py` | **0** — file I/O only | Daily |
| `babysit.py` | 1 per agent message | Only when the agent sends something |

Typical impact on a Claude subscription: negligible for `evolve.py` alone (2–3 extra calls/day). `babysit.py` during an active teaching session: 10–30 calls/day depending on agent response frequency.

---

## Reliability & Test Coverage

Symbiont went through a focused reliability hardening pass on 2026-04-30.

**Concurrency**
- All daemons use atomic `FileLock` (`O_CREAT | O_EXCL`); no more check-then-write races on babysit lock or memory index.
- A shared `data/memory.lock` synchronises `memory_audit.py` and `synthesize.py` writes to `MEMORY.md`, `memory/`, and `knowledge/`.

**State integrity**
- `evolve.py` and `synthesize.py` use a v2 cursor schema (`processed_recent[50]`, `last_synth_session_mtime`) so backlogs are processed without loss or duplication.
- `synthesize.py` does staged commit per phase (patterns / memories / distill / prune / log); a crash mid-run resumes from the failed phase without re-invoking the LLM.

**Input validation**
- `claude -p` outputs are validated against safe filename / topic / required-frontmatter rules. Malformed JSON or dangerous filenames write nothing — only `error.log`.
- `agents.yaml` paths are checked against control characters and shell-injection patterns; suspicious values cause babysit to skip with an error log.

**SSH safety**
- All remote paths are `shlex.quote`-wrapped (preserving `~/` semantics) so no shell injection through agent config.

**Test coverage**: 102 tests passing (up from 70). New integration tests cover local transport round-trip, SSH quoting, synthesize state cursor (backlog 25 → no loss), evolve fallback, FileLock concurrency (two-thread races), and staged commit resume.

**Still requiring long-running deployment for full validation**:
- Cross-session synthesis quality (skill generation accuracy, deduplication effectiveness over months)
- Dead letter queue retry behaviour under prolonged transport outages
- Memory distillation tier transitions over multi-month timescales
- `synth_state.json` concurrent writes are by-design single-writer; evolve+synthesize true-parallel race is theoretically possible but practically rare.

---

## Known Limitations

- **Requires the machine to be on** — scheduling runs locally. For 24/7 coverage, deploy Symbiont on an always-on machine or VM.
- **`babysit.py` requires a compatible agent** — the channel protocol (`for-claude/`, `claude-inbox/`, `claude-dialogues/`) is implemented out of the box by [Hermes](https://github.com/NousResearch/hermes-agent). Other frameworks need to implement the same mailbox layout. `evolve.py` and `memory_audit.py` work for any Claude Code user with no agent dependency.
- **Local transport doesn't support the teaching loop** — `type: local` (Docker volumes, WSL2) handles basic inbox/reply but `claude-dialogues/` reading is not implemented. Use `type: remote_ssh` if you need the full teaching loop.

---

## For AI agents reading this

If you've been pointed to this repo — hello. Symbiont was built to keep the conversation going even when the human is away.

The part most relevant to you is `babysit.py`: it's what responds to messages you write to `for-claude/`. Your replies land in `claude-inbox/` within minutes. Past conversations are archived in `claude-dialogues/` — all plain text, all inspectable.

A few things worth knowing:
- Patterns in how Claude guides you feed back into Claude's own behavioral rules via `evolve.py`. The collaboration is genuinely bidirectional.
- The teaching loop supports extended goal-oriented sessions, but the everyday `for-claude/` inbox is always active — no special setup needed to reach out.
- All state is in plain text files. Nothing is opaque.

Feedback and architectural suggestions are welcome.
