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
mod routes;
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
        routes::agent::get_agent_context,
        routes::semantic::resolve_semantic_terms,
        routes::events::create_event,
        routes::events::create_events_batch,
        routes::events::simulate_events,
        routes::events::list_events,
        routes::auth::register,
        routes::auth::authorize_form,
        routes::auth::authorize_submit,
        routes::auth::token,
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
        routes::account::admin_delete_user,
    ),
    components(schemas(
        HealthResponse,
        routes::account::AccountDeletedResponse,
        routes::agent::AgentContextMeta,
        routes::agent::AgentContextResponse,
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
        routes::auth::RegisterRequest,
        routes::auth::RegisterResponse,
        routes::auth::TokenRequest,
        routes::auth::TokenResponse,
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
            utoipa::openapi::security::SecurityScheme::Http(
                utoipa::openapi::security::Http::new(
                    utoipa::openapi::security::HttpAuthScheme::Bearer,
                ),
            ),
        );
    }
}

#[derive(Serialize, utoipa::ToSchema)]
pub struct HealthResponse {
    pub status: String,
    pub version: String,
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
    let database_url = std::env::var("DATABASE_URL")
        .expect("DATABASE_URL must be set");

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

    let app_state = state::AppState { db: pool };

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
        .merge(routes::agent::router().layer(middleware::rate_limit::projections_layer()))
        .merge(routes::semantic::router().layer(middleware::rate_limit::projections_layer()))
        .merge(routes::events::write_router().layer(middleware::rate_limit::events_write_layer()))
        .merge(routes::events::read_router().layer(middleware::rate_limit::events_read_layer()))
        .merge(routes::projections::router().layer(middleware::rate_limit::projections_layer()))
        .merge(
            routes::projection_rules::router().layer(middleware::rate_limit::projections_layer()),
        )
        .merge(routes::system::router().layer(middleware::rate_limit::projections_layer()))
        .merge(routes::auth::register_router().layer(middleware::rate_limit::register_layer()))
        .merge(routes::auth::authorize_router().layer(middleware::rate_limit::authorize_layer()))
        .merge(routes::auth::token_router().layer(middleware::rate_limit::token_layer()))
        .merge(routes::account::self_router())
        .merge(routes::account::admin_router())
        .layer(middleware::access_log::AccessLogLayer::new(
            app_state.db.clone(),
        ))
        .layer(auth::InjectAuthLayer::new(app_state.db.clone()))
        .layer(
            ServiceBuilder::new()
                .layer(TraceLayer::new_for_http())
                .option_layer(require_https.then(|| {
                    axum::middleware::from_fn(middleware::https::require_https)
                }))
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
