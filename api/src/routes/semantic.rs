use std::cmp::Ordering;
use std::collections::HashMap;

use axum::extract::State;
use axum::routing::post;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use kura_core::error::ApiError;

use crate::auth::AuthenticatedUser;
use crate::error::AppError;
use crate::state::AppState;

const DEFAULT_TOP_K: usize = 5;
const MAX_TOP_K: usize = 10;
const MAX_QUERIES: usize = 50;
const HIGH_CONFIDENCE_MIN: f64 = 0.86;
const MEDIUM_CONFIDENCE_MIN: f64 = 0.78;
const DEFAULT_MIN_SIMILARITY: f64 = 0.72;

pub fn router() -> Router<AppState> {
    Router::new().route("/v1/semantic/resolve", post(resolve_semantic_terms))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Deserialize, Serialize, utoipa::ToSchema)]
#[serde(rename_all = "lowercase")]
pub enum SemanticDomain {
    Exercise,
    Food,
}

impl SemanticDomain {
    fn as_str(self) -> &'static str {
        match self {
            SemanticDomain::Exercise => "exercise",
            SemanticDomain::Food => "food",
        }
    }
}

#[derive(Debug, Clone, Deserialize, utoipa::ToSchema)]
pub struct SemanticResolveRequest {
    pub queries: Vec<SemanticResolveQuery>,
    /// Number of candidates per query (default 5, max 10)
    #[serde(default)]
    pub top_k: Option<usize>,
}

