use std::net::SocketAddr;

use axum::Router;
use serde::Serialize;
use sqlx::postgres::PgPoolOptions;
use tower::ServiceBuilder;
use tower_http::trace::TraceLayer;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};
use utoipa::OpenApi;
use utoipa_swagger_ui::SwaggerUi;

mod auth;
mod error;
mod middleware;
mod privacy;
mod routes;
mod security_profile;
mod state;

#[derive(OpenApi)]
#[openapi(
    info(
        title = "Kura Training API",
        version = "0.1.0",
        description = "Agent-first API for training, nutrition, and health data. Built for AI agents, not humans."
    ),
    paths(
        routes::health::health_check,
        routes::agent::get_agent_capabilities,
        routes::agent::get_agent_context,
        routes::agent::list_observation_drafts,
        routes::agent::get_observation_draft,
        routes::agent::promote_observation_draft,
        routes::agent::resolve_observation_draft_as_observation,
        routes::agent::get_event_evidence_lineage,
        routes::agent::resolve_visualization,
        routes::agent::write_with_proof,
        routes::semantic::resolve_semantic_terms,
        routes::events::create_event,
        routes::events::create_events_batch,
        routes::events::simulate_events,
        routes::events::list_events,
        routes::imports::create_import_job,
        routes::imports::get_import_job,
        routes::provider_connections::list_provider_connections,
        routes::provider_connections::upsert_provider_connection,
        routes::provider_connections::revoke_provider_connection,
        routes::auth::register,
        routes::auth::authorize_form,
        routes::auth::authorize_submit,
        routes::auth::token,
        routes::auth::forgot_password,
        routes::auth::reset_password,
        routes::auth::reactivate_account,
        routes::auth::device_authorize,
        routes::auth::device_verify_form,
        routes::auth::device_verify_submit,
        routes::auth::device_token,
        routes::auth::oidc_login,
        routes::auth::supabase_login,
        routes::projections::snapshot,
        routes::projections::get_projection,
        routes::projections::list_projections,
        routes::projection_rules::list_projection_rules,
        routes::projection_rules::validate_projection_rule,
        routes::projection_rules::preview_projection_rule,
        routes::projection_rules::apply_projection_rule,
        routes::projection_rules::archive_projection_rule,
        routes::system::get_system_config,
        routes::account::delete_own_account,
        routes::account::update_login_email,
        routes::account::get_analysis_subject,
        routes::account::admin_delete_user,
        routes::account::admin_support_reidentify,
        routes::security::get_kill_switch_status,
        routes::security::activate_kill_switch,
        routes::security::deactivate_kill_switch,
        routes::security::list_kill_switch_audit,
        routes::security::list_security_abuse_telemetry,
        routes::security::get_security_profile_rollout,
        routes::security::update_security_profile_rollout,
        routes::security::upsert_security_profile_override,
        routes::security::delete_security_profile_override,
        routes::security::record_rollout_decision,
        routes::security::list_rollout_decisions,
        routes::security::get_security_guardrail_dashboard,
        routes::agent_telemetry::get_agent_telemetry_overview,
        routes::agent_telemetry::get_agent_telemetry_anomalies,
        routes::agent_telemetry::list_agent_telemetry_signals,
    ),
    components(schemas(
        HealthResponse,
        routes::account::AccountDeletedResponse,
        routes::account::DeleteOwnAccountRequest,
        routes::account::AccountDeletionScheduledResponse,
        routes::account::UpdateLoginEmailRequest,
        routes::account::UpdateLoginEmailResponse,
        routes::account::AnalysisSubjectResponse,
        routes::account::SupportReidentifyRequest,
        routes::account::SupportReidentifyResponse,
        routes::agent::AgentCapabilitiesResponse,
        routes::agent::AgentFallbackContract,
        routes::agent::AgentUpgradePhase,
        routes::agent::AgentUpgradePolicy,
        routes::agent::AgentVerificationContract,
        routes::agent::AgentContextMeta,
        routes::agent::AgentContextSystemContract,
        routes::agent::AgentContextResponse,
        routes::agent::AgentObservationsDraftContext,
        routes::agent::AgentObservationDraftPreview,
        routes::agent::AgentObservationDraftListResponse,
        routes::agent::AgentObservationDraftListItem,
        routes::agent::AgentObservationDraftDetailResponse,
        routes::agent::AgentObservationDraftDetail,
        routes::agent::AgentObservationDraftPromoteRequest,
        routes::agent::AgentObservationDraftPromoteResponse,
        routes::agent::AgentObservationDraftResolveRequest,
        routes::agent::AgentObservationDraftResolveResponse,
        routes::agent::AgentReadAfterWriteTarget,
        routes::agent::AgentWriteWithProofRequest,
        routes::agent::AgentWriteReceipt,
        routes::agent::AgentReadAfterWriteCheck,
        routes::agent::AgentWriteVerificationSummary,
        routes::agent::AgentWriteClaimGuard,
        routes::agent::AgentWriteWithProofResponse,
        routes::agent::AgentEvidenceClaim,
        routes::agent::AgentEventEvidenceResponse,
        routes::semantic::SemanticDomain,
        routes::semantic::SemanticConfidenceBand,
        routes::semantic::SemanticProviderInfo,
        routes::semantic::SemanticResolveRequest,
        routes::semantic::SemanticResolveQuery,
        routes::semantic::SemanticResolveResponse,
        routes::semantic::SemanticResolveResult,
        routes::semantic::SemanticResolveCandidate,
        routes::semantic::SemanticResolveProvenance,
        routes::semantic::SemanticResolveMeta,
        kura_core::error::ApiError,
        kura_core::events::Event,
        kura_core::events::EventMetadata,
        kura_core::events::CreateEventRequest,
        kura_core::events::BatchCreateEventsRequest,
        kura_core::events::EventWarning,
        kura_core::events::CreateEventResponse,
        kura_core::events::BatchCreateEventsResponse,
        kura_core::events::BatchEventWarning,
        kura_core::events::PaginatedResponse<kura_core::events::Event>,
        kura_core::events::SimulateEventsRequest,
        kura_core::events::SimulateEventsResponse,
        kura_core::events::ProjectionImpact,
        kura_core::events::ProjectionImpactChange,
        routes::imports::CreateImportJobRequest,
        routes::imports::CreateImportJobResponse,
        routes::imports::ImportJobStatusResponse,
        routes::provider_connections::UpsertProviderConnectionRequest,
        routes::provider_connections::RevokeProviderConnectionRequest,
        routes::provider_connections::ProviderConnectionAdapterContext,
        routes::provider_connections::ProviderConnectionAuditMeta,
        routes::provider_connections::ProviderConnectionResponse,
        routes::auth::RegisterRequest,
        routes::auth::RegisterResponse,
        routes::auth::ForgotPasswordRequest,
        routes::auth::ForgotPasswordResponse,
        routes::auth::ResetPasswordRequest,
        routes::auth::ResetPasswordResponse,
        routes::auth::ReactivateAccountRequest,
        routes::auth::TokenRequest,
        routes::auth::TokenResponse,
        routes::auth::DeviceAuthorizeRequest,
        routes::auth::DeviceAuthorizeResponse,
        routes::auth::DeviceTokenRequest,
        routes::auth::DeviceVerifySubmit,
        routes::auth::OidcLoginRequest,
        routes::auth::SupabaseLoginRequest,
        kura_core::projections::Projection,
        kura_core::projections::ProjectionResponse,
        kura_core::projections::ProjectionMeta,
        kura_core::projections::ProjectionFreshness,
        kura_core::projections::ProjectionFreshnessStatus,
        routes::projection_rules::ProjectionRuleItem,
        routes::projection_rules::ProjectionRulesResponse,
        routes::projection_rules::ProjectionRuleDraft,
        routes::projection_rules::ProjectionRuleDraftRequest,
        routes::projection_rules::ApplyProjectionRuleRequest,
        routes::projection_rules::ProjectionRuleValidationResponse,
        routes::projection_rules::ProjectionRulePreviewEventType,
        routes::projection_rules::ProjectionRulePreviewField,
        routes::projection_rules::ProjectionRulePreviewCategory,
        routes::projection_rules::ProjectionRulePreviewResponse,
        routes::projection_rules::ProjectionRuleApplyStatus,
        routes::projection_rules::ProjectionRuleApplyResponse,
        routes::projection_rules::ProjectionRuleArchiveResponse,
        routes::system::SystemConfigResponse,
        routes::security::KillSwitchStatusResponse,
        routes::security::ActivateKillSwitchRequest,
        routes::security::DeactivateKillSwitchRequest,
        routes::security::KillSwitchAuditEvent,
        routes::security::SecurityAbuseTelemetryEvent,
        security_profile::SecurityProfile,
        security_profile::SecurityProfileRolloutConfig,
        routes::security::UpdateSecurityProfileRolloutRequest,
        routes::security::SecurityProfileRolloutStatusResponse,
        routes::security::UpsertSecurityProfileOverrideRequest,
        routes::security::SecurityProfileOverrideResponse,
        routes::security::DeleteSecurityProfileOverrideResponse,
        routes::security::RecordRolloutDecisionRequest,
        routes::security::RolloutDecisionRecord,
        routes::security::SecurityGuardrailProfileMetrics,
        routes::security::SecurityGuardrailDashboardResponse,
        routes::agent_telemetry::AdminAgentTelemetryOverviewResponse,
        routes::agent_telemetry::AdminAgentLearningSignalSummary,
        routes::agent_telemetry::AdminAgentRequestSummary,
        routes::agent_telemetry::AdminAgentQualityHealthSummary,
        routes::agent_telemetry::AdminAgentPlanUpdateSummary,
        routes::agent_telemetry::AdminAgentTelemetryAnomaliesResponse,
        routes::agent_telemetry::AdminAgentTelemetryAnomaly,
        routes::agent_telemetry::AdminAgentTelemetrySignalsResponse,
        routes::agent_telemetry::AdminAgentTelemetrySignalItem,
    )),
    modifiers(&SecurityAddon)
)]
struct ApiDoc;

