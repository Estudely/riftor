use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Debug, Deserialize)]
pub struct Request {
    pub id: u64,
    pub method: String,
    #[serde(default)]
    pub params: Value,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum Response {
    Success { id: u64, result: Value },
    Error { id: u64, error: ResponseError },
    EventError { error: ResponseError },
}

#[derive(Debug, Serialize)]
pub struct ResponseError {
    pub code: String,
    pub message: String,
}

#[derive(Debug, Serialize)]
pub struct Event {
    pub event: String,
    pub data: Value,
}

pub fn read_request(line: &str) -> Result<Request, serde_json::Error> {
    serde_json::from_str(line)
}

pub fn write_response(response: &Response) -> Result<String, serde_json::Error> {
    serde_json::to_string(response)
}

pub fn write_event(event: &Event) -> Result<String, serde_json::Error> {
    serde_json::to_string(event)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_read_request_valid() {
        let line = r#"{"id": 1, "method": "create_identity", "params": {}}"#;
        let req = read_request(line).unwrap();
        assert_eq!(req.id, 1);
        assert_eq!(req.method, "create_identity");
    }

    #[test]
    fn test_read_request_no_params() {
        let line = r#"{"id": 2, "method": "get_state"}"#;
        let req = read_request(line).unwrap();
        assert_eq!(req.id, 2);
        assert_eq!(req.method, "get_state");
    }

    #[test]
    fn test_read_request_invalid_json() {
        let line = "not json";
        assert!(read_request(line).is_err());
    }

    #[test]
    fn test_write_response_ok() {
        let resp = Response::Success {
            id: 1,
            result: serde_json::json!({"node_id": "abc"}),
        };
        let json = write_response(&resp).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed["id"], 1);
        assert_eq!(parsed["result"]["node_id"], "abc");
        assert!(parsed.get("error").is_none());
    }

    #[test]
    fn test_write_response_err() {
        let resp = Response::Error {
            id: 1,
            error: ResponseError {
                code: "TEST".to_string(),
                message: "something broke".into(),
            },
        };
        let json = write_response(&resp).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&json).unwrap();
        assert_eq!(parsed["id"], 1);
        assert_eq!(parsed["error"]["code"], "TEST");
        assert!(parsed.get("result").is_none());
    }
}
