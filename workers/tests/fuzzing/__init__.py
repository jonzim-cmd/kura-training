"""Adversarial fuzzing harness for Kura Training API invariants.

Three layers:
1. Property-based (Hypothesis) — generates random valid/invalid payloads
2. LLM Adversarial (Claude) — generates creative edge cases
3. Transcript Regression — replays known failures as fixtures
"""
