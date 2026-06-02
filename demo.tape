# riftor demo — rendered by VHS (https://github.com/charmbracelet/vhs)
# Showcases offline features only (no API key / model calls needed).
# Regenerate locally:  vhs demo.tape   (or push this file — CI renders it)

Output demo.gif

Require riftor

Set Shell bash
Set FontSize 16
Set Width 1200
Set Height 720
Set Padding 18
Set TypingSpeed 55ms

# --- setup (hidden): dummy key avoids the Ollama probe; we never call a model
Hide
Type "export ANTHROPIC_API_KEY=sk-demo" Enter
Type "clear" Enter
Show

Type "riftor" Enter
Sleep 2.5s

Type "/help" Enter
Sleep 2.5s

Type "/scope add 10.0.0.0/24 example.com" Enter
Sleep 1.8s

# live theme switching
Type "/theme void" Enter
Sleep 1.4s
Type "/theme fracture" Enter
Sleep 1.4s
Type "/theme singularity" Enter
Sleep 1.4s
Type "/theme rift" Enter
Sleep 1.2s

# advance the RIFT stage + review findings
Type "/stage I" Enter
Sleep 1.2s
Type "/findings" Enter
Sleep 1.8s

# settings modal
Type "/config" Enter
Sleep 2.5s
Escape
Sleep 1s

Type "/exit" Enter
Sleep 1.5s
