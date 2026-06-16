# Riftor Mesh Phase 2 — Findings Processor Design

> Design spec for the AI-powered findings processing pipeline: the Commander's brain that ingests worker submissions, deduplicates, assesses severity, merges, and publishes canonical findings to iroh-docs.

## Overview

Phase 2 adds the **Findings Processor** — an AI pipeline running inside the Rust daemon that watches gossip submissions and transforms raw worker findings into canonical, deduplicated, severity-assessed entries in iroh-docs. A Commander review/override system provides human-in-the-loop control.

---

## Architecture

### Data Flow

```
Worker finding → gossip .../submit
                      │
              ┌───────▼────────┐
              │ Submission Queue │  (tokio mpsc channel, bounded at 256)
              └───────┬────────┘
                      │
        ┌─────────────┼─────────────┐
        ▼             ▼             ▼
   ┌─────────┐  ┌─────────┐  ┌─────────┐
   │Worker 1 │  │Worker 2 │  │Worker 3 │  (tokio task pool, concurrency=3)
   └────┬────┘  └────┬────┘  └────┬────┘
        │            │            │
        └────────────┼────────────┘
                     │
              ┌──────▼──────┐
              │ LLM Client  │  (HTTP to model endpoint)
              └──────┬──────┘
                     │
        ┌────────────┼────────────┐
        ▼            ▼            ▼
   [new finding] [match→merge] [false pos→reject]
        │            │            │
        └────────────┼────────────┘
                     │
              ┌──────▼──────┐
              │  Publish    │  (write to iroh-docs)
              └──────┬──────┘
                     │
              ┌──────▼──────┐
              │  Notify     │  (gossip .../processed)
              └─────────────┘
```

### New Rust Modules

| Module | Responsibility |
|---|---|
| `meshd/src/processor.rs` | `Processor`: queue management (bounded mpsc), worker pool (tokio tasks), pipeline orchestration, review mode gating |
| `meshd/src/llm.rs` | `LlmClient`: HTTP client for LLM API calls (JSON body, Authorization header), response parsing with retry, circuit breaker |
| `meshd/src/prompts.rs` | Prompt templates: dedup comparison, severity assessment. Static `&str` constants. |

### Changes to Existing Modules

| Module | Change |
|---|---|
| `handler.rs` | `submit` handler: enqueue into processor instead of just broadcasting. The processor returns a `submission_id` immediately (queue accepted) without waiting for AI. |
| `main.rs` | Spawn `Processor` on startup. Pass LLM config from env vars. |
| `docs.rs` | Add `query_similar(target: &str, vuln_class: &str, limit: usize) -> Vec<Value>` for dedup candidate retrieval. |
| `engagement.rs` | `submit` method simplified: just enqueue. Processor handles the rest. |

### LLM Configuration

