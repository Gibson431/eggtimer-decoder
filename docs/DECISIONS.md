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
| D9 | 2026-11-15 | S0: events are frozen dataclasses (Frame/Nmea/Position/Status), consumers dispatch on type and ignore unknown ones. `SampleSource.read(n)` is a blocking pull returning complex64; empty array = end. Receiver events delivered on a receiver-owned thread; subscriber exceptions logged, not propagated. | Simple, extensible. |
| D10 | 2026-11-15 | `Receiver` has `set_frequency/set_channel/set_device` only; gain/ppm are source configuration (S3/S7). Device change restarts reception. | Keeps contract small. |
| D11 | 2026-11-15 | Orchestrator added `SignalEvent(strength_db, carrier_offset_hz, present)` to the S0 union as its own small commit (PLAN rule 4) so S6/S8/S12 can rely on it. | S6 metrics need a transport to consumers. |
| D12 | 2026-11-15 | S7: `Settings` frozen dataclass; `resolve_settings` is a pure merge cli > env (`EGGTIMER_*`) > file > defaults; `None` in cli means "not given" (so S10 needs an explicit `auto` token if it wants to reset gain). | Simple, testable precedence. |
| D13 | 2026-11-15 | A saved settings file is validated as a whole: any bad value or unknown key discards the whole file with a warning. | No half-applied state. |
| D14 | 2026-11-15 | Settings store `center_freq_hz` + `channel` separately; `device` is a free-form string (`sim` = simulated source, empty = first available). Range limits (freq 1 MHz–6 GHz, gain 0–100, ppm ±1000) are sanity bounds. | Per D5; S8/S10 give device meaning. |
