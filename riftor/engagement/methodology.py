"""OWASP/PTES methodology checklist — replaces the RIFT stage model."""

from __future__ import annotations

import re
from dataclasses import dataclass

# Tool name or bash pattern → methodology item name (substring match on item name).
_AUTO_TICK_TOOLS: dict[str, str] = {
    "import_scan": "Port scanning",
    "nmap": "Port scanning",
    "subfinder": "Subdomain enumeration",
    "amass": "Subdomain enumeration",
    "httpx": "Technology fingerprinting",
    "whatweb": "Technology fingerprinting",
    "ffuf": "Directory/file discovery",
    "gobuster": "Directory/file discovery",
    "dirb": "Directory/file discovery",
    "nuclei": "Vulnerability Identification",
    "nikto": "Security headers review",
    "dig": "DNS enumeration (records, zone transfer)",
    "nslookup": "DNS enumeration (records, zone transfer)",
    "whois": "WHOIS / ASN lookup",
    "openssl": "SSL/TLS analysis",
    "sqlmap": "SQL injection",
    "wayback": "Wayback Machine / archive recon",
    "crt.sh": "Subdomain enumeration",
    "shodan": "Port scanning",
    "github": "GitHub/source code leak search",
}

_AUTO_TICK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\bnmap\b", re.I), "Port scanning"),
    (re.compile(r"\bsubfinder\b|\bamass\b|\bcrt\.sh\b", re.I), "Subdomain enumeration"),
    (re.compile(r"\bhttpx\b|\bwhatweb\b", re.I), "Technology fingerprinting"),
    (re.compile(r"\bffuf\b|\bgobuster\b|\bdirb\b|\bdirbuster\b", re.I), "Directory/file discovery"),
    (re.compile(r"\bnuclei\b", re.I), "Vulnerability Identification"),
    (re.compile(r"\bnikto\b", re.I), "Security headers review"),
    (re.compile(r"\bdig\b|\bnslookup\b", re.I), "DNS enumeration (records, zone transfer)"),
    (re.compile(r"\bwhois\b", re.I), "WHOIS / ASN lookup"),
    (re.compile(r"\bopenssl\b|\bsslyze\b|\btestssl\b", re.I), "SSL/TLS analysis"),
    (re.compile(r"\bsqlmap\b", re.I), "SQL injection"),
    (re.compile(r"\bwayback\b|web\.archive\.org", re.I), "Wayback Machine / archive recon"),
    (re.compile(r"github\.com/search", re.I), "GitHub/source code leak search"),
    (re.compile(r"\bcurl\b.*-I", re.I), "Security headers review"),
]


@dataclass(frozen=True)
class MethodologyItem:
    category: str
    name: str
    checked: bool = False
    notes: str = ""


