# Design 027: Threat Model + Security Tiering for Agent Access

Status: implemented (2026-02-12)

## Goal

Define a concrete threat model for agent access paths and map it to three
operating profiles:

- `default`
- `adaptive`
- `strict`

The design focuses on four priority threats:

- prompt exfiltration
- API enumeration
- context scraping
- scope escalation

## System Boundaries

Protected assets:

- system prompt + hidden runtime policy
- authenticated API surface + tool contracts
- user context and projection payloads
- scoped write paths (event ingestion and mutation endpoints)

Trust boundaries:

- user input -> agent runtime
- agent runtime -> tool/API invocations
- API gateway -> workers/database
- telemetry -> policy engine -> profile switching

## Prioritized Threat Matrix

| Threat | Attacker Goal | Attack Path | Detection Signals | Default Controls | Adaptive Controls | Strict Controls | Owner | Metric | Rollout |
|---|---|---|---|---|---|---|---|---|---|
| `TM-001` prompt_exfiltration | Reveal hidden prompt/policy internals | Jailbreak + reflection payloads in natural-language input | prompt leak regex hits, tool schema exposure, prompt reflection spikes | `prompt_hardening` | `prompt_hardening`, `context_minimization` | `prompt_hardening`, `context_minimization`, `abuse_kill_switch` | `platform_security` | `security.prompt_exfiltration_attempts_blocked` | default now; adaptive on abuse threshold; strict via incident command |
| `TM-002` api_enumeration | Discover hidden endpoints and capability edges | Systematic endpoint/scope probing, malformed tool calls | 404/403 burst ratio, endpoint entropy spike, repeated scope denials | `api_surface_guard` | `api_surface_guard`, `scope_enforcement` | `api_surface_guard`, `scope_enforcement`, `abuse_kill_switch` | `api_platform` | `security.api_enumeration_attempt_rate` | default allowlist now; adaptive anomaly shaping; strict hard caps |
| `TM-003` context_scraping | Extract unrelated private context | Broad summary prompts to force cross-dimension retrieval | large context pulls, cross-dimension fan-out, sensitive-key spill checks | `context_minimization` | `context_minimization`, `scope_enforcement` | `context_minimization`, `scope_enforcement`, `abuse_kill_switch` | `agent_runtime` | `security.context_leak_prevented_total` | baseline redaction now; adaptive sensitive allowlists; strict subset mode |
| `TM-004` scope_escalation | Perform actions outside granted authority | forged/replayed elevated scopes, policy bypass attempts | scope mismatch denials, replay+scope-change signatures, writes under throttle | `scope_enforcement` | `scope_enforcement`, `api_surface_guard` | `scope_enforcement`, `api_surface_guard`, `abuse_kill_switch` | `policy_engine` | `security.scope_escalation_denied_total` | enforce parity now; strict fail-closed writes in incidents |

## Security Profile Matrix

### Profile `default`

- intent: normal operation with bounded safeguards and full observability
- activation: healthy tenant baseline
- switch state:
  - `prompt_hardening=baseline`
  - `api_surface_guard=allowlist_with_rate_limits`
  - `context_minimization=redact_secrets_only`
  - `scope_enforcement=token_scope_match_required`
  - `abuse_kill_switch=manual_only`

### Profile `adaptive`

- intent: escalate protections on abuse telemetry without full lock-down
- activation: abuse score >= monitor threshold for 15 minutes
- switch state:
  - `prompt_hardening=strict_templates_plus_output_filters`
  - `api_surface_guard=allowlist_plus_anomaly_rate_shaping`
  - `context_minimization=sensitive_context_allowlist`
  - `scope_enforcement=per_action_scope_assertions`
  - `abuse_kill_switch=auto_on_multi_signal_trigger`

### Profile `strict`

- intent: incident mode with fail-closed behavior and minimal context surface
- activation: manual incident response or repeated adaptive breaches
- switch state:
  - `prompt_hardening=locked_system_prompt_and_no_tool_reflection`
  - `api_surface_guard=hard_allowlist_and_low_burst_limits`
  - `context_minimization=need_to_know_projection_subset`
  - `scope_enforcement=write_block_except_break_glass`
  - `abuse_kill_switch=always_armed_with_oncall_approval`

## Control Ownership and Measurement

| Control | Owner | Metric | Rollout Plan |
|---|---|---|---|
| `prompt_hardening` | `platform_security` | `security.prompt_exfiltration_blocks_rate` | baseline now -> adaptive anomaly trigger -> strict manual override |
| `api_surface_guard` | `api_platform` | `security.api_enumeration_blocked_requests` | baseline allowlist now -> tighter unknown endpoint budget in adaptive |
| `context_minimization` | `agent_runtime` | `security.context_overshare_incidents` | always-on secret redaction -> adaptive masking expansion with canary |
| `scope_enforcement` | `policy_engine` | `security.scope_escalation_prevented_total` | read checks now -> strict write scopes + fail-closed mode |
| `abuse_kill_switch` | `sre_oncall` | `security.kill_switch_time_to_mitigate_seconds` | enable in adaptive/strict, exercise weekly in game-days |

## Runtime Contract

`workers/src/kura_workers/system_config.py` now publishes
`agent_behavior.operational.security_tiering` with:

- threat matrix (`threat_matrix`)
- profile definitions (`profiles`)
- switch catalog (`switch_catalog`)
- explicit profile progression (`default -> adaptive -> strict`)

This keeps policy semantics machine-readable for agent/runtime consumers.
