# SRMA 100-agent metadata enrichment and screening preparation

This workflow splits the verified prospective-search master (1,129 unique records) across 100 computational agents.

Each agent:
- normalizes identifiers and metadata;
- attempts missing-metadata enrichment through Europe PMC and Crossref;
- applies the existing protocol-grounded machine triage rules;
- leaves all formal human-review fields blank / `Not reviewed`.

The consolidation job verifies all 100 agent outputs, enforces one row per master record, creates an exact-identity duplicate-candidate audit, and generates a prioritized queue for subsequent human screening.

This is not independent duplicate human screening and must not be reported as such.
