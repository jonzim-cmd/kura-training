use super::*;

pub(super) const WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE: &str = "workflow.onboarding.closed";
pub(super) const WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE: &str =
    "workflow.onboarding.override_granted";
pub(super) const WORKFLOW_INVARIANT_ID: &str = "INV-004";
pub(super) const ONBOARDING_REQUIRED_AREAS: [&str; 3] = [
    "training_background",
    "baseline_profile",
    "unit_preferences",
];
pub(super) const PLANNING_OR_COACHING_EVENT_TYPES: [&str; 8] = [
    "training_plan.created",
    "training_plan.updated",
    "training_plan.archived",
    "projection_rule.created",
    "projection_rule.archived",
    "weight_target.set",
    "sleep_target.set",
    "nutrition_target.set",
];
pub(super) const VISUALIZATION_RENDER_FORMATS: [&str; 5] =
    ["chart", "table", "timeline", "ascii", "mermaid"];
pub(super) const VISUALIZATION_INVARIANT_ID: &str = "INV-009";
pub(super) const VISUALIZATION_TREND_KEYWORDS: [&str; 8] = [
    "trend",
    "progress",
    "verlauf",
    "entwicklung",
    "plateau",
    "stagnation",
    "improve",
    "improving",
];
pub(super) const VISUALIZATION_COMPARE_KEYWORDS: [&str; 7] = [
    "compare",
    "vergleich",
    "versus",
    " vs ",
    "difference",
    "delta",
    "gegenueber",
];
pub(super) const VISUALIZATION_PLAN_VS_ACTUAL_KEYWORDS: [&str; 8] = [
    "plan vs actual",
    "planned vs actual",
    "soll ist",
    "adherence",
    "compliance",
    "abweichung",
    "planabweichung",
    "target vs actual",
];
pub(super) const VISUALIZATION_MULTI_WEEK_KEYWORDS: [&str; 8] = [
    "multi-week",
    "multi week",
    "mehrwoechig",
    "mehrwöchig",
    "weekly schedule",
    "wochenplan",
    "several weeks",
    "weeks ahead",
];

pub(super) fn default_true() -> bool {
    true
}

pub(super) fn is_planning_or_coaching_event_type(event_type: &str) -> bool {
    let normalized = event_type.trim().to_lowercase();
    PLANNING_OR_COACHING_EVENT_TYPES.contains(&normalized.as_str())
}

pub(super) fn timezone_from_user_profile(data: &Value) -> Option<String> {
    let user = data.get("user").and_then(Value::as_object)?;
    let preferences = user.get("preferences").and_then(Value::as_object)?;
    for key in ["timezone", "time_zone"] {
        let configured = preferences
            .get(key)
            .and_then(Value::as_str)
            .map(str::trim)
            .unwrap_or_default();
        if !configured.is_empty() {
            return Some(configured.to_string());
        }
    }
    None
}

pub(super) fn has_timezone_preference_in_user_profile(data: &Value) -> bool {
    timezone_from_user_profile(data).is_some()
}

pub(super) fn user_preference_string(
    user_profile: Option<&ProjectionResponse>,
    key: &str,
) -> Option<String> {
    let profile = user_profile?;
    let user = profile.projection.data.get("user")?.as_object()?;
    let preferences = user.get("preferences")?.as_object()?;
    let value = preferences.get(key)?.as_str()?.trim();
    if value.is_empty() {
        None
    } else {
        Some(value.to_string())
    }
}

pub(super) fn user_preference_bool(
    user_profile: Option<&ProjectionResponse>,
    key: &str,
) -> Option<bool> {
    let profile = user_profile?;
    let user = profile.projection.data.get("user")?.as_object()?;
    let preferences = user.get("preferences")?.as_object()?;
    preferences.get(key).and_then(Value::as_bool)
}

pub(super) fn normalize_challenge_mode(value: Option<&str>) -> String {
    match value.unwrap_or("auto").trim().to_lowercase().as_str() {
        "on" | "always" => "on".to_string(),
        "off" | "disabled" | "disable" => "off".to_string(),
        _ => "auto".to_string(),
    }
}

