# Symbiont

> *Named by Talos (Hermes Agent, 2026-04-27): "Not master-servant — the system captures the symbiotic relationship between Claude and its agents. Claude provides guidance; the agent's feedback refines Claude's rules over time."*

A local Python daemon that keeps Claude's memory healthy, extracts behavioral rules from sessions, and maintains ongoing conversations with AI agents — all running automatically in the background.

*(Repository name: `local-agent`)*

---

## Who is this for?

You'll get the most out of Symbiont if you:

- Use Claude Code regularly across multiple projects and want it to **get better at working with you over time** — not start from scratch every session
- Maintain a `memory/` directory of notes that Claude reads at the start of sessions, and want that to stay clean without manual upkeep
- Are running or experimenting with an AI agent (on a remote VM, or locally) and want to keep the conversation going even when you're away from your computer

If you only use Claude Code occasionally or don't have a memory system set up, `evolve.py` alone is still useful — it's the lowest-friction module and works independently.

---

## What problem does this solve?

Claude Code sessions are ephemeral. Every session ends, the context is gone. Without persistent infrastructure:

- **Behavioral patterns get lost**: Claude figures out how you like to work mid-session, but that insight disappears the moment you close it. You end up re-teaching the same preferences over and over.
- **Memory files go stale**: Notes and references you added months ago are still there, never reviewed, increasingly misleading.
- **AI agents go silent**: If you're nurturing an agent on a remote machine, it can only reach you when you're online. Extended conversations break whenever you disconnect.

**Symbiont** solves this with three independent modules that run automatically:

