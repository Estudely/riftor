# Riftor Improvement Plan — From Tool Orchestrator to Knowledge-Driven Attacker

## Current State

Riftor is a well-engineered offensive-security AI agent with:
- Clean tool system (bash, read, write, edit, grep, webfetch) with permission layering
- Scope enforcement at the tool execution boundary (IP/CIDR/domain/wildcard)
- SQLite-backed engagement state (findings, services, hosts, activity log)
- RIFT stage tracking (Recon → Intrusion → Foothold → Takeover)
- Report generation (md/html/json/sarif) with CVSS scoring
- Full TUI with sessions, command palette, token/cost metering
- Crash-safe sessions with auto-resume
- Audit trail (JSONL with rotation)

## What's Missing

The agent has strong infrastructure but is **methodology-light**. It executes tools well but doesn't know WHAT to look for, doesn't learn from past mistakes, can't track open leads, and can't verify its own findings. Each session starts from zero.

## Improvements — Priority Order

---

### 1. Lessons & Memory System (HIGHEST IMPACT)

**Problem**: Agent forgets everything between sessions. Operator corrections are lost. Same mistakes repeat.

**What to build**:

- A `lessons.json` file at `~/.config/riftor/lessons.json` — persists forever across sessions
- Each lesson: `{"id": "...", "trigger": "when testing JWT", "lesson": "always check alg=none before anything else", "source": "operator|agent", "ts": 1234567890}`
- A `record_lesson` engagement tool — agent or operator writes a lesson after a discovery/correction
- A `/lesson <text>` slash command — operator types it, riftor saves it
- On every session start — load all lessons into the system prompt context (inject after the RIFT methodology section)
- Agent is instructed: "BEFORE acting in any domain, check if a lesson applies. Follow lessons over your own judgment."

**Files to modify**:
- `riftor/tools/engagement.py` — add `RecordLessonTool` 
- `riftor/agent/context.py` — inject lessons into system prompt on session start
- `riftor/tui/app.py` — add `/lesson` slash command
- Create `riftor/engagement/lessons.py` — LessonStore class (JSON read/write, dedup, search)

**Lesson format** (in the prompt context):
```
## LESSONS (follow these — they override your default behavior)
- WHEN testing JWT → always check alg=none and weak HMAC secret FIRST (operator-taught)
- WHEN you get a 500 on a payload → your request is malformed, read the source code first (operator-taught)
- WHEN scanning ports → use naabu for speed, nmap -sCV only on open ports (agent-learned)
```

---

### 2. Anti-Loop / Circuit Breaker

**Problem**: Agent can spin on the same failing command, wasting tokens. Only guard is `max_steps=16` which is a blunt hammer.

**What to build**:

- **Repeat detector**: Track last N commands (normalized — collapse whitespace). If the same command appears 3x in a row → inject a note: "You've run this command 3 times. Try a different approach."  If 6x → hard stop the branch.
- **Stale detector**: Track rounds without new findings or services recorded. If 5+ rounds produce nothing → inject: "No new discoveries in 5 rounds. Consider pivoting or stopping."
- Both are counters in the agent loop, not new modules.

**Files to modify**:
- `riftor/agent/session.py` or wherever the agent loop lives — add repeat tracking + stale tracking
- Inject notes as system messages when thresholds are hit

---

### 3. Methodology Skills (Loadable Knowledge Files)

**Problem**: System prompt has ~50 lines of methodology. A real red team operator carries thousands of lines of checklists, payload patterns, tool commands, and evidence standards in their head. The agent has none of that.

**What to build**:

- A `skills/` directory at `~/.config/riftor/skills/` with markdown files
- A `LoadSkillTool` — agent reads a skill file before acting in that domain
- System prompt instruction: "Before each RIFT stage, load the matching skill. Operating from memory when a skill exists is a defect."

**Skill files to create**:

| File | Content |
|---|---|
| `skills/recon.md` | Passive surface (cert transparency, subdomain enum, historical URLs, tech fingerprint), active surface (port scan, vhost, content discovery), JS recon, parameter discovery |
| `skills/recon-dorking.md` | Google dork operators, vuln-class dork libraries (admin panels, config leaks, API docs, cloud assets), GitHub dorking, Shodan/Censys queries, automation tools |
| `skills/exploitation.md` | Per-vuln-class methodology: XSS (by context), SQLi (by DB engine), SSRF (cloud metadata, IP bypass), SSTI (by engine), command injection, IDOR, auth bypass |
| `skills/payloads.md` | Concrete, copy-adaptable payloads organized by vuln class × sink context. XSS (15+), SQLi per DB (20+), SSRF (15+), SSTI per engine (6+), command injection variants |
| `skills/reporting.md` | Evidence standards, finding contract (what fields are required), severity calibration, attacker model requirement, dupe-check checklist |
| `skills/lessons-learned.md` | Starts empty. Agent and operator append lessons here. Loaded on every session start. |

**Files to modify**:
- `riftor/tools/engagement.py` — add `LoadSkillTool`
- `riftor/agent/context.py` — on session start, auto-load `lessons-learned.md`
- System prompt (agent/prompts/system.md) — add skills table + loading ritual

---

### 4. Hypothesis Tracker

**Problem**: Agent explores ad-hoc. No structured "I suspect X because Y, need to test Z" tracking. Open leads get forgotten.