struct SecurityAddon;

impl utoipa::Modify for SecurityAddon {
    fn modify(&self, openapi: &mut utoipa::openapi::OpenApi) {
        let components = openapi.components.get_or_insert_with(Default::default);
        components.add_security_scheme(
            "bearer_auth",
            utoipa::openapi::security::SecurityScheme::Http(utoipa::openapi::security::Http::new(
                utoipa::openapi::security::HttpAuthScheme::Bearer,
            )),
        );
    }
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct HealthResponse {
    pub status: String,
    pub version: String,
}

fn required_non_empty_env(name: &str, docs_hint: &str) -> String {
    match std::env::var(name) {
        Ok(value) if !value.trim().is_empty() => value,
        _ => panic!("{name} must be set and non-empty. {docs_hint}"),
    }
}

#[tokio::main]
async fn main() {
    // Load .env if present (dev only)
    let _ = dotenvy::dotenv();

    // Structured JSON logging
    tracing_subscriber::registry()
        .with(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "kura_api=debug,tower_http=debug".into()),
        )
        .with(tracing_subscriber::fmt::layer().json())
        .init();

    // Database connection
    let database_url = std::env::var("DATABASE_URL").expect("DATABASE_URL must be set");
    let _model_attestation_secret = required_non_empty_env(
        "KURA_AGENT_MODEL_ATTESTATION_SECRET",
        "Set the same shared secret in gateway signer and API verifier.",
    );

