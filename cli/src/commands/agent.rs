use clap::Args;
use serde_json::json;

use crate::util::{api_request, exit_error, read_json_from_file};

#[derive(Args)]
pub struct WriteWithProofArgs {
    /// JSON file containing events array or {"events":[...]} (use '-' for stdin)
    #[arg(
        long,
        required_unless_present = "request_file",
        conflicts_with = "request_file"
    )]
    pub events_file: Option<String>,

    /// Read-after-write target in projection_type:key format (repeatable)
    #[arg(
        long,
        required_unless_present = "request_file",
        conflicts_with = "request_file"
    )]
    pub target: Vec<String>,

    /// Max verification wait in milliseconds (100..10000)
    #[arg(long)]
    pub verify_timeout_ms: Option<u64>,

    /// Full request payload JSON file for /v1/agent/write-with-proof
    #[arg(long, conflicts_with_all = ["events_file", "target", "verify_timeout_ms"])]
    pub request_file: Option<String>,
}

pub async fn write_with_proof(api_url: &str, token: Option<&str>, args: WriteWithProofArgs) -> i32 {
    let body = if let Some(file) = args.request_file.as_deref() {
        load_full_request(file)
    } else {
        build_request_from_events_and_targets(
            args.events_file.as_deref().unwrap_or(""),
            &args.target,
            args.verify_timeout_ms,
        )
    };

    api_request(
        api_url,
        reqwest::Method::POST,
        "/v1/agent/write-with-proof",
        token,
        Some(body),
        &[],
        &[],
        false,
        false,
    )
    .await
}

fn load_full_request(path: &str) -> serde_json::Value {
    let payload = match read_json_from_file(path) {
        Ok(v) => v,
        Err(e) => exit_error(
            &e,
            Some(
                "Provide JSON with events, read_after_write_targets, and optional verify_timeout_ms.",
            ),
        ),
    };
    if payload
        .get("events")
        .and_then(|value| value.as_array())
        .is_none()
    {
        exit_error(
            "request payload must include an events array",
            Some(
                "Use --request-file with {\"events\": [...], \"read_after_write_targets\": [...]}",
            ),
        );
    }
    if payload
        .get("read_after_write_targets")
        .and_then(|value| value.as_array())
        .is_none()
    {
        exit_error(
            "request payload must include read_after_write_targets array",
            Some("Set read_after_write_targets to [{\"projection_type\":\"...\",\"key\":\"...\"}]"),
        );
    }
    payload
}

fn build_request_from_events_and_targets(
    events_file: &str,
    raw_targets: &[String],
    verify_timeout_ms: Option<u64>,
) -> serde_json::Value {
    if raw_targets.is_empty() {
        exit_error(
            "--target is required when --request-file is not used",
            Some("Repeat --target projection_type:key for read-after-write checks."),
        );
    }

    let parsed_targets = parse_targets(raw_targets);
    let events_payload = match read_json_from_file(events_file) {
        Ok(v) => v,
        Err(e) => exit_error(
            &e,
            Some("Provide --events-file as JSON array or object with events array."),
        ),
    };

    let events = extract_events_array(events_payload);
    build_write_with_proof_request(events, parsed_targets, verify_timeout_ms)
}

fn parse_targets(raw_targets: &[String]) -> Vec<serde_json::Value> {
    raw_targets
        .iter()
        .map(|raw| {
            let (projection_type, key) = raw.split_once(':').unwrap_or_else(|| {
                exit_error(
                    &format!("Invalid --target '{raw}'"),
                    Some("Use format projection_type:key, e.g. user_profile:me"),
                )
            });
            let projection_type = projection_type.trim();
            let key = key.trim();
            if projection_type.is_empty() || key.is_empty() {
                exit_error(
                    &format!("Invalid --target '{raw}'"),
                    Some("projection_type and key must both be non-empty."),
                );
            }
            json!({
                "projection_type": projection_type,
                "key": key,
            })
        })
        .collect()
}

fn extract_events_array(events_payload: serde_json::Value) -> Vec<serde_json::Value> {
    if let Some(events) = events_payload.as_array() {
        return events.to_vec();
    }
    if let Some(events) = events_payload
        .get("events")
        .and_then(|value| value.as_array())
    {
        return events.to_vec();
    }
    exit_error(
        "events payload must be an array or object with events array",
        Some("Example: --events-file events.json where file is [{...}] or {\"events\": [{...}]}"),
    );
}

fn build_write_with_proof_request(
    events: Vec<serde_json::Value>,
    parsed_targets: Vec<serde_json::Value>,
    verify_timeout_ms: Option<u64>,
) -> serde_json::Value {
    let mut request = json!({
        "events": events,
        "read_after_write_targets": parsed_targets,
    });
    if let Some(timeout) = verify_timeout_ms {
        request["verify_timeout_ms"] = json!(timeout);
    }
    request
}

#[cfg(test)]
mod tests {
    use super::{build_write_with_proof_request, extract_events_array, parse_targets};
    use serde_json::json;

    #[test]
    fn parse_targets_accepts_projection_type_key_format() {
        let parsed = parse_targets(&[
            "user_profile:me".to_string(),
            "training_timeline:overview".to_string(),
        ]);
        assert_eq!(parsed[0]["projection_type"], "user_profile");
        assert_eq!(parsed[0]["key"], "me");
        assert_eq!(parsed[1]["projection_type"], "training_timeline");
        assert_eq!(parsed[1]["key"], "overview");
    }

    #[test]
    fn extract_events_array_supports_plain_array() {
        let events = extract_events_array(json!([
            {"event_type":"set.logged"},
            {"event_type":"metric.logged"}
        ]));
        assert_eq!(events.len(), 2);
    }

    #[test]
    fn extract_events_array_supports_object_wrapper() {
        let events = extract_events_array(json!({
            "events": [{"event_type":"set.logged"}]
        }));
        assert_eq!(events.len(), 1);
    }

    #[test]
    fn build_write_with_proof_request_serializes_expected_fields() {
        let request = build_write_with_proof_request(
            vec![json!({"event_type":"set.logged"})],
            vec![json!({"projection_type":"user_profile","key":"me"})],
            Some(1200),
        );
        assert_eq!(request["events"].as_array().unwrap().len(), 1);
        assert_eq!(
            request["read_after_write_targets"].as_array().unwrap().len(),
            1
        );
        assert_eq!(request["verify_timeout_ms"], 1200);
    }
}