**What to build**:

- A `hypotheses` table in the SQLite engagement database:
  ```sql
  CREATE TABLE hypotheses (
      id TEXT PRIMARY KEY,
      statement TEXT NOT NULL,
      status TEXT DEFAULT 'open',  -- open|confirmed|refuted|inconclusive
      rationale TEXT DEFAULT '',
      evidence_ref TEXT DEFAULT '',
      created REAL,
      updated REAL
  );
  ```
- A `RecordHypothesisTool` — agent logs what it suspects
- A `ListHypothesesTool` — shows open/resolved hypotheses
- A `ResolveHypothesisTool` — marks as confirmed/refuted with rationale
- System prompt instruction: "Track your suspicions as hypotheses. Never re-test a refuted hypothesis. Resolve them as you get evidence."
- A `/hypotheses` slash command in the TUI

**Files to modify**:
- `riftor/engagement/state.py` — add hypotheses table + CRUD methods
- `riftor/tools/engagement.py` — add 3 hypothesis tools
- `riftor/tui/app.py` — add `/hypotheses` command

---

### 5. Finding Confidence + Verification

**Problem**: Findings have severity but no confidence score. No way to distinguish "I'm 90% sure this is real" from "maybe, needs more testing." No verification that a finding is real vs a false positive.

**What to build**:

- Add `confidence INTEGER DEFAULT 0` and `verification_method TEXT DEFAULT ''` columns to the findings table
- Confidence 0-10. The system prompt says: "Confidence 8+ requires a complete attacker-controlled-input → sink chain AND a defined attacker model. Cap at 6 otherwise."
- Verification methods: `canary` (unique string reflected), `oob` (out-of-band callback), `timing` (time delay), `exact` (value match), `manual` (operator verified)
- System prompt: "A finding is CONFIRMED only when a deterministic verification fires. Status codes alone are NOT proof."
- Update `record_finding` to accept confidence + verification_method args

**Files to modify**:
- `riftor/engagement/state.py` — add columns, update schema migration
- `riftor/tools/engagement.py` — update RecordFindingTool parameters
- System prompt — add oracle/verification guidance

---

### 6. Self-Critique / Review Pass

**Problem**: No QA before reporting. Findings go straight to report without checking if they're real.

**What to build**:

- A `/review` slash command that iterates all findings and checks:
  - CONFIRMED without a PoC or evidence? → downgrade to LIKELY
  - High confidence without attacker model? → cap confidence at 6
  - No verification method? → flag as "unverified"
- Output: a summary of what was downgraded and why
- Add `--apply` to write the changes, without it → dry-run

**Files to modify**:
- `riftor/tui/app.py` — add `/review` command handler
- `riftor/engagement/state.py` — add a `review_findings()` method

---

### 7. System Prompt Improvements

**Problem**: The RIFT system prompt is thin. No vuln checklists, no evidence standards, no escalation guidance.

**What to add to `agent/prompts/system.md`**:

- **Test smart, not brute** — understand the code/contract FIRST, then send ONE targeted request
- **Evidence over assertion** — never invent CVEs, versions, endpoints. Quote output, don't paraphrase.
- **Confidence + status taxonomy** — CONFIRMED/LIKELY/SUSPECTED/INFO/FALSE-POSITIVE with definitions
- **Attacker model required** — "who controls what and what they gain, or it isn't a finding"
- **Dupe-check before deep work** — check GHSA/NVD/Huntr before spending hours
- **Skills loading ritual** — load the matching skill before each RIFT stage
- **Lessons injection point** — where lessons get inserted on session start

---

### 8. OSINT / Dorking in Recon Stage

**Problem**: No passive reconnaissance guidance. Agent jumps straight to active scanning.

**Solution**: This is covered by the `skills/recon-dorking.md` skill file (item 3). No code changes needed beyond the skill loading system. The skill file carries:
- Google dork patterns per target
- GitHub dorking syntax for credential hunting
- Shodan/Censys query patterns
- Rate limiting guidance
- Automation tool references

---

## Implementation Order

```
1. Lessons & Memory     → immediate payoff, simplest to add
2. Anti-Loop            → 20 lines of code, prevents token waste
3. System Prompt        → pure text changes, big methodology improvement
4. Skills System        → skill files + load tool, pure knowledge injection
5. Hypothesis Tracker   → SQLite table + 3 tools
6. Finding Confidence   → 2 columns + prompt guidance
7. Self-Critique        → /review command
8. Skill Content        → write the actual recon/exploitation/payload skill files
```

## What This Does NOT Change

- Tool system architecture — untouched
- Permission system — untouched
- Scope enforcement — untouched
- TUI framework — only adds new slash commands
- Report generation — untouched (benefits from better findings)
- Session persistence — untouched
- Audit trail — untouched

## End State

After all 8 improvements, riftor goes from "AI tool orchestrator" to "knowledge-driven red team agent" that:
- Remembers lessons across sessions (never repeats the same mistake)
- Loads domain-specific methodology before each phase (not guessing)
- Tracks hypotheses and resolves them systematically (not ad-hoc poking)
- Verifies findings with deterministic signals (not vibes)
- Self-critiques before reporting (catches false positives)
- Detects when it's stuck and pivots (not spinning on dead ends)
- Has concrete payloads to adapt from (not generating from scratch)