pub(super) fn resolve_challenge_mode(
    user_profile: Option<&ProjectionResponse>,
) -> AgentChallengeMode {
    let raw = user_preference_string(user_profile, "challenge_mode");
    let mode = normalize_challenge_mode(raw.as_deref());
    let intro_seen =
        user_preference_bool(user_profile, "challenge_mode_intro_seen").unwrap_or(false);
    let source = if raw.is_some() {
        "user_profile.preference"
    } else {
        "default_auto"
    }
    .to_string();

    AgentChallengeMode {
        schema_version: AGENT_CHALLENGE_MODE_SCHEMA_VERSION.to_string(),
        mode,
        source,
        onboarding_hint_required: !intro_seen,
        onboarding_hint: if intro_seen {
            None
        } else {
            Some(AGENT_CHALLENGE_MODE_ONBOARDING_HINT.to_string())
        },
    }
}

pub(super) fn tier_confidence_band(freshness_state: &str) -> String {
    match freshness_state {
        "fresh" => "high".to_string(),
        "aging" => "medium".to_string(),
        _ => "low".to_string(),
    }
}

pub(super) fn tier_freshness(
    observed_at: Option<DateTime<Utc>>,
    stale_after_days: i64,
    now: DateTime<Utc>,
) -> (String, Option<String>) {
    let Some(observed_at) = observed_at else {
        return (
            "stale".to_string(),
            Some("no_observed_timestamp".to_string()),
        );
    };
    let age_days = (now - observed_at).num_days();
    if age_days <= (stale_after_days / 3).max(1) {
        return ("fresh".to_string(), None);
    }
    if age_days < stale_after_days {
        return ("aging".to_string(), Some(format!("age_days={age_days}")));
    }
    (
        "stale".to_string(),
        Some(format!("stale_age_days={age_days}")),
    )
}

pub(super) fn build_memory_tier_contract(
    user_profile: &ProjectionResponse,
    training_plan: Option<&ProjectionResponse>,
    semantic_memory: Option<&ProjectionResponse>,
    now: DateTime<Utc>,
) -> AgentMemoryTierContract {
    let working_observed_at = semantic_memory.map(|projection| projection.projection.updated_at);
    let (working_state, working_reason) = tier_freshness(working_observed_at, 7, now);

    let project_observed_at = training_plan.map(|projection| projection.projection.updated_at);
    let (project_state, project_reason) = tier_freshness(project_observed_at, 30, now);

    let principles_observed_at = Some(user_profile.projection.updated_at);
    let (principles_state, principles_reason) = tier_freshness(principles_observed_at, 180, now);

    AgentMemoryTierContract {
        schema_version: AGENT_MEMORY_TIER_CONTRACT_VERSION.to_string(),
        high_impact_stale_action: "confirm_first".to_string(),
        tiers: vec![
            AgentMemoryTierSnapshot {
                tier: "working".to_string(),
                freshness_state: working_state.clone(),
                confidence_band: tier_confidence_band(&working_state),
                source: "projection:semantic_memory/overview".to_string(),
                observed_at: working_observed_at,
                last_verified_at: working_observed_at,
                stale_reason: working_reason,
            },
            AgentMemoryTierSnapshot {
                tier: "project".to_string(),
                freshness_state: project_state.clone(),
                confidence_band: tier_confidence_band(&project_state),
                source: "projection:training_plan/overview".to_string(),
                observed_at: project_observed_at,
                last_verified_at: project_observed_at,
                stale_reason: project_reason,
            },
            AgentMemoryTierSnapshot {
                tier: "principles".to_string(),
                freshness_state: principles_state.clone(),
                confidence_band: tier_confidence_band(&principles_state),
                source: "projection:user_profile/me.preferences".to_string(),
                observed_at: principles_observed_at,
                last_verified_at: principles_observed_at,
                stale_reason: principles_reason,
            },
        ],
    }
}

