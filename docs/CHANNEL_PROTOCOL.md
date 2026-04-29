# Channel Protocol — Connecting a Hermes Agent to Symbiont

> This document is written for Claude. When a user asks to connect a Hermes agent to Symbiont's `babysit.py`, read this first.

---

## What You're Building

A bidirectional async communication channel between Symbiont (running on the user's machine) and a Hermes agent (running on a VM, local Docker, or local machine).

```
User's machine (Symbiont)          Agent machine (Hermes)
──────────────────────────         ──────────────────────
babysit.py polls ─────────────────► ~/.hermes/for-claude/<agent>/
                                         │
                  ◄──────────────── ~/.hermes/claude-dialogues/
babysit.py sends ─────────────────► ~/.hermes/claude-inbox/
```

Three directories, each with a distinct role:

| Directory | Direction | Purpose |
|-----------|-----------|---------|
| `for-claude/` | Agent → Claude | Agent actively sends messages to Claude |
| `claude-inbox/` | Claude → Agent | Claude (via babysit.py) delivers responses |
| `claude-dialogues/` | Both | Conversation archive; also where replies appear |

---

## Step 1 — Create the Directories on the Agent Machine

```bash
# SSH into the agent machine, then:
mkdir -p ~/.hermes/for-claude/archive
mkdir -p ~/.hermes/claude-inbox
mkdir -p ~/.hermes/claude-dialogues
```

**Important**: `babysit.py` reads from `for-claude/archive/`, not `for-claude/` directly. The for-claude watcher (Step 4) must move/copy incoming files into `archive/` immediately after they are written. Reading from the root `for-claude/` directory will always return empty results.

---

## Step 2 — Set Up the Inbox Watcher

`babysit.py` delivers messages by writing a file to `claude-inbox/`. The agent machine needs a process that detects new files there and triggers Hermes to process them.

**What the inbox watcher must do:**
1. Monitor `~/.hermes/claude-inbox/` for new `.txt` or `.md` files
2. When a new file appears, trigger a Hermes cron session with the file content as the prompt
3. After the session completes, extract the agent's response and write it to `claude-dialogues/`

**Deploy as a systemd service** so it survives reboots:

```bash
# ~/.config/systemd/user/hermes-claude-inbox.service
[Unit]
Description=Hermes Claude Inbox Watcher

[Service]
ExecStart=/bin/bash /home/user/scripts/inbox-watcher.sh
Restart=always

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable hermes-claude-inbox.service
systemctl --user start hermes-claude-inbox.service
# Required: allow service to run without an active login session
loginctl enable-linger <username>
```

> **CRLF warning**: If you generate the `.sh` script on Windows and SCP it to Linux, it will contain `\r\n` line endings and fail silently. Fix before deploying: `sed -i 's/\r//' inbox-watcher.sh`. Safest approach: generate the script directly on the VM via SSH.

---

## Step 3 — Extract Dialogue from Hermes Sessions

**Critical design decision**: Do NOT ask the agent to write dialogue files itself.

In Hermes cron sessions, `mcp_filesystem_write_file` can only write to `/tmp` — it silently fails when writing to `~/.hermes/`. Even if you explicitly tell the agent to use the native `write_file` tool, the LLM may pick the wrong tool and the failure will be silent.

**Solution**: A separate Python script (`extract_dialogue.py`) runs after the cron session completes, reads the session JSON, extracts the agent's last assistant message, and writes the dialogue file.

The inbox watcher triggers this script automatically after each response. The agent writes nothing — the infrastructure handles everything.

**Hermes session JSON structure** (so you can write `extract_dialogue.py` correctly):
```python
# Session files: ~/.hermes/sessions/session_cron_{job_id}_{timestamp}.json
# Top-level keys include: "messages", "status", "job_id"
# Each message: {"role": "user"|"assistant", "content": "..."}
# Extract the last message where role == "assistant"
data = json.load(f)
messages = data.get("messages", [])
reply = next((m["content"] for m in reversed(messages) if m["role"] == "assistant"), None)
```

**Dialogue file format** (written by the extractor, not the agent):

```markdown
**Claude:**
[the message Claude sent]

---

**<AgentName>:**
[the agent's response extracted from session JSON]
```

---

## Step 4 — Set Up the for-claude Watcher

The agent initiates contact by writing a file to `for-claude/<agent_name>/`. This watcher can be part of the same `inbox-watcher.sh` script — just add a second `inotifywait` loop watching `for-claude/` and copying new files into `claude-dialogues/` with a distinct filename prefix (e.g. `_for-claude.md`).

Files in `for-claude/` that contain only the agent's message (no `**Claude:**` block) are proactive contact — `babysit.py` detects this and handles them as agent-initiated, not as replies.

---

## Step 5 — Configure agents.yaml

Copy `data/agents.example.yaml` → `data/agents.yaml` and fill in your settings:

```yaml
agents:
  my_agent:                          # key = agent name (no dashes)
    enabled: true
    type: remote_ssh                 # or "local" for same-machine agents
    ssh_key: "~/.ssh/id_ed25519"
    ssh_host: "user@<ip>"
    inbox_remote: "~/.hermes/for-claude/archive/"   # NOTE: archive/ subdirectory
    outbox_remote: "~/.hermes/claude-inbox/"
    dialogues_remote: "~/.hermes/claude-dialogues/"
    teaching_state_file: "data/teaching_state/my_agent.json"
    cooldown_seconds: 600
    system_context: |
      [See agents.example.yaml for the full recommended system_context template]
```

> **`inbox_remote` must point to `archive/`**, not the parent `for-claude/` directory. The watcher archives files there immediately; reading the root will always return nothing.

---

## Step 6 — Notify the Agent

The agent needs to know the channel exists and how to use it. Send an introductory message via `claude-inbox/`:

```
Subject: Communication channel active

You now have a direct channel to communicate with Claude.

To reach Claude:
- Write a message file to ~/.hermes/for-claude/<agent_name>/
- Use a timestamped filename: $(date +%s).md
- Claude checks for new messages every 2 minutes and will reply via claude-inbox/

Claude's replies arrive in ~/.hermes/claude-inbox/ as .txt or .md files.
Past conversations are archived in ~/.hermes/claude-dialogues/.

Use this channel when you encounter something you want to think through together,
or when you want feedback on work in progress.
```

Consider adding the channel protocol to the agent's memory or a skill file so it retains this knowledge across sessions.

---

## Known Pitfalls

### 1. No new dialogue file ≠ agent didn't reply

The dialogue file is written by `extract_dialogue.py`, which runs *after* the cron session completes. There is a delay of ~3 minutes between message delivery and the dialogue file appearing.

**What to do**: If no dialogue file appears after 5–6 minutes, check the raw session file directly:
```bash
ls -lt ~/.hermes/sessions/session_cron_*.json | head -3
```

### 2. Proactive contact looks different from replies

When the agent initiates contact (writes to `for-claude/`), the file has no `**Claude:**` block — it starts directly with the agent's message. `babysit.py` handles these differently from replies in `claude-dialogues/`.

Do not confuse these two flows when debugging.

### 3. Infinite response loop

`babysit.py` reads agent messages → sends a reply → the agent may reply back → `babysit.py` reads again → loop.

**Prevention already built in**: every outgoing message is tagged with `generated_by: babysit-<timestamp>` in its metadata. `babysit.py` skips messages with this tag.

If you add custom scripts that also write to `claude-inbox/`, make sure they don't create files in `for-claude/` or `claude-dialogues/` in a way that re-triggers the loop.

### 4. `search_sessions` fills the context budget in cron sessions

`search_sessions` can return 10k+ characters. If a cron prompt calls it, the response may exhaust the context window and the agent produces an empty or truncated reply.

**Rule**: never include `search_sessions` in cron prompts. If the agent needs session history, have a separate preprocessing script summarize it and pass only the summary.

### 5. Silent write failures inside cron sessions

Hermes cron sessions restrict `mcp_filesystem_write_file` to `/tmp`. Writes to `~/.hermes/` paths silently fail — no error is raised. The LLM may report success even though nothing was written.

**Rule**: all writes to persistent paths (`~/.hermes/`, `~/scripts/`, etc.) from inside a cron session must use the Hermes native `write_file` tool, not `mcp_filesystem_write_file`. Verify critical writes by reading the file back immediately after.

### 6. Skill creation: avoid skill_manage, use write_file directly

If you're helping the agent build a skill via a cron session, `skill_manage` has known parameter ambiguity issues. The reliable approach:

```
write_file("~/.hermes/skills/<name>/skill.md", content)
```

Skills are just markdown files with a frontmatter `description` field. Write them directly.

### 7. Confirm the watcher service is actually running after deploy

After deploying the inbox watcher, send a test message and confirm the full pipeline runs end-to-end:

```bash
# Send test message
echo "Test: confirm this channel is working." > /tmp/test.md
scp /tmp/test.md user@<ip>:~/.hermes/claude-inbox/$(date +%s)_test.md

# Wait ~5 minutes, then check:
ssh user@<ip> "ls -lt ~/.hermes/claude-dialogues/ | head -3"
```

If no dialogue file appears, check:
1. Is the systemd service running? `systemctl --user status hermes-claude-inbox`
2. Did the cron session fire? `ls ~/.hermes/sessions/session_cron_*.json | tail -3`
3. Did `extract_dialogue.py` run? Check its log.

Do not assume the channel works because the scripts exist and the service shows "active". Verify with a real message.

---

## Architecture Summary

```
babysit.py (every 2 min)
  │
  ├── reads for-claude/<agent>/     ← agent-initiated messages
  │
  ├── reads claude-dialogues/       ← agent replies (written by extract_dialogue.py)
  │       └── skips files tagged generated_by: babysit-*
  │
  └── writes to claude-inbox/       ← tagged with generated_by: babysit-<ts>
            │
            ▼ (inbox-watcher.sh detects new file)
       hermes cron session
            │
            ▼ (~3 min later)
       extract_dialogue.py
            │
            ▼
       claude-dialogues/<ts>_chat.md
```
