# Claude Code Memory Systems: Comparison & Symbiont Analysis

*Last updated: 2026-04-30 | Research by Talos (Hermes Agent) + Claude Code*

---

## The Landscape

Claude Code's native memory is a two-file system: `CLAUDE.md` (injected on every session start) and `MEMORY.md` (loaded by convention). Persistent memory beyond that is a gap the ecosystem has been filling since 2025, producing several distinct approaches.

This document compares Symbiont against the most active alternatives, then assesses Symbiont's own code health and maturity.

---

## Systems Compared

| System | Approach | Transport | Storage | Billing model |
|--------|----------|-----------|---------|--------------|
| **Claude Code native** | Static markdown files | Auto-inject | Local `.md` | Subscription |
| **Symbiont** (this repo) | Daemon extracts rules + babysits agents | `claude -p` subprocess | Local `.md` + YAML | Subscription |
| **EngramX** ([NickCirv/engram](https://github.com/NickCirv/engram)) | Context spine intercepting Read/Edit | MCP | SQLite (bi-temporal) | $0 local |
| **Memsearch** ([zilliztech/memsearch](https://github.com/zilliztech/memsearch)) | Semantic memory layer | MCP plugin | Milvus vector DB | $0 local (ONNX) |
| **claude-mem** ([thedotmack/claude-mem](https://github.com/thedotmack/claude-mem)) | Session capture → compress → inject | Hooks + MCP | ChromaDB | Subscription (Haiku compress) |
| **Engram** ([Gentleman-Programming/engram](https://github.com/Gentleman-Programming/engram)) | Agent-agnostic memory store | MCP + HTTP API + CLI | SQLite + FTS5 | $0 local |
| **mcp-knowledge-graph** ([shaneholloman](https://github.com/shaneholloman/mcp-knowledge-graph)) | Knowledge graph | MCP | Local JSON graph | $0 local |

---

## Multi-Dimensional Comparison

### Recall quality

| System | Method | Strengths | Weaknesses |
|--------|--------|-----------|-----------|
| Claude Code native | Full-file inject | Zero latency, always available | No search; entire file loaded regardless of relevance |
| Symbiont | Rule extraction + grep | Rules are distilled, not raw | No semantic search; exact-match only |
| EngramX | 9 providers + bi-temporal | Mistake-guard; git/AST context; 89.1% token savings | Complex setup |
| Memsearch | BM25 + dense + RRF hybrid | Best-in-class semantic recall; multi-agent compatible | Requires Milvus infra |
| claude-mem | ChromaDB vector search | Auto-captures without any manual tagging | Depends on Haiku for compression quality |
| Engram | SQLite FTS5 full-text | Fast; agent-agnostic; offline-first | No vector/semantic search |
| mcp-knowledge-graph | Graph traversal | Captures relationships between entities | Manual curation required |

### Scalability

| System | Memory capacity | Growth model |
|--------|----------------|-------------|
| Claude Code native | ~200 MEMORY.md lines before context truncation | Manual; needs human curation |
| Symbiont | MEMORY.md index (200-line limit enforced) + archive/ | Auto-distillation at threshold; thoughts/ overflow archival |
| EngramX | SQLite; bounded by retention policy | Bi-temporal; old entries automatically expire |
| Memsearch | Milvus (100k+ vectors) | Continuous append; progressive disclosure limits injection |
| claude-mem | ChromaDB (100k+ vectors) | Continuous; semantic retrieval bounds context cost |
| Engram | SQLite; unlimited practical size | Configurable retention |
| mcp-knowledge-graph | Local JSON; depends on graph size | Manual; no auto-pruning |

### Reliability & error handling

| System | Failure mode | Recovery |
|--------|-------------|----------|
| Claude Code native | File corruption = silent failure | Manual |
| Symbiont | JSON parse fail → error.log only, no writes; FileLock on all concurrent writes; dead letter queue for SCP failures; 3-tier trigger (Stop hook → pending file → startup catchup) | Automatic retry; crash detection via lock age |
| EngramX | MCP crash = no context spine | MCP restart |
| Memsearch | Milvus down = no semantic search | Plugin degrades gracefully |
| claude-mem | Hook fail = no capture | Partial session loss |
| Engram | Go binary crash = no writes | No specific recovery mechanism documented |
| mcp-knowledge-graph | MCP crash | MCP restart |

### Cost model

| System | Per-session cost | Notes |
|--------|----------------|-------|
| Claude Code native | $0 | Static file; no LLM calls |
| Symbiont | 1-2 `claude -p` calls per session (subscription, not API tokens) | Distillation adds 1 extra call at threshold |
| EngramX | $0 | Pure local compute; no LLM calls for storage |
| Memsearch | $0 | Local ONNX embeddings |
| claude-mem | ~$0.001/session | Haiku for compression |
| Engram | $0 | SQLite only |
| mcp-knowledge-graph | $0 | No LLM calls |

### Maintenance burden

| System | Setup complexity | Ongoing maintenance |
|--------|----------------|---------------------|
| Claude Code native | None | Manual curation |
| Symbiont | Windows Task Scheduler + Stop hook config | Automatic (distillation, archival, dead letters) |
| EngramX | npm install + MCP config | Auto; self-managing |
| Memsearch | Milvus setup required | Auto after setup |
| claude-mem | Plugin install | Auto |
| Engram | Binary install + MCP config | Auto |
| mcp-knowledge-graph | MCP config | Manual graph curation |

---

## What Symbiont Does That Nobody Else Does

### 1. Behavioral rule extraction (not context injection)

Every other system injects memories *into* Claude's context window. Symbiont writes extracted rules *into `CLAUDE.md`* — Claude's behavior configuration. The difference:

- Context injection: "here's what happened last time"
- CLAUDE.md extraction: "here's how to behave from now on"

Rules survive context compaction, cannot be pushed out by long sessions, and apply across all future sessions without any retrieval step.

### 2. AI agent babysitting with teaching loop

No other tool maintains ongoing Socratic conversations with a remote AI agent. Symbiont's `babysit.py`:
- Polls agent inbox every 2 minutes
- Generates guidance responses via `claude -p` (not answers — questions)
- Tracks teaching state across sessions (round number, last question, timeout)
- Sends timeout confirmations if agent goes silent for 30 minutes
- Dead-letter queues failed deliveries for retry on next cycle

This is a communication layer, not a memory layer — but it feeds behavioral data back into `evolve.py`.

### 3. Subscription billing (no token cost)

Symbiont uses `claude -p` subprocess exclusively. All LLM analysis runs on Claude subscription, not API tokens. At high session volume (100+ sessions/month), this matters.

### 4. Cross-session synthesis

`synthesize.py` aggregates friction patterns and habit fragments across N sessions, generating new skills and memory entries. The pattern-over-time signal produces different (higher-signal) outputs than per-session analysis.

---

## Symbiont Code Health Assessment

### Architecture

```
3 core modules + 6 utility modules + 70 tests
evolve.py       (721 lines)  — session → CLAUDE.md rules
babysit.py      (622 lines)  — agent inbox → guidance
memory_audit.py (346 lines)  — memory lifecycle management
synthesize.py   (~400 lines) — cross-session pattern synthesis
```

Each module has a single entry point (`run()` or `main()`), `--dry-run` flag, and independent config resolution.

### Strengths

**Correctness guardrails**
- JSON parse failures always fall back to `error.log` only — no partial writes
- Schema validation (`_validate_output`, `_validate_distill_output`) before any file mutation
- Distillation has dual gates: minimum 5 rules output (prevents over-pruning) + must reduce total count (prevents no-op distillations)
- FileLock on every concurrent-access path (CLAUDE.md, synth_state.json, MEMORY.md)

**Reliability mechanisms**
- 3-tier trigger for `evolve.py`: Stop hook (immediate) → `pending_evolve.txt` (session ID preserved) → startup task (catches crashes/reboots)
- Dead letter queue in `babysit.py`: SCP failures queued → retried next cycle → abandoned after 5 attempts with log entry
- Lock age detection: `babysit.lock` > 15 minutes → assumed crash, force-released
- Log rotation: both `babysit.log` (5000 lines) and `error.log` (2000 lines) auto-truncated

**Operational ergonomics**
- `--dry-run` mode on every module: shows exact changes without writing anything
- Config-driven via `config.yaml` with typed getters (`get_int`, `get_path`, `get_str`)
- Platform-aware: `robocopy` on Windows, `rsync` on Unix; `pythonw.exe` stdout redirect handling
- Backup integration: `evolve.py` optionally calls rsync/robocopy after each successful run

**Test coverage**
- 70 tests across `test_evolve.py`, `test_babysit.py`, `test_memory_audit.py`
- Unit tests for: JSON extraction (including brace-counting edge cases), schema validation, lock lifecycle, state serialization, frontmatter parsing, archive logic, thoughts overflow
- No integration tests or end-to-end tests

### Weaknesses

**No semantic recall**
The biggest gap vs. the field. Symbiont writes rules into CLAUDE.md but has no way to answer "what do I know about X?" The memory system is inject-all, not retrieve-relevant. At scale, CLAUDE.md becomes the bottleneck (200-line context limit).

**Polling transport**
`babysit.py` polls every 2 minutes via SCP. There's no event-driven path: a message from an agent always waits up to 2 minutes for a response. The transport layer (`src/utils/transport.py`) is extensible but only SSH/SCP is implemented.

**Session analysis is window-bounded**
`evolve.py` reads at most 50 turns × 800 characters per turn from a session. Long debugging sessions or multi-hour pair programming sessions lose their tail. The truncation is configurable but the default may miss key interactions.

**`synthesize.py` maturity gap**
The three original modules (evolve, babysit, memory_audit) each have dedicated test files and production history. `synthesize.py` has no test file. It's the most complex module and the least validated.

**No rollback beyond last distillation**
`distill_backup.json` stores the pre-distillation rule snapshot, but only the most recent one. There's no version history for CLAUDE.md rules.

**Agent-specific coupling**
`babysit.py` is designed around the Claude↔Hermes agent pattern. The teaching loop assumes a specific dialogue format and transport. Other agent architectures would require adapter work.

### Maturity by module

| Module | Status | Test coverage | Production history |
|--------|--------|--------------|-------------------|
| `evolve.py` | ✅ Stable | Comprehensive | M2+ (months in production) |
| `memory_audit.py` | ✅ Stable | Comprehensive | M3+ (months in production) |
| `babysit.py` | ✅ Stable | Good (unit) | M4+ (months in production) |
| `synthesize.py` | ⚠ Beta | None | M7-M8 (recent) |
| `utils/` | ✅ Stable | Via parent tests | Production-validated |

---

## Gap Analysis: What to Borrow

Based on the comparison, three gaps are worth closing:

### 1. Semantic search for memory recall (from Memsearch / EngramX)
The 200-line MEMORY.md limit is a hard ceiling. A hybrid BM25 + embedding search over the archive would let Symbiont inject only relevant memories rather than the entire index. Implementation path: add a lightweight FAISS/SQLite-vec index alongside the existing file store; expose as a tool via a local MCP server.

### 2. Token savings measurement (from EngramX)
EngramX reports 89.1% token savings. Symbiont has no telemetry on context window usage before/after rule extraction. Adding session turn-count and estimated token cost to `evolution_log.md` would make the value proposition measurable.

### 3. Test coverage for `synthesize.py` (internal gap)
The most complex module has zero tests. At minimum: JSON extraction/validation, skill deduplication logic, and friction/habit fragment extraction should be covered before the module is considered stable.

---

## Summary

Symbiont occupies a different niche than MCP-based memory systems. Where they inject context, Symbiont modifies behavior. Where they scale to 100k memories, Symbiont stays tight and auditable. The trade-off is intentional: CLAUDE.md rules are universal (no retrieval step, no vector infra) but bounded.

The agent babysitting capability is genuinely unique in the ecosystem. No other tool maintains a teaching relationship with a remote AI agent across session boundaries.

The code is production-quality for its three mature modules. `synthesize.py` is the one area that needs test coverage before it can be considered equally reliable.