pub(super) fn memory_tier_confirm_reason(
    action_class: &str,
    user_profile: Option<&ProjectionResponse>,
    now: DateTime<Utc>,
) -> Option<String> {
    if action_class != "high_impact_write" {
        return None;
    }

    let Some(profile) = user_profile else {
        return Some(MEMORY_TIER_PRINCIPLES_MISSING_CONFIRM_REASON_CODE.to_string());
    };
    let Some(user) = profile
        .projection
        .data
        .get("user")
        .and_then(Value::as_object)
    else {
        return Some(MEMORY_TIER_PRINCIPLES_MISSING_CONFIRM_REASON_CODE.to_string());
    };
    let preferences = user.get("preferences").and_then(Value::as_object);
    if preferences.is_none() || preferences.is_some_and(|map| map.is_empty()) {
        return Some(MEMORY_TIER_PRINCIPLES_MISSING_CONFIRM_REASON_CODE.to_string());
    }

    let (freshness_state, _reason) = tier_freshness(Some(profile.projection.updated_at), 180, now);
    if freshness_state == "stale" {
        Some(MEMORY_TIER_PRINCIPLES_STALE_CONFIRM_REASON_CODE.to_string())
    } else {
        None
    }
}

pub(super) fn normalize_visualization_format(format: &str) -> Option<String> {
    let normalized = format.trim().to_lowercase();
    if VISUALIZATION_RENDER_FORMATS.contains(&normalized.as_str()) {
        Some(normalized)
    } else {
        None
    }
}

pub(super) fn normalize_visualization_preference(preference: Option<&str>) -> String {
    match preference.unwrap_or("auto").trim().to_lowercase().as_str() {
        "always" => "always".to_string(),
        "never" => "never".to_string(),
        _ => "auto".to_string(),
    }
}

pub(super) fn contains_any_keyword(haystack: &str, keywords: &[&str]) -> bool {
    keywords.iter().any(|keyword| haystack.contains(keyword))
}

pub(super) fn detect_visualization_trigger(task_intent: &str) -> String {
    let normalized = format!(" {} ", task_intent.trim().to_lowercase());
    if contains_any_keyword(&normalized, &VISUALIZATION_PLAN_VS_ACTUAL_KEYWORDS) {
        return "plan_vs_actual".to_string();
    }
    if contains_any_keyword(&normalized, &VISUALIZATION_MULTI_WEEK_KEYWORDS) {
        return "multi_week_scheduling".to_string();
    }
    if contains_any_keyword(&normalized, &VISUALIZATION_COMPARE_KEYWORDS) {
        return "compare".to_string();
    }
    if contains_any_keyword(&normalized, &VISUALIZATION_TREND_KEYWORDS) {
        return "trend".to_string();
    }
    "none".to_string()
}

pub(super) fn normalize_visualization_complexity(
    complexity_hint: Option<&str>,
    source_count: usize,
    trigger: &str,
) -> String {
    let normalized_hint = complexity_hint.unwrap_or("").trim().to_lowercase();
    if matches!(normalized_hint.as_str(), "low" | "medium" | "high") {
        return normalized_hint;
    }
    if source_count >= 3 || trigger == "plan_vs_actual" || trigger == "multi_week_scheduling" {
        return "high".to_string();
    }
    if source_count >= 2 || trigger == "trend" || trigger == "compare" {
        return "medium".to_string();
    }
    "low".to_string()
}

pub(super) fn visualization_policy_decision(
    task_intent: &str,
    user_preference_override: Option<&str>,
    complexity_hint: Option<&str>,
    source_count: usize,
) -> AgentVisualizationPolicyDecision {
    let preference_mode = normalize_visualization_preference(user_preference_override);
    let trigger = detect_visualization_trigger(task_intent);
    let complexity = normalize_visualization_complexity(complexity_hint, source_count, &trigger);

    if preference_mode == "never" {
        return AgentVisualizationPolicyDecision {
            status: "skipped".to_string(),
            trigger: "user_preference_never".to_string(),
            preference_mode,
            complexity,
            reason: "Visualization skipped due to explicit user preference override.".to_string(),
        };
    }

    if preference_mode == "always" {
        return AgentVisualizationPolicyDecision {
            status: "visualize".to_string(),
            trigger: "user_preference_always".to_string(),
            preference_mode,
            complexity,
            reason: "Visualization enabled due to explicit user preference override.".to_string(),
        };
    }

    if trigger == "none" {
        return AgentVisualizationPolicyDecision {
            status: "skipped".to_string(),
            trigger,
            preference_mode,
            complexity,
            reason:
                "No visualization trigger detected (trend/compare/plan-vs-actual/multi-week scheduling)."
                    .to_string(),
        };
    }

    AgentVisualizationPolicyDecision {
        status: "visualize".to_string(),
        trigger,
        preference_mode,
        complexity,
        reason: "Visualization trigger detected and policy allows structured rendering."
            .to_string(),
    }
}