#[derive(Debug, Clone, Deserialize, utoipa::ToSchema)]
pub struct SemanticResolveQuery {
    pub term: String,
    pub domain: SemanticDomain,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SemanticResolveResponse {
    pub results: Vec<SemanticResolveResult>,
    pub meta: SemanticResolveMeta,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SemanticResolveResult {
    pub term: String,
    pub normalized_term: String,
    pub domain: SemanticDomain,
    pub candidates: Vec<SemanticResolveCandidate>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SemanticResolveCandidate {
    pub canonical_key: String,
    pub canonical_label: String,
    pub score: f64,
    pub confidence: SemanticConfidenceBand,
    pub provenance: Vec<SemanticResolveProvenance>,
}

#[derive(Debug, Clone, Copy, Serialize, utoipa::ToSchema, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum SemanticConfidenceBand {
    High,
    Medium,
    Low,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SemanticResolveProvenance {
    pub source: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub matched_term: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub observed_count: Option<i64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
}

#[derive(Debug, Clone, Serialize, utoipa::ToSchema)]
pub struct SemanticResolveMeta {
    pub generated_at: DateTime<Utc>,
    pub top_k: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub provider: Option<SemanticProviderInfo>,
    pub min_similarity_threshold: f64,
}

#[derive(Debug, Clone, Deserialize, Serialize, utoipa::ToSchema)]
pub struct SemanticProviderInfo {
    pub provider: String,
    pub model: String,
    pub dimensions: i64,
}

#[derive(Debug, Clone, Deserialize, Default)]
struct SemanticMemoryProjectionData {
    #[serde(default)]
    exercise_candidates: Vec<SemanticMemoryExerciseCandidate>,
    #[serde(default)]
    food_candidates: Vec<SemanticMemoryFoodCandidate>,
    provider: Option<SemanticProviderInfo>,
    data_quality: Option<SemanticMemoryDataQuality>,
}

#[derive(Debug, Clone, Deserialize)]
struct SemanticMemoryExerciseCandidate {
    term: String,
    #[serde(default)]
    count: i64,
    suggested_exercise_id: String,
    label: String,
    #[serde(default)]
    score: f64,
}

#[derive(Debug, Clone, Deserialize)]
struct SemanticMemoryFoodCandidate {
    term: String,
    #[serde(default)]
    count: i64,
    suggested_food_id: String,
    label: String,
    #[serde(default)]
    score: f64,
}

#[derive(Debug, Clone, Deserialize, Default)]
struct SemanticMemoryDataQuality {
    min_similarity_threshold: Option<f64>,
}

#[derive(Debug, sqlx::FromRow)]
struct ProjectionDataRow {
    data: serde_json::Value,
}

#[derive(Debug, sqlx::FromRow)]
struct CatalogCandidateRow {
    canonical_key: String,
    canonical_label: String,
}

#[derive(Debug, sqlx::FromRow)]
struct EmbeddingRow {
    embedding: serde_json::Value,
}

#[derive(Debug, sqlx::FromRow)]
struct CatalogEmbeddingRow {
    canonical_key: String,
    canonical_label: String,
    embedding: serde_json::Value,
}

#[derive(Debug, Clone)]
struct CatalogEmbedding {
    canonical_key: String,
    canonical_label: String,
    embedding: Vec<f64>,
}

#[derive(Debug, Clone)]
struct CandidateAccumulator {
    canonical_key: String,
    canonical_label: String,
    score: f64,
    provenance: Vec<SemanticResolveProvenance>,
}

#[derive(Debug, Clone)]
struct SemanticMemoryMatch {
    canonical_key: String,
    canonical_label: String,
    score: f64,
    count: i64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum EmbeddingSource {
    UserEmbedding,
    HashingRuntimeFallback,
}

fn normalize_term(value: &str) -> String {
    value
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .trim()
        .to_lowercase()
}

fn round_score(score: f64) -> f64 {
    (score * 10_000.0).round() / 10_000.0
}

fn confidence_band(score: f64) -> SemanticConfidenceBand {
    if score >= HIGH_CONFIDENCE_MIN {
        SemanticConfidenceBand::High
    } else if score >= MEDIUM_CONFIDENCE_MIN {
        SemanticConfidenceBand::Medium
    } else {
        SemanticConfidenceBand::Low
    }
}

fn cosine_similarity(a: &[f64], b: &[f64]) -> f64 {
    if a.is_empty() || b.is_empty() || a.len() != b.len() {
        return 0.0;
    }

    let mut dot = 0.0;
    let mut norm_a = 0.0;
    let mut norm_b = 0.0;
    for (x, y) in a.iter().zip(b.iter()) {
        dot += x * y;
        norm_a += x * x;
        norm_b += y * y;
    }

    if norm_a <= 0.0 || norm_b <= 0.0 {
        return 0.0;
    }

    dot / (norm_a.sqrt() * norm_b.sqrt())
}

fn parse_embedding(value: &serde_json::Value) -> Vec<f64> {
    let Some(items) = value.as_array() else {
        return Vec::new();
    };

    let mut out = Vec::with_capacity(items.len());
    for item in items {
        let Some(v) = item.as_f64() else {
            return Vec::new();
        };
        out.push(v);
    }
    out
}

fn tokenize_ascii(text: &str) -> Vec<String> {
    let mut tokens = Vec::new();
    let mut current = String::new();

    for ch in text.chars() {
        if ch.is_ascii_alphanumeric() || ch == '_' {
            current.push(ch.to_ascii_lowercase());
        } else if !current.is_empty() {
            tokens.push(std::mem::take(&mut current));
        }
    }

    if !current.is_empty() {
        tokens.push(current);
    }

    tokens
}

fn hashing_embedding(text: &str, dimensions: usize) -> Vec<f64> {
    let mut vec = vec![0.0_f64; dimensions];
    if dimensions == 0 {
        return vec;
    }

    let tokens = tokenize_ascii(text);
    if tokens.is_empty() {
        return vec;
    }

    let mut counts: HashMap<String, u32> = HashMap::new();
    for token in tokens {
        *counts.entry(token).or_insert(0) += 1;
    }

    for (token, count) in counts {
        let digest = Sha256::digest(token.as_bytes());
        let bucket =
            u32::from_be_bytes([digest[0], digest[1], digest[2], digest[3]]) as usize % dimensions;
        let sign = if digest[4] % 2 == 0 { 1.0 } else { -1.0 };
        vec[bucket] += sign * f64::from(count);
    }

    let norm = vec.iter().map(|v| v * v).sum::<f64>().sqrt();
    if norm > 0.0 {
        for value in &mut vec {
            *value /= norm;
        }
    }

    vec
}

fn validate_request(req: &SemanticResolveRequest) -> Result<usize, AppError> {
    if req.queries.is_empty() {
        return Err(AppError::Validation {
            message: "queries must contain at least one item".to_string(),
            field: Some("queries".to_string()),
            received: None,
            docs_hint: Some("Provide one or more {term, domain} queries.".to_string()),
        });
    }
    if req.queries.len() > MAX_QUERIES {
        return Err(AppError::Validation {
            message: format!("queries must contain at most {MAX_QUERIES} items"),
            field: Some("queries".to_string()),
            received: Some(serde_json::json!(req.queries.len())),
            docs_hint: None,
        });
    }

    for (idx, query) in req.queries.iter().enumerate() {
        if normalize_term(&query.term).is_empty() {
            return Err(AppError::Validation {
                message: "term must not be empty".to_string(),
                field: Some(format!("queries[{idx}].term")),
                received: Some(serde_json::json!(query.term)),
                docs_hint: None,
            });
        }
    }

    Ok(req.top_k.unwrap_or(DEFAULT_TOP_K).clamp(1, MAX_TOP_K))
}

fn add_candidate(
    out: &mut HashMap<String, CandidateAccumulator>,
    canonical_key: String,
    canonical_label: String,
    score: f64,
    provenance: SemanticResolveProvenance,
) {
    let score = score.clamp(-1.0, 1.0);
    let entry = out
        .entry(canonical_key.clone())
        .or_insert_with(|| CandidateAccumulator {
            canonical_key: canonical_key.clone(),
            canonical_label: canonical_label.clone(),
            score,
            provenance: Vec::new(),
        });

    if score > entry.score {
        entry.score = score;
    }
    if entry.canonical_label.is_empty() && !canonical_label.is_empty() {
        entry.canonical_label = canonical_label;
    }

    let duplicate = entry.provenance.iter().any(|existing| {
        existing.source == provenance.source
            && existing.matched_term == provenance.matched_term
            && existing.observed_count == provenance.observed_count
            && existing.provider == provenance.provider
            && existing.model == provenance.model
    });
    if !duplicate {
        entry.provenance.push(provenance);
    }
}

fn collect_projection_matches(
    projection: Option<&SemanticMemoryProjectionData>,
    domain: SemanticDomain,
    normalized_term: &str,
) -> Vec<SemanticMemoryMatch> {
    let Some(data) = projection else {
        return Vec::new();
    };

    match domain {
        SemanticDomain::Exercise => data
            .exercise_candidates
            .iter()
            .filter(|c| normalize_term(&c.term) == normalized_term)
            .map(|c| SemanticMemoryMatch {
                canonical_key: c.suggested_exercise_id.clone(),
                canonical_label: c.label.clone(),
                score: c.score,
                count: c.count,
            })
            .collect(),
        SemanticDomain::Food => data
            .food_candidates
            .iter()
            .filter(|c| normalize_term(&c.term) == normalized_term)
            .map(|c| SemanticMemoryMatch {
                canonical_key: c.suggested_food_id.clone(),
                canonical_label: c.label.clone(),
                score: c.score,
                count: c.count,
            })
            .collect(),
    }
}

async fn fetch_semantic_memory_projection(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
) -> Result<Option<SemanticMemoryProjectionData>, AppError> {
    let row = sqlx::query_as::<_, ProjectionDataRow>(
        r#"
        SELECT data
        FROM projections
        WHERE user_id = $1
          AND projection_type = 'semantic_memory'
          AND key = 'overview'
        "#,
    )
    .bind(user_id)
    .fetch_optional(&mut **tx)
    .await?;

    match row {
        Some(row) => match serde_json::from_value::<SemanticMemoryProjectionData>(row.data) {
            Ok(parsed) => Ok(Some(parsed)),
            Err(err) => {
                tracing::warn!("semantic_memory projection parse failed: {err}");
                Ok(None)
            }
        },
        None => Ok(None),
    }
}

async fn fetch_exact_catalog_matches(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    domain: SemanticDomain,
    normalized_term: &str,
) -> Result<Vec<CatalogCandidateRow>, AppError> {
    if normalized_term.is_empty() {
        return Ok(Vec::new());
    }

    let rows = sqlx::query_as::<_, CatalogCandidateRow>(
        r#"
        SELECT DISTINCT c.canonical_key, c.canonical_label
        FROM semantic_catalog c
        LEFT JOIN semantic_variants v ON v.catalog_id = c.id
        WHERE c.domain = $1
          AND (
            lower(c.canonical_key) = $2
            OR lower(c.canonical_label) = $2
            OR lower(v.variant_text) = $2
          )
        ORDER BY c.canonical_key
        "#,
    )
    .bind(domain.as_str())
    .bind(normalized_term)
    .fetch_all(&mut **tx)
    .await?;

    Ok(rows)
}

async fn fetch_user_embedding(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
    domain: SemanticDomain,
    term: &str,
    model: &str,
) -> Result<Option<Vec<f64>>, AppError> {
    let row = sqlx::query_as::<_, EmbeddingRow>(
        r#"
        SELECT embedding
        FROM semantic_user_embeddings
        WHERE user_id = $1
          AND domain = $2
          AND term_text = $3
          AND model = $4
        "#,
    )
    .bind(user_id)
    .bind(domain.as_str())
    .bind(term)
    .bind(model)
    .fetch_optional(&mut **tx)
    .await?;

    Ok(row
        .map(|r| parse_embedding(&r.embedding))
        .filter(|v| !v.is_empty()))
}

async fn load_catalog_embeddings(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    domain: SemanticDomain,
    model: &str,
) -> Result<Vec<CatalogEmbedding>, AppError> {
    let rows = sqlx::query_as::<_, CatalogEmbeddingRow>(
        r#"
        SELECT c.canonical_key, c.canonical_label, ce.embedding
        FROM semantic_catalog c
        JOIN semantic_catalog_embeddings ce ON ce.catalog_id = c.id
        WHERE c.domain = $1
          AND ce.model = $2
        "#,
    )
    .bind(domain.as_str())
    .bind(model)
    .fetch_all(&mut **tx)
    .await?;

    Ok(rows
        .into_iter()
        .filter_map(|row| {
            let embedding = parse_embedding(&row.embedding);
            if embedding.is_empty() {
                return None;
            }
            Some(CatalogEmbedding {
                canonical_key: row.canonical_key,
                canonical_label: row.canonical_label,
                embedding,
            })
        })
        .collect())
}

fn pick_embedding_for_query(
    stored: Option<Vec<f64>>,
    provider: Option<&SemanticProviderInfo>,
    normalized_term: &str,
) -> Option<(Vec<f64>, EmbeddingSource)> {
    if let Some(vec) = stored {
        return Some((vec, EmbeddingSource::UserEmbedding));
    }

    let info = provider?;
    if info.provider != "hashing" {
        return None;
    }
    let dimensions = usize::try_from(info.dimensions).ok()?;
    if dimensions == 0 {
        return None;
    }

    Some((
        hashing_embedding(normalized_term, dimensions),
        EmbeddingSource::HashingRuntimeFallback,
    ))
}

fn finalize_candidates(
    entries: HashMap<String, CandidateAccumulator>,
    top_k: usize,
) -> Vec<SemanticResolveCandidate> {
    let mut out: Vec<SemanticResolveCandidate> = entries
        .into_values()
        .map(|entry| SemanticResolveCandidate {
            canonical_key: entry.canonical_key,
            canonical_label: entry.canonical_label,
            score: round_score(entry.score),
            confidence: confidence_band(entry.score),
            provenance: entry.provenance,
        })
        .collect();

    out.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(Ordering::Equal)
            .then_with(|| a.canonical_key.cmp(&b.canonical_key))
    });
    out.truncate(top_k);
    out
}

async fn resolve_query_candidates(
    tx: &mut sqlx::Transaction<'_, sqlx::Postgres>,
    user_id: Uuid,
    query: &SemanticResolveQuery,
    normalized_term: &str,
    top_k: usize,
    projection_data: Option<&SemanticMemoryProjectionData>,
    provider: Option<&SemanticProviderInfo>,
    min_similarity: f64,
    catalog_cache: &mut HashMap<(SemanticDomain, String), Vec<CatalogEmbedding>>,
) -> Result<Vec<SemanticResolveCandidate>, AppError> {
    let mut candidates: HashMap<String, CandidateAccumulator> = HashMap::new();

    for item in collect_projection_matches(projection_data, query.domain, normalized_term) {
        add_candidate(
            &mut candidates,
            item.canonical_key,
            item.canonical_label,
            item.score,
            SemanticResolveProvenance {
                source: "semantic_memory_projection".to_string(),
                matched_term: Some(normalized_term.to_string()),
                observed_count: Some(item.count),
                provider: provider.map(|p| p.provider.clone()),
                model: provider.map(|p| p.model.clone()),
            },
        );
    }

    for row in fetch_exact_catalog_matches(tx, query.domain, normalized_term).await? {
        add_candidate(
            &mut candidates,
            row.canonical_key,
            row.canonical_label,
            1.0,
            SemanticResolveProvenance {
                source: "catalog_exact_match".to_string(),
                matched_term: Some(normalized_term.to_string()),
                observed_count: None,
                provider: None,
                model: None,
            },
        );
    }

    if let Some(provider_info) = provider {
        let stored_embedding = fetch_user_embedding(
            tx,
            user_id,
            query.domain,
            normalized_term,
            &provider_info.model,
        )
        .await?;

        if let Some((query_embedding, source)) =
            pick_embedding_for_query(stored_embedding, provider, normalized_term)
        {
            let cache_key = (query.domain, provider_info.model.clone());
            if !catalog_cache.contains_key(&cache_key) {
                let loaded =
                    load_catalog_embeddings(tx, query.domain, &provider_info.model).await?;
                catalog_cache.insert(cache_key.clone(), loaded);
            }

            if let Some(catalog_embeddings) = catalog_cache.get(&cache_key) {
                for item in catalog_embeddings {
                    let score = cosine_similarity(&query_embedding, &item.embedding);
                    if score < min_similarity {
                        continue;
                    }

                    add_candidate(
                        &mut candidates,
                        item.canonical_key.clone(),
                        item.canonical_label.clone(),
                        score,
                        SemanticResolveProvenance {
                            source: match source {
                                EmbeddingSource::UserEmbedding => {
                                    "user_embedding_similarity".to_string()
                                }
                                EmbeddingSource::HashingRuntimeFallback => {
                                    "hashing_runtime_similarity".to_string()
                                }
                            },
                            matched_term: Some(normalized_term.to_string()),
                            observed_count: None,
                            provider: Some(provider_info.provider.clone()),
                            model: Some(provider_info.model.clone()),
                        },
                    );
                }
            }
        }
    }

    Ok(finalize_candidates(candidates, top_k))
}

/// Resolve free-text exercise/food terms into ranked canonical candidates.
///
/// Uses semantic_memory projection matches first, then exact catalog matches,
/// then embedding similarity against catalog entries.
#[utoipa::path(
    post,
    path = "/v1/semantic/resolve",
    request_body = SemanticResolveRequest,
    responses(
        (status = 200, description = "Semantic candidates by query", body = SemanticResolveResponse),
        (status = 400, description = "Validation failed", body = ApiError),
        (status = 401, description = "Unauthorized", body = ApiError)
    ),
    security(("bearer_auth" = [])),
    tag = "semantic"
)]
pub async fn resolve_semantic_terms(
    State(state): State<AppState>,
    auth: AuthenticatedUser,
    Json(req): Json<SemanticResolveRequest>,
) -> Result<Json<SemanticResolveResponse>, AppError> {
    let top_k = validate_request(&req)?;
    let user_id = auth.user_id;

    let mut tx = state.db.begin().await?;
    sqlx::query("SELECT set_config('kura.current_user_id', $1, true)")
        .bind(user_id.to_string())
        .execute(&mut *tx)
        .await?;

    let projection_data = fetch_semantic_memory_projection(&mut tx, user_id).await?;
    let provider = projection_data.as_ref().and_then(|p| p.provider.clone());
    let min_similarity_threshold = projection_data
        .as_ref()
        .and_then(|p| p.data_quality.as_ref())
        .and_then(|dq| dq.min_similarity_threshold)
        .unwrap_or(DEFAULT_MIN_SIMILARITY);

    let mut catalog_cache: HashMap<(SemanticDomain, String), Vec<CatalogEmbedding>> =
        HashMap::new();
    let mut results = Vec::with_capacity(req.queries.len());

    for query in req.queries {
        let normalized_term = normalize_term(&query.term);
        let candidates = resolve_query_candidates(
            &mut tx,
            user_id,
            &query,
            &normalized_term,
            top_k,
            projection_data.as_ref(),
            provider.as_ref(),
            min_similarity_threshold,
            &mut catalog_cache,
        )
        .await?;

        results.push(SemanticResolveResult {
            term: query.term,
            normalized_term,
            domain: query.domain,
            candidates,
        });
    }

    tx.commit().await?;

    Ok(Json(SemanticResolveResponse {
        results,
        meta: SemanticResolveMeta {
            generated_at: Utc::now(),
            top_k,
            provider,
            min_similarity_threshold,
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_term_collapses_whitespace_and_lowercases() {
        assert_eq!(normalize_term("  Knie   Beuge "), "knie beuge");
    }

    #[test]
    fn confidence_bands_match_thresholds() {
        assert_eq!(confidence_band(0.90), SemanticConfidenceBand::High);
        assert_eq!(confidence_band(0.80), SemanticConfidenceBand::Medium);
        assert_eq!(confidence_band(0.70), SemanticConfidenceBand::Low);
    }

    #[test]
    fn hashing_embedding_is_deterministic_and_normalized() {
        let a = hashing_embedding("Kniebeuge", 64);
        let b = hashing_embedding("Kniebeuge", 64);
        assert_eq!(a, b);

        let norm = a.iter().map(|v| v * v).sum::<f64>().sqrt();
        assert!((norm - 1.0).abs() < 1e-9);
    }

    #[test]
    fn projection_matches_use_domain_specific_fields() {
        let projection = SemanticMemoryProjectionData {
            exercise_candidates: vec![SemanticMemoryExerciseCandidate {
                term: "Kniebeuge".to_string(),
                count: 7,
                suggested_exercise_id: "barbell_back_squat".to_string(),
                label: "Barbell Back Squat".to_string(),
                score: 0.89,
            }],
            food_candidates: vec![SemanticMemoryFoodCandidate {
                term: "Hafer".to_string(),
                count: 3,
                suggested_food_id: "oats".to_string(),
                label: "Oats".to_string(),
                score: 0.88,
            }],
            provider: None,
            data_quality: None,
        };

        let ex =
            collect_projection_matches(Some(&projection), SemanticDomain::Exercise, "kniebeuge");
        assert_eq!(ex.len(), 1);
        assert_eq!(ex[0].canonical_key, "barbell_back_squat");

        let food = collect_projection_matches(Some(&projection), SemanticDomain::Food, "hafer");
        assert_eq!(food.len(), 1);
        assert_eq!(food[0].canonical_key, "oats");
    }

    #[test]
    fn add_candidate_keeps_best_score_and_merges_provenance() {
        let mut out = HashMap::new();
        add_candidate(
            &mut out,
            "barbell_back_squat".to_string(),
            "Barbell Back Squat".to_string(),
            0.81,
            SemanticResolveProvenance {
                source: "semantic_memory_projection".to_string(),
                matched_term: Some("kniebeuge".to_string()),
                observed_count: Some(4),
                provider: None,
                model: None,
            },
        );
        add_candidate(
            &mut out,
            "barbell_back_squat".to_string(),
            "Barbell Back Squat".to_string(),
            0.93,
            SemanticResolveProvenance {
                source: "user_embedding_similarity".to_string(),
                matched_term: Some("kniebeuge".to_string()),
                observed_count: None,
                provider: Some("hashing".to_string()),
                model: Some(
                    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2".to_string(),
                ),
            },
        );

        let result = finalize_candidates(out, 5);
        assert_eq!(result.len(), 1);
        assert_eq!(result[0].score, 0.93);
        assert_eq!(result[0].confidence, SemanticConfidenceBand::High);
        assert_eq!(result[0].provenance.len(), 2);
    }

    #[test]
    fn validate_request_rejects_empty_queries() {
        let err = validate_request(&SemanticResolveRequest {
            queries: Vec::new(),
            top_k: None,
        })
        .expect_err("empty query list must fail");

        match err {
            AppError::Validation { field, .. } => {
                assert_eq!(field.as_deref(), Some("queries"));
            }
            other => panic!("unexpected error: {other:?}"),
        }
    }

    #[test]
    fn validate_request_clamps_top_k() {
        let top_k = validate_request(&SemanticResolveRequest {
            queries: vec![SemanticResolveQuery {
                term: "bench".to_string(),
                domain: SemanticDomain::Exercise,
            }],
            top_k: Some(100),
        })
        .expect("request should validate");

        assert_eq!(top_k, MAX_TOP_K);
    }
}
