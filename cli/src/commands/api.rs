use clap::Args;

use crate::util::{
    admin_surface_enabled, api_request, exit_error, is_admin_api_path, read_json_from_file,
    resolve_token,
};

#[derive(Args)]
pub struct ApiArgs {
    /// HTTP method (GET, POST, PUT, DELETE, PATCH)
    pub method: String,

    /// API path (e.g. /v1/events)
    pub path: String,

    /// Request body as JSON string
    #[arg(long, short = 'd')]
    pub data: Option<String>,

    /// Read request body from file (use '-' for stdin)
    #[arg(long, short = 'f', conflicts_with = "data")]
    pub data_file: Option<String>,

    /// Query parameters (repeatable: key=value)
    #[arg(long, short = 'q')]
    pub query: Vec<String>,

    /// Extra headers (repeatable: Key:Value)
    #[arg(long, short = 'H')]
    pub header: Vec<String>,

    /// Skip pretty-printing (raw JSON for piping)
    #[arg(long)]
    pub raw: bool,

    /// Include HTTP status and headers in response wrapper
    #[arg(long, short = 'i')]
    pub include: bool,

    /// Skip authentication (for public endpoints like /health)
    #[arg(long)]
    pub no_auth: bool,
}

pub async fn run(api_url: &str, args: ApiArgs) -> i32 {
    if is_admin_api_path(&args.path) && !admin_surface_enabled() {
        exit_error(
            "Admin API paths are disabled in CLI by default.",
            Some("Set KURA_ENABLE_ADMIN_SURFACE=1 only in trusted developer/admin sessions."),
        );
    }

    // Parse method
    let method = match args.method.to_uppercase().as_str() {
        "GET" => reqwest::Method::GET,
        "POST" => reqwest::Method::POST,
        "PUT" => reqwest::Method::PUT,
        "DELETE" => reqwest::Method::DELETE,
        "PATCH" => reqwest::Method::PATCH,
        "HEAD" => reqwest::Method::HEAD,
        "OPTIONS" => reqwest::Method::OPTIONS,
        other => exit_error(
            &format!("Unknown HTTP method: {other}"),
            Some("Supported methods: GET, POST, PUT, DELETE, PATCH, HEAD, OPTIONS"),
        ),
    };

    // Parse query parameters
    let mut query = Vec::new();
    for q in &args.query {
        match q.split_once('=') {
            Some((k, v)) => query.push((k.to_string(), v.to_string())),
            None => exit_error(
                &format!("Invalid query parameter: '{q}'"),
                Some("Format: key=value, e.g. --query event_type=set.logged"),
            ),
        }
    }

    // Parse extra headers
    let mut headers = Vec::new();
    for h in &args.header {
        match h.split_once(':') {
            Some((k, v)) => headers.push((k.trim().to_string(), v.trim().to_string())),
            None => exit_error(
                &format!("Invalid header: '{h}'"),
                Some("Format: Key:Value, e.g. --header Content-Type:application/json"),
            ),
        }
    }

    // Resolve body
    let body = if let Some(ref d) = args.data {
        match serde_json::from_str(d) {
            Ok(v) => Some(v),
            Err(e) => exit_error(
                &format!("Invalid JSON in --data: {e}"),
                Some("Provide valid JSON string"),
            ),
        }
    } else if let Some(ref f) = args.data_file {
        match read_json_from_file(f) {
            Ok(v) => Some(v),
            Err(e) => exit_error(&e, Some("Provide a valid JSON file or use '-' for stdin")),
        }
    } else {
        None
    };

    // Resolve auth
    let token = if args.no_auth {
        None
    } else {
        match resolve_token(api_url).await {
            Ok(t) => Some(t),
            Err(e) => exit_error(
                &e.to_string(),
                Some("Run `kura login`, set KURA_API_KEY, or use --no-auth for public endpoints"),
            ),
        }
    };

    api_request(
        api_url,
        method,
        &args.path,
        token.as_deref(),
        body,
        &query,
        &headers,
        args.raw,
        args.include,
    )
    .await
}

#[cfg(test)]
mod tests {
    #[test]
    fn test_query_parsing() {
        let input = "event_type=set.logged";
        let (k, v) = input.split_once('=').unwrap();
        assert_eq!(k, "event_type");
        assert_eq!(v, "set.logged");
    }

    #[test]
    fn test_header_parsing() {
        let input = "Content-Type: application/json";
        let (k, v) = input.split_once(':').unwrap();
        assert_eq!(k.trim(), "Content-Type");
        assert_eq!(v.trim(), "application/json");
    }

    #[test]
    fn test_method_parsing() {
        for m in &[
            "get", "GET", "Get", "post", "POST", "delete", "DELETE", "put", "patch",
        ] {
            let parsed = match m.to_uppercase().as_str() {
                "GET" => Some(reqwest::Method::GET),
                "POST" => Some(reqwest::Method::POST),
                "PUT" => Some(reqwest::Method::PUT),
                "DELETE" => Some(reqwest::Method::DELETE),
                "PATCH" => Some(reqwest::Method::PATCH),
                _ => None,
            };
            assert!(parsed.is_some(), "Failed to parse method: {m}");
        }
    }
}
