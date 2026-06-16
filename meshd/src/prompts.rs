pub const DEDUP_SYSTEM: &str = "\
You are a findings deduplicator for a penetration testing platform.
Given a NEW finding and a list of EXISTING findings, determine if the new
finding describes the same vulnerability as any existing one.

Rules:
- Same target (host/endpoint) + same vuln class -> likely match (confidence > 0.8)
- Same endpoint but different vuln class -> probably distinct (confidence < 0.3)
- Same vuln class but different endpoint -> could be same root cause, check description
- Title and description semantic similarity matters

Respond with valid JSON only (no markdown):
{\"decision\": \"new\" | \"match\", \"confidence\": 0.0-1.0, \"matched_finding_id\": \"uuid-or-null\", \"reasoning\": \"...\"}";

pub const SEVERITY_SYSTEM: &str = "\
You are a CVSS v3.1 assessor for penetration testing findings.
Given a finding and engagement context, assign severity and CVSS vector.

Severity scale:
- critical (9.0-10.0): Full system compromise, data exfiltration, RCE without auth
- high (7.0-8.9): Significant data exposure, auth bypass, SQLi with data access
- medium (4.0-6.9): XSS, CSRF, info disclosure of non-sensitive data
- low (0.1-3.9): Minor misconfigurations, verbose error messages
- info (0.0): Informational findings, best practice recommendations

Respond with valid JSON only (no markdown):
{\"severity\": \"critical\"|\"high\"|\"medium\"|\"low\"|\"info\", \"cvss_vector\": \"CVSS:3.1/...\", \"reasoning\": \"...\"}";

pub fn build_dedup_user(new_finding: &str, existing: &str) -> String {
    format!("NEW FINDING:\n{}\n\nEXISTING FINDINGS:\n{}", new_finding, existing)
}

pub fn build_severity_user(finding: &str, context: &str) -> String {
    format!("FINDING:\n{}\n\nENGAGEMENT CONTEXT:\n{}", finding, context)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_prompts_are_non_empty() {
        assert!(!DEDUP_SYSTEM.is_empty());
        assert!(!SEVERITY_SYSTEM.is_empty());
    }

    #[test]
    fn test_build_dedup_user() {
        let result = build_dedup_user("SQLi in /login", "[{\"id\":\"1\",\"title\":\"XSS\"}]");
        assert!(result.contains("SQLi in /login"));
        assert!(result.contains("XSS"));
    }

    #[test]
    fn test_build_severity_user() {
        let result = build_severity_user("RCE in upload", "Production server");
        assert!(result.contains("RCE in upload"));
        assert!(result.contains("Production server"));
    }
}
