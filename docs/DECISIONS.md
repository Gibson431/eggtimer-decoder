# Decision log

Orchestrator-maintained. Newest last. Format: date, decision, reason.

| # | Date | Decision | Reason |
|---|---|---|---|
| D1 | 2026-11-15 | Each step runs in its own git worktree on branch `feat/sNN-name`; the orchestrator merges to `master` (fast-forward/merge commit) wave by wave. | Matches PLAN.md rules 1–2; keeps the merge point single. |
| D2 | 2026-11-15 | Step status is tracked in the PLAN.md table via a `Status` column, updated only by the orchestrator after merge. | Avoids merge conflicts on PLAN.md between parallel agents. |
| D3 | 2026-11-15 | Stay small: standard library + existing deps only unless a step genuinely needs more; no new frameworks for plumbing. | "Small but extensible" priority. |
| D4 | 2026-11-15 | S1: a `Channel` stores GFSK fields flat; `RadioConfig` stays the receiver's working type, derived via `Channel.radio_config()`. config.py untouched. | Minimal change, no refactor risk. |
| D5 | 2026-11-15 | Channel offset is `offset_hz` = signal − center; default center `CENTER_FREQ_HZ` = 921 MHz. CLI `--freq` default (920.99e6) must be moved to 921 MHz + channel offset by S7/S10. | Matches PLAN assumptions. |
| D6 | 2026-11-15 | Added `verified` flag on channels (0 verified, 1–8 not). Invalid channel raises `ValueError`. | Makes unverified copies visible; no custom exception type needed. |
| D7 | 2026-11-15 | S2: `FrameDetector.feed(bits)->list[bytes]` with bounded state; `find_frames` wraps it. A pending valid-length sync candidate blocks later ones until resolved (keeps batch-identical ordering). | Chunking-invariant output. |
| D8 | 2026-11-15 | `FrameDetector` is not thread-safe; S8 keeps one per receiver and calls `reset()` on channel/frequency change. S5 should feed reasonably sized pieces, not single bits. | Cost/safety notes from S2. |
