# Decision log

Orchestrator-maintained. Newest last. Format: date, decision, reason.

| # | Date | Decision | Reason |
|---|---|---|---|
| D1 | 2026-11-15 | Each step runs in its own git worktree on branch `feat/sNN-name`; the orchestrator merges to `master` (fast-forward/merge commit) wave by wave. | Matches PLAN.md rules 1–2; keeps the merge point single. |
| D2 | 2026-11-15 | Step status is tracked in the PLAN.md table via a `Status` column, updated only by the orchestrator after merge. | Avoids merge conflicts on PLAN.md between parallel agents. |
| D3 | 2026-11-15 | Stay small: standard library + existing deps only unless a step genuinely needs more; no new frameworks for plumbing. | "Small but extensible" priority. |
