use serde_json::json;

use crate::util::{api_request, check_auth_configured, client, exit_error, raw_api_request, resolve_token};

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

pub async fn doctor(api_url: &str) -> i32 {
    let mut checks: Vec<serde_json::Value> = Vec::new();
    let mut overall = "ok";

    // 1. API reachable
    match raw_api_request(api_url, reqwest::Method::GET, "/health", None).await {
        Ok((status, body)) if status == 200 => {
            let version = body
                .get("version")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown");
            checks.push(json!({
                "name": "api_reachable",
                "status": "ok",
                "detail": format!("{api_url} (v{version})")
            }));
        }
        Ok((status, _)) => {
            overall = "error";
            checks.push(json!({
                "name": "api_reachable",
                "status": "error",
                "detail": format!("{api_url} returned HTTP {status}")
            }));
        }
        Err(e) => {
            overall = "error";
            checks.push(json!({
                "name": "api_reachable",
                "status": "error",
                "detail": format!("{api_url}: {e}")
            }));
            // No point continuing if API is unreachable
            let output = json!({ "checks": checks, "overall": overall });
            eprintln!("{}", serde_json::to_string_pretty(&output).unwrap());
            return 1;
        }
    }

    // 2. Auth configured
    match check_auth_configured() {
        Some((method, detail)) => {
            checks.push(json!({
                "name": "auth_configured",
                "status": "ok",
                "detail": format!("{method}: {detail}")
            }));
        }
        None => {
            if overall == "ok" {
                overall = "warn";
            }
            checks.push(json!({
                "name": "auth_configured",
                "status": "warn",
                "detail": "No credentials found. Run `kura login` or set KURA_API_KEY."
            }));
        }
    }

    // 3. Auth valid (only if configured)
    let token = match resolve_token(api_url).await {
        Ok(t) => {
            // Try an authenticated request
            match raw_api_request(api_url, reqwest::Method::GET, "/v1/projections/user_profile/me", Some(&t)).await {
                Ok((status, body)) if (200..=404).contains(&status) => {
                    // 200 = has profile, 404 = new user (both mean auth works)
                    let user_hint = if status == 200 {
                        body.get("user_id")
                            .and_then(|u| u.as_str())
                            .map(|u| format!("user_id: {u}"))
                            .unwrap_or_else(|| "authenticated".to_string())
                    } else {
                        "authenticated (new user, no profile yet)".to_string()
                    };
                    checks.push(json!({
                        "name": "auth_valid",
                        "status": "ok",
                        "detail": user_hint
                    }));
                }
                Ok((401, _)) => {
                    if overall == "ok" {
                        overall = "warn";
                    }
                    checks.push(json!({
                        "name": "auth_valid",
                        "status": "error",
                        "detail": "Token rejected (HTTP 401). Run `kura login` again."
                    }));
                }
                Ok((status, _)) => {
                    if overall == "ok" {
                        overall = "warn";
                    }
                    checks.push(json!({
                        "name": "auth_valid",
                        "status": "warn",
                        "detail": format!("Unexpected HTTP {status} on auth check")
                    }));
                }
                Err(e) => {
                    if overall == "ok" {
                        overall = "warn";
                    }
                    checks.push(json!({
                        "name": "auth_valid",
                        "status": "error",
                        "detail": format!("Auth check request failed: {e}")
                    }));
                }
            }
            Some(t)
        }
        Err(e) => {
            if overall == "ok" {
                overall = "warn";
            }
            checks.push(json!({
                "name": "auth_valid",
                "status": "warn",
                "detail": format!("{e}")
            }));
            None
        }
    };

    // 4. System config (workers write this at startup — empty = worker never ran)
    if let Some(ref t) = token {
        match raw_api_request(api_url, reqwest::Method::GET, "/v1/system/config", Some(t)).await {
            Ok((200, body)) => {
                let data = body.get("data").unwrap_or(&body);
                let dimensions = data
                    .get("dimensions")
                    .and_then(|d| d.as_object())
                    .map(|d| d.len())
                    .unwrap_or(0);
                let conventions = data
                    .get("event_conventions")
                    .and_then(|c| c.as_object())
                    .map(|c| c.len())
                    .unwrap_or(0);

                if dimensions > 0 {
                    checks.push(json!({
                        "name": "system_config",
                        "status": "ok",
                        "detail": format!("{dimensions} dimensions, {conventions} event conventions")
                    }));
                } else {
                    if overall == "ok" {
                        overall = "warn";
                    }
                    checks.push(json!({
                        "name": "system_config",
                        "status": "warn",
                        "detail": "Config exists but has no dimensions. Worker may not have started."
                    }));
                }
            }
            Ok((404, _)) => {
                if overall == "ok" {
                    overall = "warn";
                }
                checks.push(json!({
                    "name": "system_config",
                    "status": "warn",
                    "detail": "Not found. Worker has not started yet (worker writes system_config at startup)."
                }));
            }
            Ok((status, _)) => {
                if overall == "ok" {
                    overall = "warn";
                }
                checks.push(json!({
                    "name": "system_config",
                    "status": "warn",
                    "detail": format!("HTTP {status}")
                }));
            }
            Err(e) => {
                if overall == "ok" {
                    overall = "warn";
                }
                checks.push(json!({
                    "name": "system_config",
                    "status": "error",
                    "detail": format!("{e}")
                }));
            }
        }
    }

    // 5. Worker active — check if projections exist (workers create them)
    if let Some(ref t) = token {
        match raw_api_request(api_url, reqwest::Method::GET, "/v1/projections", Some(t)).await {
            Ok((200, body)) => {
                let count = body.as_array().map(|a| a.len()).unwrap_or(0);
                if count > 0 {
                    // Check freshness: find most recent updated_at
                    let newest = body
                        .as_array()
                        .and_then(|arr| {
                            arr.iter()
                                .filter_map(|p| p.get("updated_at").and_then(|u| u.as_str()))
                                .max()
                        })
                        .unwrap_or("unknown");

                    checks.push(json!({
                        "name": "worker_active",
                        "status": "ok",
                        "detail": format!("{count} projections, newest: {newest}")
                    }));
                } else {
                    if overall == "ok" {
                        overall = "warn";
                    }
                    checks.push(json!({
                        "name": "worker_active",
                        "status": "warn",
                        "detail": "No projections found. Worker may not be running or no events logged yet."
                    }));
                }
            }
            Ok((status, _)) => {
                if overall == "ok" {
                    overall = "warn";
                }
                checks.push(json!({
                    "name": "worker_active",
                    "status": "warn",
                    "detail": format!("HTTP {status} on snapshot check")
                }));
            }
            Err(e) => {
                if overall == "ok" {
                    overall = "warn";
                }
                checks.push(json!({
                    "name": "worker_active",
                    "status": "error",
                    "detail": format!("{e}")
                }));
            }
        }
    }

    let output = json!({
        "checks": checks,
        "overall": overall
    });

    if overall == "ok" {
        println!("{}", serde_json::to_string_pretty(&output).unwrap());
        0
    } else if overall == "warn" {
        println!("{}", serde_json::to_string_pretty(&output).unwrap());
        0
    } else {
        eprintln!("{}", serde_json::to_string_pretty(&output).unwrap());
        1
    }
}
