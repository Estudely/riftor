"""Built-in engagement templates — playbooks applied with /template <name>.

Applying a template sets the starting RIFT stage and records the active template
name in engagement meta; the methodology text below is injected into the agent's
context (see engagement/injection.py). The text lives here, in code, so templates
can be edited without migrating stored state.
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
    stage: str          # starting RIFT stage: R/I/F/T
    tools: list[str]    # suggested external tool chain (display only)
    methodology: str    # playbook injected into agent context


TEMPLATES: dict[str, Template] = {
    "webapp": Template(
        key="webapp",
        label="Web Application",
        description="Web app / website assessment",
        stage="R",
        tools=["httpx", "ffuf", "nuclei", "sqlmap", "nikto"],
        methodology=(
            "Engagement type: WEB APPLICATION.\n"
            "- Recon: enumerate hosts/vhosts, fingerprint stack (httpx/whatweb), "
            "map endpoints, find content (ffuf/gobuster), review JS for routes/secrets.\n"
            "- Intrusion: test authn/session, access control (IDOR), injection "
            "(SQLi/SSTI/XSS), SSRF, file upload; run nuclei for known CVEs.\n"
            "- Foothold: chain a working exploit, capture a session/credential.\n"
            "- Takeover: assess blast radius (data access, privilege escalation).\n"
            "Record each confirmed issue with record_finding (severity + evidence)."
        ),
    ),
    "api": Template(
        key="api",
        label="API",
        description="REST/GraphQL API assessment",
        stage="R",
        tools=["httpx", "ffuf", "nuclei", "curl"],
        methodology=(
            "Engagement type: API.\n"
            "- Recon: discover endpoints (docs/swagger/graphql introspection), "
            "auth scheme (JWT/OAuth/keys), enumerate methods + params.\n"
            "- Intrusion: test broken object/function-level authz (BOLA/BFLA), mass "
            "assignment, injection, rate-limit + JWT flaws (alg=none, weak secret).\n"
            "- Foothold: leverage a token/object access to reach protected data.\n"
            "- Takeover: chain to account takeover or cross-tenant access.\n"
            "Record each confirmed issue with record_finding (severity + evidence)."
        ),
    ),
    "network": Template(
        key="network",
        label="Network",
        description="Network / infrastructure assessment",
        stage="R",
        tools=["nmap", "nuclei", "httpx"],
        methodology=(
            "Engagement type: NETWORK / INFRASTRUCTURE.\n"
            "- Recon: host discovery, full port + service/version scan (nmap), "
            "banner-grab, identify exposed admin/mgmt services.\n"
            "- Intrusion: check default/weak creds, known-CVE services (nuclei), "
            "exposed shares/DBs, unauthenticated endpoints.\n"
            "- Foothold: exploit a service to get a shell or credential.\n"
            "- Takeover: pivot, escalate, map lateral movement paths.\n"
            "Record services with record_service and issues with record_finding."
        ),
    ),
    "ad": Template(
        key="ad",
        label="Active Directory",
        description="Active Directory / Windows domain assessment",
        stage="R",
        tools=["nmap", "nuclei"],
        methodology=(
            "Engagement type: ACTIVE DIRECTORY.\n"
            "- Recon: enumerate domain (users, groups, shares, GPOs), find DCs, "
            "spot AS-REP-roastable and Kerberoastable accounts.\n"
            "- Intrusion: password spray (lockout-aware), roast tickets, hunt for "
            "creds in shares/SYSVOL, check ACL misconfigs and delegation.\n"
            "- Foothold: authenticate as a captured principal; establish access.\n"
            "- Takeover: path to Domain Admin (DCSync, delegation abuse); document "
            "the chain.\n"
            "Record each confirmed issue with record_finding (severity + evidence)."
        ),
    ),
}