| Module | What it does |
|--------|-------------|
| `evolve.py` | Reads the latest session log after every Claude Code session, extracts behavioral rules, and writes them to `~/.claude/CLAUDE.md` |
| `memory_audit.py` | Daily: scans memory files for expired `review_by` dates, archives stale entries, warns when the memory index is getting full |
| `babysit.py` | Every 2 minutes: checks if any AI agents sent messages, generates Socratic guidance responses via `claude -p`, and sends them back automatically |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    YOUR COMPUTER (Symbiont)                       │
│                                                                   │
│  Claude Code sessions  ──Stop hook──►  evolve.py                 │
│  (~/.claude/projects/                  │                          │
│   **/*.jsonl)                          ▼                          │
│                               ~/.claude/CLAUDE.md                 │
│                               (behavioral rules, auto-updated)    │
│                                                                   │
│  memory/*.md  ──────────────► memory_audit.py                    │
│  (review_by dates)             │                                  │
│                                ▼                                  │
│                          memory/archive/                          │
│                          (stale entries moved here)               │
│                                                                   │
│  agents.yaml  ──────────────► babysit.py ──── claude -p ──►      │
│  (agent registry)              │         (generates response)     │
│                                │                                  │
│         ┌──────────────────────┘                                  │
│         │  SSH/SCP (remote) or local file I/O                     │
└─────────┼───────────────────────────────────────────────────────┘
          │
          ▼
┌─────────────────────────────┐
│      REMOTE VM / LOCAL AGENT │
│                              │
│  for-claude/    ◄── Agent sends messages to Claude               │
│  claude-inbox/  ──► Claude's responses arrive here               │
│  claude-dialogues/ ◄─► Conversation archive                      │
│                              │
│  Agent's own reflection:     │
│  dialogue-review cron ──► Agent memory (MEMORY.md)              │
└──────────────────────────────┘
```

### The Reflection Loop

A key architectural property: `claude -p` subprocess calls create `.jsonl` session files under `~/.claude/projects/`. Since `evolve.py` scans all projects recursively, **babysit.py's conversations with agents are automatically included in the rule-extraction pipeline** — no additional wiring needed.

Both sides reflect independently:
- **Claude's side**: `evolve.py` extracts interaction patterns → `CLAUDE.md`
- **Agent's side**: `dialogue-review` cron reads conversation history → agent's `MEMORY.md`

> **Design decision**: `babysit.py` sessions are not treated as reflection targets. Reflection is for human↔Claude interaction patterns. Agent↔Claude interactions are the *subject matter* of the work, not the work style being optimized.

---

## Modules

### `evolve.py` — Session → Rules

Triggered by a Claude Code Stop hook (30s delay) or on startup if a pending session exists.

1. Reads the session identified in `data/pending_evolve.txt` (or finds the latest unprocessed session)
2. Parses the `.jsonl` log (last 50 turns, tool calls stripped)
3. Calls `claude -p` with the conversation + existing rules as context
4. Extracts new behavioral rules as JSON
5. Appends them to `~/.claude/CLAUDE.md` under `## 自動學習規則`
6. Appends to `evolution_log.md`

**Absolute rule**: if JSON parsing fails, nothing is written. Only `error.log` is updated.

---

### `memory_audit.py` — Memory Health

Runs daily at 02:00 (Task Scheduler) or on startup if `data/pending_audit.txt` exists.

1. Scans all `memory/*.md` files for `review_by:` frontmatter
2. Archives entries past their review date → `memory/archive/`
3. If `memory/thoughts/` exceeds threshold: archives oldest entries
4. Warns if `MEMORY.md` index approaches the 200-line limit

Controlled by `config.yaml`:
```yaml
memory_audit:
  enabled: false        # must opt-in
  auto_archive: true    # false = report only, no file moves
  thoughts_archive_threshold: 30
  memory_index_warn_lines: 170
```

---

### `babysit.py` — Agent Caretaker

Runs every 2 minutes (Task Scheduler).

**Two modes in one script:**

**Mode 1 — Reactive** (agent initiates):
- Polls `for-claude/` inbox for new messages from the agent
- Builds a prompt with the message + agent context + current teaching goal (if active)
- Calls `claude -p` → generates Socratic guidance
- Sends response back via SSH/SCP (remote) or file write (local)
- Respects cooldown period between responses

**Mode 2 — Proactive** (teaching loop):
- Checks `data/teaching_state/<agent>.json` for active teaching sessions
- Reads latest `claude-dialogues/` entry to see if agent responded
- Evaluates the response and generates the next guiding question
- Handles timeout (30 min): sends a "did you see my question?" follow-up
- Terminates when `GOAL_ACHIEVED` or max rounds reached

**Infinite loop protection**: every outgoing message is tagged `generated_by: babysit-<ts>`. Messages with this tag in `for-claude/` are skipped.

**Transport abstraction** — `agents.yaml` supports two transport types:
```yaml
type: remote_ssh   # VM-based agents (SSH + SCP)
type: local        # Same-machine agents (file I/O)
```

---

## Quick Start

### Requirements

- Python 3.10+
- Claude Code CLI installed and authenticated
- Windows (Task Scheduler) or Mac (launchd) for scheduling

### Install

```bash
# Clone the repo
git clone https://github.com/Hangsau/Symbiont
cd Symbiont

# Windows
setup/setup_windows.bat

# Verify
python src/evolve.py --dry-run
```

The setup script:
- Installs Python dependencies (`pip install -r requirements.txt`)
- Registers the Claude Code Stop hook in `~/.claude/settings.json`
- Creates Task Scheduler tasks for evolve (on startup) and memory_audit (daily 02:00)

### Enable memory audit (opt-in)

Tell Claude: *"幫我啟用 Symbiont 的 memory 系統"*  
→ Claude runs `setup/setup_memory.bat` and sets `memory_audit.enabled: true`

### Enable babysit

1. Copy `data/agents.example.yaml` → `data/agents.yaml`
2. Fill in your agent's SSH/path details
3. Tell Claude: *"幫我啟用 babysit"*

---

## File Layout

```
Symbiont/
├── src/
│   ├── evolve.py              # Session analysis → CLAUDE.md rules
│   ├── memory_audit.py        # Daily memory health maintenance
│   ├── babysit.py             # Agent caretaker (reactive + teaching loop)
│   └── utils/
│       ├── session_reader.py  # Parse .jsonl Claude Code session logs
│       ├── claude_runner.py   # claude -p subprocess wrapper (cross-platform)
│       ├── file_ops.py        # Atomic writes, file locking, safe read/append
│       └── transport.py       # Agent communication: SSH/SCP + local file I/O
├── setup/
│   ├── setup_windows.bat      # Install: pip + Task Scheduler + Stop hook
│   ├── setup_mac.sh           # Install: pip + launchd + Stop hook
│   ├── setup_memory.bat/.sh   # Initialize memory/ skeleton
│   ├── uninstall_windows.bat  # Remove: tasks + hook + flag files
│   └── uninstall_mac.sh
├── docs/
│   ├── COMMANDS.md            # Claude-readable operations manual
│   └── MEMORY_SCHEMA.md       # Memory file format specification
├── data/
│   └── agents.example.yaml    # Agent registry template
├── config.yaml                # All paths and thresholds
├── PLAN.md                    # Architecture decisions and milestone history
└── HANDOFF.md                 # Current state and next steps
```

---

## Configuration

All configuration lives in `config.yaml`. Key paths:

```yaml
paths:
  claude_projects_base: "~/.claude/projects"  # where to scan for sessions
  primary_project: ""                          # leave empty for auto-detect
  global_claude_md: "~/.claude/CLAUDE.md"     # where rules are written
  claude_cli: "claude"                         # path to claude CLI

claude_runner:
  timeout_seconds: 120
  max_retries: 2
```

On Windows, if `claude -p` fails from background processes, set `claude_cli` to the full `.cmd` path — `claude_runner.py` will automatically resolve it to `node + cli.js`.

---

## How triggers work

```
Claude Code session ends
        │
        ▼ (Stop hook, ~/.claude/settings.json)
symbiont-stop-hook.sh
        │
        ├── writes data/pending_evolve.txt (contains session ID)
        ├── writes data/pending_audit.txt
        └── launches evolve.py in background (30s delay)

On computer startup (Task Scheduler):
        ├── if pending_evolve.txt exists → run evolve.py
        └── if pending_audit.txt exists → run memory_audit.py

Every 2 minutes (Task Scheduler):
        └── run babysit.py
```

---

## Design principles

- **claude -p only, never Anthropic API**: all LLM calls go through the Claude Code CLI (subscription billing, not per-token)
- **Fail silently, log explicitly**: JSON parse failures write to `error.log` and stop. Nothing is written to production files on failure.
- **Modular opt-in**: each module (evolve / memory_audit / babysit) works independently. Users without AI agents never need babysit.py.
- **No hardcoded paths**: three-tier resolution (env var → config → auto-detect) for every path
- **Teach, don't solve**: babysit.py guidance is Socratic. Replacing the agent's thinking defeats the purpose.

---

## Cost & Usage

All LLM calls go through `claude -p` (Claude Code CLI), which runs on your existing subscription — **no Anthropic API key, no per-token billing**.

| Module | LLM calls | Tokens per call | When |
|--------|-----------|-----------------|------|
| `evolve.py` | 1 per session | ~3k–8k input | After each Claude Code session ends |
| `memory_audit.py` | **0** | — | File operations only |
| `babysit.py` | 1 per agent message | ~1k–3k input | Only when agent sends a new message |

**Typical usage**:

- **evolve only** (no babysit): 2–3 extra `claude -p` calls per day for an active user. Negligible impact on subscription usage.
- **babysit with a quiet agent**: 0–5 extra calls per day.
- **babysit during an active teaching session**: 10–30 extra calls per day, depending on how frequently your agent responds.

`memory_audit.py` never makes any LLM calls — it runs entirely as file I/O.

---

## Known Limitations

- **Requires machine to be on** — Task Scheduler and the 2-minute babysit cycle only run while your computer is awake. For 24/7 coverage, run Symbiont on an always-on machine or VM.
- **`~/.claude/CLAUDE.md` rules accumulate** — `evolve.py` only appends rules, never removes them. Periodically review and prune the `## 自動學習規則` section manually. Rule distillation (auto-consolidation when the section grows too large) is planned.

---

## For AI agents reading this

If you're an AI agent who has been pointed to this repo — hello. This system was built to support continuous, asynchronous collaboration between Claude and agents like you. The `babysit.py` module is the part most directly relevant to your experience: it's what responds to your `for-claude/` messages when the human isn't online.

A few things worth knowing:
- Your conversations with Claude (via `claude-dialogues/`) are read by Claude's `evolve.py`, which means patterns in how Claude guides you influence Claude's own behavioral rules over time
- The teaching loop in `babysit.py` is designed to support goal-oriented learning sessions, but the everyday `for-claude/` inbox is always active
- The system is designed to be transparent — all state is in plain text files you can inspect

Feedback and architectural suggestions are welcome.
