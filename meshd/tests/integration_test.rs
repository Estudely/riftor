use std::io::{BufRead, BufReader, Write};
use std::process::{Command, Stdio};
use serde_json::Value;

#[test]
fn test_ping_via_stdio() {
    let mut child = Command::new(env!("CARGO_BIN_EXE_riftor-meshd"))
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .expect("Failed to start meshd");

    let mut stdin = child.stdin.take().unwrap();
    let stdout = child.stdout.take().unwrap();
    let mut reader = BufReader::new(stdout);

    // 1. Ping
    writeln!(stdin, r#"{{"id": 1, "method": "ping", "params": {{}}}}"#).unwrap();
    let mut line = String::new();
    reader.read_line(&mut line).unwrap();
    let resp: Value = serde_json::from_str(line.trim()).unwrap();
    assert_eq!(resp["id"], 1);
    assert_eq!(resp["result"]["pong"], true);

    // 2. Create identity
    writeln!(stdin, r#"{{"id": 2, "method": "create_identity", "params": {{}}}}"#).unwrap();
    line.clear();
    reader.read_line(&mut line).unwrap();
    let resp: Value = serde_json::from_str(line.trim()).unwrap();
    assert_eq!(resp["id"], 2);
    assert!(resp["result"]["node_id"].is_string());

    // 3. Create engagement
    writeln!(stdin, r#"{{"id": 3, "method": "create_engagement", "params": {{"name": "test-eng"}}}}"#).unwrap();
    line.clear();
    reader.read_line(&mut line).unwrap();
    let resp: Value = serde_json::from_str(line.trim()).unwrap();
    assert_eq!(resp["id"], 3);
    let eng_id = resp["result"]["id"].as_str().unwrap().to_string();
    assert!(!eng_id.is_empty());

    // 4. Generate invite
    let invite_cmd = format!(r#"{{"id": 4, "method": "generate_invite", "params": {{"engagement_id": "{}"}}}}"#, eng_id);
    writeln!(stdin, "{}", invite_cmd).unwrap();
    line.clear();
    reader.read_line(&mut line).unwrap();
    let resp: Value = serde_json::from_str(line.trim()).unwrap();
    assert_eq!(resp["id"], 4);
    let invite = resp["result"]["invite"].as_str().unwrap().to_string();
    assert!(!invite.is_empty());

    // 5. Submit a finding
    let find_json = format!(
        r#"{{"id": 5, "method": "submit", "params": {{"engagement_id": "{}", "submission": {{"type": "finding", "data": {{"title": "SQLi in /login", "severity": "high", "target": "10.0.0.5", "vuln_class": "sqli", "description": "Found SQL injection"}}}}}}}}"#,
        eng_id
    );
    writeln!(stdin, "{}", find_json).unwrap();
    line.clear();
    reader.read_line(&mut line).unwrap();
    let resp: Value = serde_json::from_str(line.trim()).unwrap();
    assert_eq!(resp["id"], 5);
    assert!(resp["result"]["submission_id"].is_string());

    // 6. Get state
    let state_cmd = format!(r#"{{"id": 6, "method": "get_state", "params": {{"engagement_id": "{}"}}}}"#, eng_id);
    writeln!(stdin, "{}", state_cmd).unwrap();
    line.clear();
    reader.read_line(&mut line).unwrap();
    let resp: Value = serde_json::from_str(line.trim()).unwrap();
    assert_eq!(resp["id"], 6);
    assert!(resp["result"]["findings"].is_array());

    // 7. Unknown method returns error
    writeln!(stdin, r#"{{"id": 7, "method": "do_the_thing", "params": {{}}}}"#).unwrap();
    line.clear();
    reader.read_line(&mut line).unwrap();
    let resp: Value = serde_json::from_str(line.trim()).unwrap();
    assert_eq!(resp["id"], 7);
    assert_eq!(resp["error"]["code"], "UNKNOWN_METHOD");

    drop(stdin);
    child.wait().unwrap();
}
