use serde_json::json;

use crate::util::{api_request, client, exit_error};

pub async fn config(api_url: &str, token: &str) -> i32 {
    api_request(
        api_url,
        reqwest::Method::GET,
        "/v1/system/config",
        Some(token),
        None,
        &[],
        &[],
        false,
        false,
    )
    .await
}

pub async fn snapshot(api_url: &str, token: &str) -> i32 {
    api_request(
        api_url,
        reqwest::Method::GET,
        "/v1/projections",
        Some(token),
        None,
        &[],
        &[],
        false,
        false,
    )
    .await
}

pub async fn discover(api_url: &str, endpoints_only: bool) -> i32 {
    let resp = match client()
        .get(format!("{api_url}/api-doc/openapi.json"))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            let err = json!({
                "error": "connection_error",
                "message": format!("{e}"),
                "docs_hint": "Is the API server running? Check KURA_API_URL."
            });
            eprintln!("{}", serde_json::to_string_pretty(&err).unwrap());
            return 3;
        }
    };

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        let body: serde_json::Value = resp.json().await.unwrap_or(json!({"error": "unknown"}));
        eprintln!("{}", serde_json::to_string_pretty(&body).unwrap());
        return if (400..500).contains(&status) { 1 } else { 2 };
    }

    let spec: serde_json::Value = match resp.json().await {
        Ok(v) => v,
        Err(e) => exit_error(&format!("Failed to parse OpenAPI spec: {e}"), None),
    };

    if !endpoints_only {
        println!("{}", serde_json::to_string_pretty(&spec).unwrap());
        return 0;
    }

    // Extract compact endpoint list from OpenAPI paths
    let mut endpoints = Vec::new();

    if let Some(paths) = spec.get("paths").and_then(|p| p.as_object()) {
        for (path, methods) in paths {
            if let Some(methods_obj) = methods.as_object() {
                for (method, details) in methods_obj {
                    // Skip OpenAPI metadata keys
                    if !["get", "post", "put", "delete", "patch", "head", "options"]
                        .contains(&method.as_str())
                    {
                        continue;
                    }

                    let summary = details
                        .get("summary")
                        .and_then(|s| s.as_str())
                        .unwrap_or("");

                    let requires_auth = details
                        .get("security")
                        .map(|s| !s.as_array().map(|a| a.is_empty()).unwrap_or(true))
                        .unwrap_or(false);

                    endpoints.push(json!({
                        "method": method.to_uppercase(),
                        "path": path,
                        "summary": summary,
                        "auth": requires_auth
                    }));
                }
            }
        }
    }

    // Sort by path, then method
    endpoints.sort_by(|a, b| {
        let pa = a["path"].as_str().unwrap_or("");
        let pb = b["path"].as_str().unwrap_or("");
        pa.cmp(pb).then_with(|| {
            let ma = a["method"].as_str().unwrap_or("");
            let mb = b["method"].as_str().unwrap_or("");
            ma.cmp(mb)
        })
    });

    let output = json!({
        "endpoints": endpoints,
        "total": endpoints.len(),
        "base_url": api_url
    });

    println!("{}", serde_json::to_string_pretty(&output).unwrap());
    0
}