    let pool = PgPoolOptions::new()
        .max_connections(20)
        .connect(&database_url)
        .await
        .expect("Failed to connect to database");

    // Run migrations
    sqlx::migrate!("../migrations")
        .run(&pool)
        .await
        .expect("Failed to run migrations");

    let signup_gate = state::SignupGate::from_env();
    tracing::info!(signup_gate = ?signup_gate, "Signup gate configured");

    let app_state = state::AppState {
        db: pool,
        signup_gate,
    };

    // HTTPS enforcement (only when KURA_REQUIRE_HTTPS=true)
    let require_https = std::env::var("KURA_REQUIRE_HTTPS")
        .map(|v| v == "true")
        .unwrap_or(false);

    // CORS
    let cors_layer = middleware::cors::build_cors_layer();

    // Router with per-endpoint rate limiting on auth routes
    let app = Router::new()
        .merge(SwaggerUi::new("/swagger-ui").url("/api-doc/openapi.json", ApiDoc::openapi()))
        .merge(routes::health::router())
        .merge(
            routes::agent::router()
                .layer(middleware::rate_limit::projections_layer())
                .layer(middleware::adaptive_abuse::agent_layer(
                    app_state.db.clone(),
                ))
                .layer(middleware::kill_switch::agent_layer(app_state.db.clone())),
        )
        .merge(routes::semantic::router().layer(middleware::rate_limit::projections_layer()))
        .merge(
            routes::events::write_router()
                .layer(middleware::rate_limit::events_write_layer())
                .layer(middleware::upgrade_signal::legacy_contract_layer()),
        )
        .merge(
            routes::events::read_router()
                .layer(middleware::rate_limit::events_read_layer())
                .layer(middleware::upgrade_signal::legacy_contract_layer()),
        )
        .merge(routes::imports::router().layer(middleware::rate_limit::events_write_layer()))
        .merge(
            routes::provider_connections::router()
                .layer(middleware::rate_limit::projections_layer()),
        )
        .merge(
            routes::projections::router()
                .layer(middleware::rate_limit::projections_layer())
                .layer(middleware::upgrade_signal::legacy_contract_layer()),
        )
        .merge(
            routes::projection_rules::router().layer(middleware::rate_limit::projections_layer()),
        )
        .merge(routes::system::router().layer(middleware::rate_limit::projections_layer()))
        .merge(routes::auth::register_router().layer(middleware::rate_limit::register_layer()))
        .merge(routes::auth::authorize_router().layer(middleware::rate_limit::authorize_layer()))
        .merge(routes::auth::token_router().layer(middleware::rate_limit::token_layer()))
        .merge(routes::auth::device_router().layer(middleware::rate_limit::token_layer()))
        .merge(routes::auth::oidc_router().layer(middleware::rate_limit::authorize_layer()))
        .merge(
            routes::auth::supabase_login_router().layer(middleware::rate_limit::authorize_layer()),
        )
        .merge(routes::auth::email_login_router().layer(middleware::rate_limit::authorize_layer()))
        .merge(
            routes::auth::password_reset_router().layer(middleware::rate_limit::authorize_layer()),
        )
        .merge(
            routes::auth::reactivate_account_router()
                .layer(middleware::rate_limit::authorize_layer()),
        )
        .merge(routes::auth::me_router())
        .merge(routes::invite::public_router().layer(middleware::rate_limit::register_layer()))
        .merge(routes::invite::email_action_router())
        .merge(routes::account::self_router())
        .merge(routes::account::admin_router())
        .merge(routes::invite::admin_router())
        .merge(routes::security::admin_router())
        .merge(routes::agent_telemetry::admin_router())
        .layer(middleware::access_log::AccessLogLayer::new(
            app_state.db.clone(),
        ))
        .layer(auth::InjectAuthLayer::new(app_state.db.clone()))
        .layer(
            ServiceBuilder::new()
                .layer(TraceLayer::new_for_http())
                .option_layer(
                    require_https
                        .then(|| axum::middleware::from_fn(middleware::https::require_https)),
                )
                .layer(cors_layer),
        )
        .with_state(app_state);

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(3000);

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    tracing::info!("Kura API listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(
        listener,
        app.into_make_service_with_connect_info::<SocketAddr>(),
    )
    .await
    .unwrap();
}