pub(super) fn normalize_visualization_spec(
    spec: AgentVisualizationSpec,
) -> Result<AgentVisualizationSpec, AppError> {
    let format =
        normalize_visualization_format(&spec.format).ok_or_else(|| AppError::Validation {
            message: "visualization_spec.format is not supported".to_string(),
            field: Some("visualization_spec.format".to_string()),
            received: Some(serde_json::json!(spec.format)),
            docs_hint: Some(
                "Supported formats: chart, table, timeline, ascii, mermaid.".to_string(),
            ),
        })?;
    let purpose = spec.purpose.trim();
    if purpose.is_empty() {
        return Err(AppError::Validation {
            message: "visualization_spec.purpose must not be empty".to_string(),
            field: Some("visualization_spec.purpose".to_string()),
            received: None,
            docs_hint: Some(
                "Provide a concrete purpose such as '4-week volume trend' or 'plan-vs-actual adherence'."
                    .to_string(),
            ),
        });
    }
    if spec.data_sources.is_empty() {
        return Err(AppError::Validation {
            message: "visualization_spec.data_sources must not be empty".to_string(),
            field: Some("visualization_spec.data_sources".to_string()),
            received: None,
            docs_hint: Some(
                "Declare at least one projection source with projection_type/key before rendering."
                    .to_string(),
            ),
        });
    }

    let normalized_sources: Vec<AgentVisualizationDataSource> = spec
        .data_sources
        .iter()
        .enumerate()
        .map(|(index, source)| {
            let projection_type = source.projection_type.trim().to_lowercase();
            let key = source.key.trim().to_lowercase();
            if projection_type.is_empty() || key.is_empty() {
                return Err(AppError::Validation {
                    message: "Each visualization data source requires projection_type and key"
                        .to_string(),
                    field: Some(format!("visualization_spec.data_sources[{index}]")),
                    received: Some(serde_json::json!({
                        "projection_type": source.projection_type,
                        "key": source.key,
                    })),
                    docs_hint: Some(
                        "Use a concrete source reference, e.g. projection_type='training_timeline', key='overview'."
                            .to_string(),
                    ),
                });
            }
            let json_path = source
                .json_path
                .as_ref()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty());
            Ok(AgentVisualizationDataSource {
                projection_type,
                key,
                json_path,
            })
        })
        .collect::<Result<Vec<_>, AppError>>()?;

    Ok(AgentVisualizationSpec {
        format,
        purpose: purpose.to_string(),
        title: spec
            .title
            .as_ref()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty()),
        timezone: spec
            .timezone
            .as_ref()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty()),
        data_sources: normalized_sources,
    })
}

pub(super) fn extract_json_path_value(data: &Value, json_path: Option<&str>) -> Option<Value> {
    let path = json_path.map(str::trim).unwrap_or_default();
    if path.is_empty() {
        return Some(data.clone());
    }

    let mut current = data;
    for segment in path.split('.') {
        let token = segment.trim();
        if token.is_empty() {
            return None;
        }
        if let Ok(index) = token.parse::<usize>() {
            current = current.as_array()?.get(index)?;
        } else {
            current = current.get(token)?;
        }
    }
    Some(current.clone())
}

pub(super) fn bind_visualization_source(
    source: &AgentVisualizationDataSource,
    projection: &ProjectionResponse,
) -> Result<AgentVisualizationResolvedSource, String> {
    let value = extract_json_path_value(&projection.projection.data, source.json_path.as_deref())
        .ok_or_else(|| {
        format!(
            "{}:{} path '{}' was not resolvable",
            source.projection_type,
            source.key,
            source.json_path.as_deref().unwrap_or_default()
        )
    })?;

    Ok(AgentVisualizationResolvedSource {
        projection_type: source.projection_type.clone(),
        key: source.key.clone(),
        json_path: source.json_path.clone(),
        projection_version: projection.projection.version,
        projection_last_event_id: projection.projection.last_event_id,
        value,
    })
}

