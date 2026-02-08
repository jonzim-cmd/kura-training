use std::net::SocketAddr;

use axum::Router;
use serde::Serialize;
use sqlx::postgres::PgPoolOptions;
use tower_http::trace::TraceLayer;
use tracing_subscriber::{layer::SubscriberExt, util::SubscriberInitExt};
use utoipa::OpenApi;
use utoipa_swagger_ui::SwaggerUi;

mod auth;
mod error;
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
        routes::events::create_event,
        routes::events::create_events_batch,
        routes::events::list_events,
        routes::auth::register,
        routes::auth::authorize_form,
        routes::auth::authorize_submit,
        routes::auth::token,
    ),
    components(schemas(
        HealthResponse,
        kura_core::error::ApiError,
        kura_core::events::Event,
        kura_core::events::EventMetadata,
        kura_core::events::CreateEventRequest,
        kura_core::events::BatchCreateEventsRequest,
        kura_core::events::PaginatedResponse<kura_core::events::Event>,
        routes::auth::RegisterRequest,
        routes::auth::RegisterResponse,
        routes::auth::TokenRequest,
        routes::auth::TokenResponse,
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

    // Router
    let app = Router::new()
        .merge(SwaggerUi::new("/swagger-ui").url("/api-doc/openapi.json", ApiDoc::openapi()))
        .merge(routes::health::router())
        .merge(routes::events::router())
        .merge(routes::auth::router())
        .layer(TraceLayer::new_for_http())
        .with_state(app_state);

    let port: u16 = std::env::var("PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(3000);

    let addr = SocketAddr::from(([0, 0, 0, 0], port));
    tracing::info!("Kura API listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await.unwrap();
    axum::serve(listener, app).await.unwrap();
}
