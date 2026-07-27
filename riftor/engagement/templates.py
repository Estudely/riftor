"""Built-in engagement templates — playbooks applied with /template <name>.

Applying a template records the active template name in engagement meta; the
methodology text below is injected into the agent's context (see
engagement/injection.py).
"""

from __future__ import annotations

from dataclasses import dataclass

#: meta key under which the active template name is stored in engagement.db.
ACTIVE_TEMPLATE_META_KEY = "template"


@dataclass(frozen=True)
class Template:
    key: str
    label: str
    description: str
    tools: tuple[str, ...]  # suggested external tool chain (display only)
    methodology: str    # playbook injected into agent context


TEMPLATES: dict[str, Template] = {
    "webapp": Template(
        key="webapp",
        label="Web Application",
        description="Web app / website assessment",
        tools=("httpx", "ffuf", "nuclei", "sqlmap", "nikto"),
        methodology=(
            "Engagement type: WEB APPLICATION.\n"
            "- Passive recon: DNS, CT logs, wayback, public OSINT.\n"
            "- Active recon: enumerate hosts/vhosts, fingerprint stack (httpx/whatweb), "
            "map endpoints, find content (ffuf/gobuster), review JS for routes/secrets.\n"
            "- Vulnerability testing: authn/session, access control (IDOR), injection "
            "(SQLi/SSTI/XSS), SSRF, file upload; run nuclei for known CVEs.\n"
            "- Exploitation: chain confirmed vulns, demonstrate impact.\n"
            "- Documentation: record each confirmed issue with record_finding.\n"
            "Use list_methodology to track OWASP checklist progress."
        ),
    ),
    "api": Template(
        key="api",
        label="API",
        description="REST/GraphQL API assessment",
        tools=("httpx", "ffuf", "nuclei", "curl"),
        methodology=(
            "Engagement type: API.\n"
            "- Discovery: endpoints (docs/swagger/graphql introspection), "
            "auth scheme (JWT/OAuth/keys), enumerate methods + params.\n"
            "- Testing: broken object/function-level authz (BOLA/BFLA), mass "
            "assignment, injection, rate-limit + JWT flaws (alg=none, weak secret).\n"
            "- Exploitation: leverage token/object access to reach protected data.\n"
            "- Chain to account takeover or cross-tenant access where possible.\n"
            "Record each confirmed issue with record_finding (severity + evidence)."
        ),
    ),
    "network": Template(
        key="network",
        label="Network",
        description="Network / infrastructure assessment",
        tools=("nmap", "nuclei", "httpx"),
        methodology=(
            "Engagement type: NETWORK / INFRASTRUCTURE.\n"
            "- Recon: host discovery, port + service/version scan (nmap), "
            "banner-grab, identify exposed admin/mgmt services.\n"
            "- Testing: default/weak creds, known-CVE services (nuclei), "
            "exposed shares/DBs, unauthenticated endpoints.\n"
            "- Exploitation: validate service vulns, map lateral movement paths.\n"
            "Record services with record_service and issues with record_finding."
        ),
    ),
    "ad": Template(
        key="ad",
        label="Active Directory",
        description="Active Directory / Windows domain assessment",
        tools=("nmap", "nuclei"),
        methodology=(
            "Engagement type: ACTIVE DIRECTORY.\n"
            "- Recon: enumerate domain (users, groups, shares, GPOs), find DCs, "
            "spot AS-REP-roastable and Kerberoastable accounts.\n"
            "- Testing: password spray (lockout-aware), roast tickets, hunt for "
            "creds in shares/SYSVOL, check ACL misconfigs and delegation.\n"
            "- Exploitation: path to Domain Admin (DCSync, delegation abuse); "
            "document the chain.\n"
            "Record each confirmed issue with record_finding (severity + evidence)."
        ),
    ),
}
