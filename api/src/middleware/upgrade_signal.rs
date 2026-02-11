use std::collections::HashMap;
use std::convert::Infallible;
use std::future::Future;
use std::pin::Pin;
use std::sync::{LazyLock, Mutex};
use std::task::{Context, Poll};
use std::time::{Duration, Instant};

use axum::extract::Request;
use axum::response::{IntoResponse, Response};
use tower::{Layer, Service, ServiceExt};

const UPGRADE_SIGNAL_COOLDOWN_SECONDS: u64 = 300;

static SIGNAL_EMISSION_CACHE: LazyLock<Mutex<HashMap<String, Instant>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

#[derive(Clone)]
pub struct LegacyContractUpgradeSignalLayer;

pub fn legacy_contract_layer() -> LegacyContractUpgradeSignalLayer {
    LegacyContractUpgradeSignalLayer
}

impl<S> Layer<S> for LegacyContractUpgradeSignalLayer {
    type Service = LegacyContractUpgradeSignalService<S>;

    fn layer(&self, inner: S) -> Self::Service {
        LegacyContractUpgradeSignalService { inner }
    }
}

#[derive(Clone)]
pub struct LegacyContractUpgradeSignalService<S> {
    inner: S,
}

impl<S> Service<Request> for LegacyContractUpgradeSignalService<S>
where
    S: Service<Request, Response = Response, Error = Infallible> + Clone + Send + 'static,
    S::Future: Send + 'static,
{
    type Response = Response;
    type Error = Infallible;
    type Future = Pin<Box<dyn Future<Output = Result<Response, Infallible>> + Send>>;

    fn poll_ready(&mut self, cx: &mut Context<'_>) -> Poll<Result<(), Self::Error>> {
        self.inner.poll_ready(cx)
    }

    fn call(&mut self, req: Request) -> Self::Future {
        let not_ready = self.inner.clone();
        let ready = std::mem::replace(&mut self.inner, not_ready);

        let method = req.method().as_str().to_string();
        let path = req.uri().path().to_string();
        let user_agent = req
            .headers()
            .get("user-agent")
            .and_then(|value| value.to_str().ok())
            .unwrap_or("")
            .to_string();
        let signal = legacy_signal_for(&method, &path);

        Box::pin(async move {
            let mut response = ready.oneshot(req).await.into_response();

            if let Some(signal) = signal {
                let rate_key = format!("{}|{}", signal.signal_id, user_agent);
                if should_emit_upgrade_signal(&rate_key, Instant::now()) {
                    response.headers_mut().insert(
                        "x-kura-upgrade-signal",
                        signal
                            .signal_id
                            .parse()
                            .expect("valid upgrade signal header"),
                    );
                    response.headers_mut().insert(
                        "x-kura-upgrade-phase",
                        signal
                            .compatibility_phase
                            .parse()
                            .expect("valid upgrade phase header"),
                    );
                    response.headers_mut().insert(
                        "x-kura-upgrade-endpoint",
                        signal
                            .recommended_endpoint
                            .parse()
                            .expect("valid upgrade endpoint header"),
                    );
                    response.headers_mut().insert(
                        "x-kura-upgrade-action",
                        signal
                            .action_hint
                            .parse()
                            .expect("valid upgrade action header"),
                    );
                    response.headers_mut().insert(
                        "x-kura-upgrade-reason",
                        signal.reason.parse().expect("valid upgrade reason header"),
                    );
                    response.headers_mut().insert(
                        "x-kura-upgrade-docs",
                        "/v1/agent/capabilities"
                            .parse()
                            .expect("valid upgrade docs header"),
                    );

                    tracing::info!(
                        signal_id = signal.signal_id,
                        phase = signal.compatibility_phase,
                        recommended_endpoint = signal.recommended_endpoint,
                        method = method,
                        path = path,
                        user_agent = user_agent,
                        "legacy_contract_upgrade_signal_emitted"
                    );
                }
            }

            Ok(response)
        })
    }
}

#[derive(Debug, Clone, Copy)]
struct UpgradeSignal {
    signal_id: &'static str,
    compatibility_phase: &'static str,
    recommended_endpoint: &'static str,
    action_hint: &'static str,
    reason: &'static str,
}

fn legacy_signal_for(method: &str, path: &str) -> Option<UpgradeSignal> {
    match (method, path) {
        ("POST", "/v1/events") | ("POST", "/v1/events/batch") => Some(UpgradeSignal {
            signal_id: "legacy_event_write_contract",
            compatibility_phase: "supported",
            recommended_endpoint: "/v1/agent/write-with-proof",
            action_hint: "Route agent writes through write-with-proof for receipts and claim guard.",
            reason: "Legacy write endpoints do not enforce read-after-write proof.",
        }),
        _ if method == "GET" && path.starts_with("/v1/projections") => Some(UpgradeSignal {
            signal_id: "legacy_projection_read_contract",
            compatibility_phase: "supported",
            recommended_endpoint: "/v1/agent/context",
            action_hint: "Route agent reads through context bundle for ranked dimensions and system metadata.",
            reason: "Direct projection reads bypass agent bundle semantics.",
        }),
        _ => None,
    }
}

fn should_emit_upgrade_signal(key: &str, now: Instant) -> bool {
    let mut cache = SIGNAL_EMISSION_CACHE
        .lock()
        .expect("upgrade signal cache mutex should not be poisoned");
    should_emit_with_cache(&mut cache, key, now)
}

fn should_emit_with_cache(cache: &mut HashMap<String, Instant>, key: &str, now: Instant) -> bool {
    let cooldown = Duration::from_secs(UPGRADE_SIGNAL_COOLDOWN_SECONDS);
    match cache.get(key) {
        Some(last_emitted) if now.duration_since(*last_emitted) < cooldown => false,
        _ => {
            cache.insert(key.to_string(), now);
            true
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn legacy_signal_detects_event_write_paths() {
        let signal = legacy_signal_for("POST", "/v1/events").expect("signal should exist");
        assert_eq!(signal.recommended_endpoint, "/v1/agent/write-with-proof");

        let signal = legacy_signal_for("POST", "/v1/events/batch").expect("signal should exist");
        assert_eq!(signal.signal_id, "legacy_event_write_contract");
    }

    #[test]
    fn legacy_signal_detects_projection_read_paths() {
        let signal = legacy_signal_for("GET", "/v1/projections/user_profile/me")
            .expect("signal should exist");
        assert_eq!(signal.recommended_endpoint, "/v1/agent/context");
    }

    #[test]
    fn legacy_signal_ignores_non_legacy_paths() {
        assert!(legacy_signal_for("GET", "/v1/agent/context").is_none());
        assert!(legacy_signal_for("POST", "/v1/agent/write-with-proof").is_none());
        assert!(legacy_signal_for("GET", "/health").is_none());
    }

    #[test]
    fn signal_emission_cache_enforces_cooldown() {
        let mut cache = HashMap::new();
        let now = Instant::now();
        assert!(should_emit_with_cache(&mut cache, "key", now));
        assert!(!should_emit_with_cache(
            &mut cache,
            "key",
            now + Duration::from_secs(120)
        ));
        assert!(should_emit_with_cache(
            &mut cache,
            "key",
            now + Duration::from_secs(UPGRADE_SIGNAL_COOLDOWN_SECONDS + 1)
        ));
    }
}