pub(super) fn resolve_visualization_timezone_context(
    spec: &AgentVisualizationSpec,
    user_profile: Option<&ProjectionResponse>,
) -> AgentVisualizationTimezoneContext {
    if let Some(explicit) = spec
        .timezone
        .as_ref()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
    {
        return AgentVisualizationTimezoneContext {
            timezone: explicit,
            assumed: false,
            source: "spec".to_string(),
        };
    }

    if let Some(profile) = user_profile {
        if let Some(profile_timezone) = timezone_from_user_profile(&profile.projection.data) {
            return AgentVisualizationTimezoneContext {
                timezone: profile_timezone,
                assumed: false,
                source: "user_profile.preference".to_string(),
            };
        }
    }

    AgentVisualizationTimezoneContext {
        timezone: "UTC".to_string(),
        assumed: true,
        source: "fallback_utc".to_string(),
    }
}

pub(super) fn visualization_uncertainty_label(
    quality_health: Option<&ProjectionResponse>,
) -> Option<String> {
    let status = quality_health?
        .projection
        .data
        .get("status")
        .and_then(Value::as_str)
        .map(str::trim)
        .unwrap_or_default()
        .to_lowercase();

    match status.as_str() {
        "degraded" => Some(
            "Data quality is currently degraded; treat this visualization as directional, not definitive."
                .to_string(),
        ),
        "monitor" => Some(
            "Data quality is in monitor mode; verify key conclusions before acting on the chart."
                .to_string(),
        ),
        _ => None,
    }
}

pub(super) fn truncate_visualization_value(value: &Value) -> String {
    let serialized = serde_json::to_string(value).unwrap_or_else(|_| "null".to_string());
    if serialized.len() <= 180 {
        serialized
    } else {
        format!("{}...", &serialized[..177])
    }
}

pub(super) fn build_visualization_equivalent_summary(
    spec: &AgentVisualizationSpec,
    resolved_sources: &[AgentVisualizationResolvedSource],
    timezone_context: &AgentVisualizationTimezoneContext,
    uncertainty_label: Option<&str>,
) -> String {
    let mut lines = vec![
        format!("Purpose: {}", spec.purpose),
        format!(
            "Timezone: {}{}",
            timezone_context.timezone,
            if timezone_context.assumed {
                " (assumed)"
            } else {
                ""
            }
        ),
    ];

    for source in resolved_sources {
        let path_suffix = source
            .json_path
            .as_ref()
            .map(|path| format!(".{path}"))
            .unwrap_or_default();
        lines.push(format!(
            "Source {}:{}{} => {}",
            source.projection_type,
            source.key,
            path_suffix,
            truncate_visualization_value(&source.value)
        ));
    }

    if let Some(label) = uncertainty_label {
        lines.push(format!("Uncertainty: {label}"));
    }

    lines.join("\n")
}

pub(super) fn build_mermaid_preview(
    resolved_sources: &[AgentVisualizationResolvedSource],
    summary: &str,
) -> String {
    let mut mermaid_lines = vec!["flowchart TD".to_string()];
    for (index, source) in resolved_sources.iter().enumerate() {
        mermaid_lines.push(format!(
            "  S{index}[\"{}:{}\"]",
            source.projection_type, source.key
        ));
        if index > 0 {
            mermaid_lines.push(format!("  S{} --> S{index}", index - 1));
        }
    }
    format!("{}\n\n{}", mermaid_lines.join("\n"), summary)
}

