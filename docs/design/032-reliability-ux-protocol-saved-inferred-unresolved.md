# Decision 13 Reliability UX Protocol V1 (m3k.1)

Date: 2026-02-12  
Owner: Team (JZ + Codex)  
Issue: `kura-training-m3k.1`

## Problem

`write-with-proof` already separates receipt/read-after-write status and session-audit outcomes, but user-facing wording can still overstate certainty when values are inferred or unresolved conflicts remain.

## Goal

Introduce an explicit post-write response contract with three mutually exclusive states:

- `saved`: persistence verified (receipt + read-after-write).
- `inferred`: persistence verified, but at least one fact is inferred and must expose confidence/provenance.
- `unresolved`: do not claim saved; ask exactly one conflict-focused clarification question.

## API Contract Additions

`AgentWriteWithProofResponse` now includes `reliability_ux`:

- `state`: `saved | inferred | unresolved`
- `assistant_phrase`: canonical wording for the state
- `inferred_facts[]`: `{ field, confidence, provenance }` (only when relevant)
- `clarification_question`: single concise question (only unresolved)

## State Derivation Rules

1. `unresolved` if:
   - `claim_guard.allow_saved_claim == false`, or
   - `session_audit.status == needs_clarification`.
2. `inferred` if:
   - write proof is verified, and
   - inferred evidence exists (`evidence.claim.logged`) or deterministic repair provenance is inferred.
3. `saved` otherwise.

Priority order is strict: `unresolved` > `inferred` > `saved`.

## Clarification Prompt Rule

Clarification prompts are conflict-first and short:

- conflict case: `Konflikt bei <scope>: <field> = <a>|<b>. Welcher Wert stimmt?`
- single-value confirm case: `Bitte bestätigen: <field> bei <scope> = <value>?`

Only one clarification question is allowed per turn.

## Anti-Patterns (Rejected)

- Saying "saved/logged" while proof is pending.
- Presenting inferred values without confidence/provenance.
- Asking broad multi-question clarifications for one concrete mismatch.

## Compatibility Constraints

The protocol is additive and must preserve existing override controls:

- `workflow_gate.override`
- `autonomy_policy.max_scope_level`
- confirmation templates from `confirmation_template_catalog`

## Tests

Added/updated tests cover:

- `saved` state for verified writes with no inferred facts.
- `inferred` state with confidence + provenance extraction.
- `unresolved` state preferring conflict clarification prompts.
- system-config contract checks for state matrix and override-hook compatibility.