Daemon reads at startup (environment variables matching riftor's config):
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` — which LLM provider
- `RIFTOR_MODEL` — model ID (default: `anthropic/claude-sonnet-4-6`)
- `RIFTOR_API_BASE` — optional custom API base URL
- `RIFTOR_TEMPERATURE` — (default: `0.3`)

---

## Pipeline Stages (Per Submission)

### Stage 1: Ingest & Validate

Worker pops submission from queue. Validates:
- Required fields present: title, severity, target, vuln_class
- Referenced blob hashes actually exist in blob store
- Not a duplicate of another queued submission (same author + same target + same vuln_class within last 60 seconds)

Invalid submissions get an immediate rejection via gossip `.../processed` with reason. No LLM cost incurred.

### Stage 2: Deduplicate

Fetches up to 5 candidate existing findings from docs using `query_similar()` (same target, or same vuln_class, ordered by recency). Sends to LLM:

**System prompt:** "You are a findings deduplicator for a penetration testing platform. Given a NEW finding and a list of EXISTING findings, determine if the new finding describes the same vulnerability as any existing one. Consider: same target + same vuln_class → likely match, same endpoint but different class → probably distinct, same class but different endpoint → could be same root cause, title/description similarity."

**LLM returns JSON:**
```json
{
  "decision": "new" | "match",
  "confidence": 0.0-1.0,
  "matched_finding_id": "uuid-or-null",
  "reasoning": "Same endpoint (/api/login) and vuln class (sqli)..."
}
```

**Optimization:** If confidence > 0.95 for a match, skip severity assessment and go straight to merge.

### Stage 3: Severity Assessment (new findings only)

LLM receives the finding + engagement context (scope, asset type):

**System prompt:** "You are a CVSS assessor. Given a finding, assign severity and CVSS v3.1 vector. Consider engagement context. Severity: critical (9.0-10.0), high (7.0-8.9), medium (4.0-6.9), low (0.1-3.9), info (0.0)."

**Returns:**
```json
{
  "severity": "critical" | "high" | "medium" | "low" | "info",
  "cvss_vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H",
  "reasoning": "Remote code execution with no authentication..."
}
```

### Stage 4: Merge & Enrich (matched findings only)

- Picks more detailed description (LLM choice, or keeps existing)
- Appends new author to `co_contributors`
- Merges evidence blob hashes (deduplicated by hash)
- Appends to `source_finding_ids` chain
- Re-assesses severity only if new info suggests a change

### Stage 5: Reject (false positives only)

- Out of scope → reject with "Target not in engagement scope"
- False positive → reject with specific reason from LLM or Commander
- Too vague → reject with "Insufficient detail — please provide more context"
- Rejection includes reason string; logged for audit trail

### Stage 6: Publish & Notify

- If in autonomous mode: writes canonical finding to iroh-docs, broadcasts to gossip `.../processed`
- If in review-required mode: holds in pending review queue, Commander must approve
- Notification payload:
  ```json
  {"finding_id": "...", "decision": "accepted"|"merged"|"rejected",
   "severity": "critical", "title": "SQLi in /api/login",
   "merged_into": "uuid|null", "rejection_reason": "..."}
  ```

---

## Commander Review & Override

### Processing Modes

| Mode | Behavior |
|---|---|
| **Autonomous** (default) | Processor auto-publishes. Commander sees notifications but doesn't need to approve. |
| **Review required** | Every decision goes to a review queue. Commander approves/overrides/rejects each via `/mesh review`. |
| **Critical only** | Auto-publish medium/info/low. Queue critical/high for review. |

### Review Queue

In review mode, decisions are held in an in-memory `Vec<PendingDecision>`. The TUI's `/mesh review` command opens an interactive screen:

```
┌─ PENDING REVIEW (3) ─────────────────────────────────────┐
│                                                           │
│  #1 SQLi in /api/login          [CRITICAL]  New           │
│     Submitted by: Alice  |  AI confidence: 0.92           │
│     Target: 10.0.0.5  |  Class: sqli                     │
│     AI reasoning: "Blind SQL injection confirmed..."      │
│                                                           │
│     [Accept] [Override Sev] [Reject] [Skip] [More]        │
│                                                           │
│  #2 XSS in /search              [MEDIUM]    New           │
│  #3 RCE in upload.php           [HIGH]      Merge → #12   │
└───────────────────────────────────────────────────────────┘
```

### Override Actions

| Action | Effect |
|---|---|
| **Accept** | Publish as-is |
| **Override Severity** | Change severity level, then publish |
| **Reject** | Send back to worker with reason |
| **Skip** | Keep in queue |
| **Force Merge** | Manually pick which canonical finding to merge into |
| **Un-reject** | Restore and publish a wrongly-rejected finding |

### Override History & Learning

Every override is logged in an `override_log` (append-only, per engagement). Patterns:
- If Commander consistently overrides severity upward → adjust the severity prompt
- If Commander consistently rejects the same vuln_class → add a section to the dedup prompt
- Future Phase 3: use override history to fine-tune prompts automatically

---

## Error Handling

| Failure | Retry Strategy | Fallback |
|---|---|---|
| LLM API timeout (30s) | 3 retries, exponential backoff (1s, 2s, 4s) | Queue for human review |
| LLM returns invalid JSON | 1 retry with stricter prompt ("Respond with valid JSON only. No markdown.") | Queue for human review |
| LLM rate limited (429) | Respect Retry-After header, up to 60s | Queue for human review |
| Docs write fails | 3 retries | Log error, keep in queue, notify Commander |
| Queue full (256 pending) | Reject with "busy" notification | Worker retries later |

### Circuit Breaker

- If 5 consecutive LLM calls fail → processor pauses for 60s
- Notifies Commander via gossip `.../alerts`
- Resumes automatically after cooldown
- Resets failure count on first success after cooldown

---

## TUI/UX Changes

### New Commands

| Command | Action |
|---|---|
| `/mesh review` | Open pending review screen (interactive finding-by-finding approval) |
| `/mesh mode [autonomous\|review\|critical]` | Switch processing mode |
| `/mesh queue` | Show queue stats (pending, processing, failed) |
| `/mesh processor` | Show processor status (online, circuit-broken, last error) |

### Mesh Sidebar Updates

- New section: "Processor" showing mode badge + queue depth
  ```
  Processor  ● [autonomous]
  Queue: 3 pending / 2 processing
  ```

### Activity Feed Enrichment

- "Processor accepted Alice's finding: SQLi in /api/login (critical)"
- "Processor merged Bob's finding into #42"
- "Processor rejected Charlie's finding: out of scope"
- "Circuit breaker tripped — processor paused for 60s"

### Notification Banners

When a critical finding is auto-published, the Commander's TUI shows a brief notification banner: "CRITICAL: SQLi in /api/login (auto-accepted)"

---

## Security Considerations

- LLM API keys read from environment variables only, never logged
- Prompts do not include raw engagement secrets or credentials
- Circuit breaker prevents runaway API costs
- Review-required mode ensures human oversight on high-stakes findings
- All rejections and overrides are logged for audit

---

## Open Questions

1. **LLM cost estimation**: Should the processor track token usage and report cost per engagement? (Yes, add a cost counter in Phase 2 impl.)
2. **Prompt customization**: Should Commanders be able to edit prompt templates per engagement? (Future — Phase 3)
3. **Multi-LLM fallback**: If Anthropic fails, try OpenAI? (Future — Phase 3)
4. **Queue persistence**: Should the submission queue survive daemon restarts? (No for Phase 2 — in-memory is fine. If the daemon crashes, workers re-submit.)
