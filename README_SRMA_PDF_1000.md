# SRMA 1,000-lane PDF retrieval rerun

- Target: 1,357 records without a saved PDF after the first 100-lane retrieval.
- Computational lanes: 1,000 (`PDF-AGENT-0001` to `PDF-AGENT-1000`).
- Distribution: 357 lanes have two records; 643 lanes have one record.
- Workflows: four shards of 250 jobs each (GitHub matrix limit is 256 jobs per workflow run).
- Workflow `max-parallel`: 5 per shard; at most 20 jobs are intentionally active across all four shards before account-level limits.
- Discovery routes: existing URLs, DOI landing metadata, OpenAlex OA locations, Crossref links, Europe PMC/PMC, and high-similarity OpenAlex title search.
- No paywall, login, CAPTCHA, robots restriction, or access control is bypassed.
- Every attempt is auditable in `attempt_log_json`.
- Artifacts are retained for 14 days.

## Important

A lane is a computational work unit, not a human reviewer. Retrieval success or failure is not an eligibility decision.