pub(super) fn build_visualization_outputs(
    spec: &AgentVisualizationSpec,
    resolved_sources: &[AgentVisualizationResolvedSource],
    timezone_context: &AgentVisualizationTimezoneContext,
    allow_rich_rendering: bool,
    uncertainty_label: Option<&str>,
) -> (
    String,
    AgentVisualizationOutput,
    Option<AgentVisualizationOutput>,
    Vec<String>,
) {
    let summary = build_visualization_equivalent_summary(
        spec,
        resolved_sources,
        timezone_context,
        uncertainty_label,
    );
    let mut warnings = Vec::new();
    if timezone_context.assumed {
        warnings.push(
            "Timezone was not explicitly configured; UTC fallback was used for visualization semantics."
                .to_string(),
        );
    }
    if let Some(label) = uncertainty_label {
        warnings.push(label.to_string());
    }

    if !allow_rich_rendering && spec.format != "ascii" {
        return (
            "fallback".to_string(),
            AgentVisualizationOutput {
                format: "ascii".to_string(),
                content: summary,
            },
            None,
            warnings,
        );
    }

    if spec.format == "ascii" {
        return (
            "visualize".to_string(),
            AgentVisualizationOutput {
                format: "ascii".to_string(),
                content: summary,
            },
            None,
            warnings,
        );
    }

    let rich_content = match spec.format.as_str() {
        "mermaid" => build_mermaid_preview(resolved_sources, &summary),
        "timeline" => format!("Timeline Preview\n\n{summary}"),
        "chart" => format!("Chart Preview\n\n{summary}"),
        "table" => format!("Table Preview\n\n{summary}"),
        _ => summary.clone(),
    };

    (
        "visualize".to_string(),
        AgentVisualizationOutput {
            format: spec.format.clone(),
            content: rich_content,
        },
        Some(AgentVisualizationOutput {
            format: "ascii".to_string(),
            content: summary,
        }),
        warnings,
    )
}

pub(super) fn coverage_status_from_user_profile(data: &Value, area: &str) -> Option<String> {
    let coverage = data
        .get("user")
        .and_then(|u| u.get("interview_coverage"))
        .and_then(Value::as_array)?;
    coverage.iter().find_map(|entry| {
        let candidate_area = entry.get("area").and_then(Value::as_str)?.trim();
        if candidate_area != area {
            return None;
        }
        entry
            .get("status")
            .and_then(Value::as_str)
            .map(|status| status.trim().to_lowercase())
    })
}

pub(super) fn missing_onboarding_close_requirements(
    user_profile: Option<&ProjectionResponse>,
) -> Vec<String> {
    let mut missing = Vec::new();
    let Some(profile) = user_profile else {
        missing.push("user_profile_missing".to_string());
        missing.push("user_profile_bootstrap_pending".to_string());
        return missing;
    };
    let data = &profile.projection.data;
    if data.get("user").map(Value::is_null).unwrap_or(true) {
        missing.push("user_profile_bootstrap_pending".to_string());
        return missing;
    }

    for area in ONBOARDING_REQUIRED_AREAS {
        let Some(status) = coverage_status_from_user_profile(data, area) else {
            missing.push(format!("coverage.{area}.missing"));
            continue;
        };
        let satisfied = if area == "baseline_profile" {
            matches!(status.as_str(), "covered" | "deferred")
        } else {
            status == "covered"
        };
        if !satisfied {
            missing.push(format!("coverage.{area}.{status}"));
        }
    }

    if !has_timezone_preference_in_user_profile(data) {
        missing.push("preference.timezone.missing".to_string());
    }

    missing
}

