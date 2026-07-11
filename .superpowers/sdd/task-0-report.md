status: DONE_WITH_CONCERNS
commit_shas:
  - c777534ecd8725398a5dbff4d17c77dfbbe15aba
test_summary: "Targeted + full verification passed: test_hub_prereqs (4 passed), full tests (214 passed, 4 skipped), import-linter contracts kept."
concerns:
  - "`lint-imports` is not on PATH in this shell; equivalent check succeeded via explicit executable path."
---
task_0_review_fixes:
  branch: feature/ugs-hub
  changes:
    - "Documented §7.5.4 replay dedupe: `verbs.relate` explicitly raises `ValueError('duplicate edge: ...')` for duplicate edges; `_already_applied` is not the guard for relate replay."
    - "Added a 'Hub / offline replay' subsection in docs/DEVELOPMENT.md clarifying duplicate-edge replay behavior in 2-3 sentences."
    - "Encapsulated declare idempotency semantics in `AssetcoreService.declare` via `DeclareResult(id, created)` so the route no longer calls `service.repo.get_asset` directly."
    - "Updated `/assets` route to set 201 vs 200 from service result (`created`) while returning the existing id for client-supplied duplicate declare."
  verification:
    - "python -m pytest tests/integration/test_hub_prereqs.py -v"
    - "python -m pytest tests/ -v"
