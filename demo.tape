# riftor demo — rendered by VHS (https://github.com/charmbracelet/vhs)
# Offline: a dummy key + a mock agent response mean no model/API call is made.
# Regenerate locally:  vhs demo.tape   (or push this file — CI renders it)

Output demo.gif

Require riftor

Set Shell bash
Set FontSize 16
Set Width 1200
Set Height 720
Set Padding 18
Set TypingSpeed 55ms

# --- setup (hidden) --------------------------------------------------------
# Dummy key avoids the Ollama probe; RIFTOR_DEMO_RESPONSE makes the agent task
# stream a canned reply (no model call). Pre-seed one finding so /findings and
# /report have real content. Start from a clean engagement for a reproducible run.
Hide
Type "export ANTHROPIC_API_KEY=sk-demo" Enter
Type `export RIFTOR_DEMO_RESPONSE='Recon on 10.0.0.5 shows 22/ssh and 80/http (nginx). The exposed SSH service is the seam — recorded as a finding. Advancing to Intrusion.'` Enter
Type "rm -rf .riftor" Enter
Type `python -c "from pathlib import Path; from riftor.engagement import Engagement; e=Engagement(Path('.')); e.add_finding(title='Exposed SSH', severity='medium', host='10.0.0.5', evidence='22/tcp open ssh OpenSSH 8.2', recommendation='Restrict SSH to a bastion / VPN')"` Enter
Type "clear" Enter
Show

# --- launch ----------------------------------------------------------------
Type "riftor" Enter
Sleep 2.5s

# orient
Type "/help" Enter
Sleep 2.5s

# the guardrail — riftor's identity: scope is enforced
Type "/scope add 10.0.0.0/24 example.com" Enter
Sleep 1.8s

# task the agent (offline mock) — it reasons and narrates the recon
Type "enumerate 10.0.0.5 and flag exposed services" Enter
Sleep 3.5s

# advance the RIFT methodology (shows the colored stage divider)
Type "/stage I" Enter
Sleep 1.5s

# review findings (severity-sorted) and write the report
Type "/findings" Enter
Sleep 1.8s
Type "/report md" Enter
Sleep 1.8s

Type "/exit" Enter
Sleep 1.2s