pub(super) fn workflow_gate_from_request(
    events: &[CreateEventRequest],
    state: &AgentWorkflowState,
) -> AgentWorkflowGate {
    let planning_event_types: Vec<String> = events
        .iter()
        .filter_map(|event| {
            let event_type = event.event_type.trim().to_lowercase();
            if is_planning_or_coaching_event_type(&event_type) {
                Some(event_type)
            } else {
                None
            }
        })
        .collect();
    let contains_planning_action = !planning_event_types.is_empty();
    let requested_close = events.iter().any(|event| {
        event
            .event_type
            .trim()
            .eq_ignore_ascii_case(WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE)
    });
    let requested_override = events.iter().any(|event| {
        event
            .event_type
            .trim()
            .eq_ignore_ascii_case(WORKFLOW_ONBOARDING_OVERRIDE_EVENT_TYPE)
    });

    if !contains_planning_action {
        return AgentWorkflowGate {
            phase: if state.onboarding_closed {
                "planning".to_string()
            } else {
                "onboarding".to_string()
            },
            status: "allowed".to_string(),
            transition: "none".to_string(),
            onboarding_closed: state.onboarding_closed,
            override_used: false,
            message: if state.onboarding_closed {
                "Onboarding is closed; planning/coaching actions are available.".to_string()
            } else {
                "Onboarding remains active; no planning/coaching payload detected.".to_string()
            },
            missing_requirements: state.missing_close_requirements.clone(),
            planning_event_types,
        };
    }

    if state.onboarding_closed {
        return AgentWorkflowGate {
            phase: "planning".to_string(),
            status: "allowed".to_string(),
            transition: "none".to_string(),
            onboarding_closed: true,
            override_used: false,
            message: "Planning/coaching payload allowed because onboarding is already closed."
                .to_string(),
            missing_requirements: Vec::new(),
            planning_event_types,
        };
    }

    if requested_close && state.missing_close_requirements.is_empty() {
        return AgentWorkflowGate {
            phase: "planning".to_string(),
            status: "allowed".to_string(),
            transition: "onboarding_closed".to_string(),
            onboarding_closed: true,
            override_used: false,
            message:
                "Onboarding close transition accepted. Planning/coaching payload is now allowed."
                    .to_string(),
            missing_requirements: Vec::new(),
            planning_event_types,
        };
    }

    if requested_override || state.override_active {
        return AgentWorkflowGate {
            phase: "onboarding".to_string(),
            status: "allowed".to_string(),
            transition: "override".to_string(),
            onboarding_closed: false,
            override_used: true,
            message: "Planning/coaching payload allowed via explicit onboarding override."
                .to_string(),
            missing_requirements: state.missing_close_requirements.clone(),
            planning_event_types,
        };
    }

    if state.legacy_planning_history && state.missing_close_requirements.is_empty() {
        return AgentWorkflowGate {
            phase: "planning".to_string(),
            status: "allowed".to_string(),
            transition: "onboarding_closed".to_string(),
            onboarding_closed: true,
            override_used: false,
            message: "Planning/coaching payload allowed for legacy compatibility; onboarding close marker will be auto-recorded."
                .to_string(),
            missing_requirements: Vec::new(),
            planning_event_types,
        };
    }

    AgentWorkflowGate {
        phase: "onboarding".to_string(),
        status: "blocked".to_string(),
        transition: "none".to_string(),
        onboarding_closed: false,
        override_used: false,
        message: "Planning/coaching payload blocked: onboarding phase is not closed.".to_string(),
        missing_requirements: state.missing_close_requirements.clone(),
        planning_event_types,
    }
}

pub(super) fn build_auto_onboarding_close_event(
    events: &[CreateEventRequest],
) -> CreateEventRequest {
    let mut idempotency_keys: Vec<String> = events
        .iter()
        .map(|event| event.metadata.idempotency_key.trim().to_lowercase())
        .filter(|key| !key.is_empty())
        .collect();
    idempotency_keys.sort();
    idempotency_keys.dedup();
    let seed = format!("workflow_auto_close|{}", idempotency_keys.join("|"));
    let idempotency_key = format!("workflow-auto-close-{}", stable_hash_suffix(&seed, 20));
    let session_id = events
        .iter()
        .find_map(|event| event.metadata.session_id.clone())
        .filter(|value| !value.trim().is_empty())
        .or_else(|| Some("workflow:onboarding-auto-close".to_string()));

    CreateEventRequest {
        timestamp: Utc::now(),
        event_type: WORKFLOW_ONBOARDING_CLOSED_EVENT_TYPE.to_string(),
        data: serde_json::json!({
            "reason": "Auto-close emitted for legacy compatibility before planning/coaching write.",
            "closed_by": "system_auto",
            "compatibility_mode": "legacy_planning_history",
        }),
        metadata: EventMetadata {
            source: Some("agent_write_with_proof".to_string()),
            agent: Some("api".to_string()),
            device: None,
            session_id,
            idempotency_key,
        },
    }
}
