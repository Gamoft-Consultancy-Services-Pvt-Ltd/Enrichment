# ADR 0003 — Deterministic scoring via temperature 0 and pinned prompt version

- **Status:** Accepted
- **Date:** 2026-05-30

## Context

Scoring must be deterministic and explainable: identical lead data plus identical
config and prompt version must produce an identical score, and every score must
carry a reasoning trace. Scoring is performed by an LLM (Anthropic Sonnet), which
is inherently non-deterministic.

## Decision

Achieve determinism by **constraining the model**: call Sonnet with temperature 0
and a pinned active prompt version (resolved from `prompt_registry`). This is a
best-effort guarantee — highly consistent in practice, not a mathematical one.

The stronger guarantee — score once and cache the result by
`(lead_data_hash, prompt_template_version)` so the model is never re-invoked for
the same input — is **deferred** to later.

## Consequences

- Minimal infrastructure now: determinism is a property of how we call the model,
  not a separate cache/store.
- Identical re-scoring still re-calls the model, so it incurs LLM cost and carries
  a small residual risk of variation.
- When cost or strict reproducibility demands it, add the scored-result cache
  keyed by lead fingerprint + prompt version (the deferred option), which makes
  repeat scores free and exactly reproducible.
- Determinism depends on the prompt version being pinned at score time; a prompt
  re-run produces a new version (see ADR 0001 context on prompt lifecycle) and is
  expected to change scores.