def default_methodology() -> list[MethodologyItem]:
    """Default OWASP/PTES checklist (ported from pi redteam)."""
    raw = [
        ("Reconnaissance", "Subdomain enumeration"),
        ("Reconnaissance", "Port scanning"),
        ("Reconnaissance", "Technology fingerprinting"),
        ("Reconnaissance", "Directory/file discovery"),
        ("Reconnaissance", "JavaScript file analysis"),
        ("Reconnaissance", "Wayback Machine / archive recon"),
        ("Reconnaissance", "DNS enumeration (records, zone transfer)"),
        ("Reconnaissance", "SSL/TLS analysis"),
        ("Reconnaissance", "WHOIS / ASN lookup"),
        ("Reconnaissance", "Google dorking"),
        ("Reconnaissance", "GitHub/source code leak search"),
        ("Authentication", "Default credentials"),
        ("Authentication", "Brute force / credential stuffing"),
        ("Authentication", "Password reset flow"),
        ("Authentication", "Account lockout testing"),
        ("Authentication", "Multi-factor auth bypass"),
        ("Authentication", "Session token analysis"),
        ("Authentication", "OAuth/SSO misconfiguration"),
        ("Authentication", "JWT vulnerabilities"),
        ("Authentication", "Registration flow abuse"),
        ("Authorization", "IDOR (horizontal privilege escalation)"),
        ("Authorization", "Vertical privilege escalation"),
        ("Authorization", "Missing function-level access control"),
        ("Authorization", "API endpoint authorization"),
        ("Authorization", "Admin panel access"),
        ("Authorization", "GraphQL authorization"),
        ("Injection", "SQL injection"),
        ("Injection", "Cross-site scripting (XSS)"),
        ("Injection", "Server-side template injection (SSTI)"),
        ("Injection", "Command injection"),
        ("Injection", "XML external entity (XXE)"),
        ("Injection", "LDAP injection"),
        ("Injection", "Header injection / CRLF"),
        ("Injection", "NoSQL injection"),
        ("Client-Side", "CSRF"),
        ("Client-Side", "Clickjacking"),
        ("Client-Side", "DOM-based XSS"),
        ("Client-Side", "Postmessage vulnerabilities"),
        ("Client-Side", "CORS misconfiguration"),
        ("Client-Side", "WebSocket security"),
        ("Server-Side", "SSRF"),
        ("Server-Side", "Path traversal / LFI"),
        ("Server-Side", "Remote file inclusion"),
        ("Server-Side", "File upload vulnerabilities"),
        ("Server-Side", "Insecure deserialization"),
        ("Server-Side", "Race conditions"),
        ("Server-Side", "Open redirect"),
        ("Server-Side", "HTTP request smuggling"),
        ("Configuration", "Security headers review"),
        ("Configuration", "Cookie flags (Secure, HttpOnly, SameSite)"),
        ("Configuration", "HTTPS enforcement / mixed content"),
        ("Configuration", "Information disclosure (stack traces, debug)"),
        ("Configuration", "Sensitive data in responses"),
        ("Configuration", "Rate limiting / DoS resilience"),
        ("Configuration", "Error handling / verbose errors"),
        ("Configuration", "Subdomain takeover"),
        ("API Security", "API endpoint enumeration"),
        ("API Security", "Mass assignment / parameter tampering"),
        ("API Security", "Rate limiting on API"),
        ("API Security", "API versioning issues"),
        ("API Security", "GraphQL introspection / batching"),
        ("API Security", "Excessive data exposure"),
        ("Business Logic", "Price manipulation"),
        ("Business Logic", "Coupon/promo code abuse"),
        ("Business Logic", "Feature abuse / workflow bypass"),
        ("Business Logic", "Payment flow manipulation"),
        ("Business Logic", "Signup/invite abuse"),
        # Extra item for auto-tick from nuclei/import_scan
        ("Reconnaissance", "Vulnerability Identification"),
    ]
    return [MethodologyItem(category=c, name=n) for c, n in raw]


def format_methodology_block(items: list[MethodologyItem]) -> str:
    """Render checklist progress for system-prompt injection."""
    if not items:
        return ""
    checked = sum(1 for i in items if i.checked)
    lines = [f"## METHODOLOGY CHECKLIST ({checked}/{len(items)} complete)"]
    cat: str | None = None
    for item in items:
        if item.category != cat:
            cat = item.category
            lines.append(f"\n### {cat}")
        mark = "x" if item.checked else " "
        note = f" — {item.notes}" if item.notes else ""
        lines.append(f"- [{mark}] {item.name}{note}")
    return "\n".join(lines)


def auto_tick_for_tool(tool_name: str, preview: str = "") -> str | None:
    """Return methodology item name to tick for a tool call, or None."""
    if tool_name in _AUTO_TICK_TOOLS:
        return _AUTO_TICK_TOOLS[tool_name]
    if tool_name == "bash" and preview:
        for pattern, item_name in _AUTO_TICK_PATTERNS:
            if pattern.search(preview):
                return item_name
    if tool_name == "record_finding":
        return "Vulnerability Identification"
    return None
