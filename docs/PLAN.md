# Implementation Plan

## Product goal

A decoder app where the user chooses the SDR, the center frequency (default 921 MHz) and a channel (0 to 8). Each channel maps internally to a set of GFSK parameters plus a frequency offset from the center frequency. The user can change these settings while the app is running.

## Principles

- **Live operation.** The app receives continuously until stopped, rather than capturing for a fixed time and then decoding.
- **Channels are data.** A channel is a named set of parameters (GFSK parameters and a frequency offset), held in a table. Adding or changing a channel never requires changing decoding logic.
- **The core is separate from the interface.** The decoding core knows nothing about how it is controlled, so the CLI and GUI are thin layers on top of it.
- **No hardware needed for testing.** Every step must be verifiable with the simulated signal source. Tests that need a real SDR are opt-in.

## Steps

Each step gets one worktree and branch (`feat/sNN-name`).

| ID | Status | Task | Depends on | Completion metric |
|---|---|---|---|---|
| **S0** | done | Define the contracts between the major parts: what events the core produces (frames, NMEA sentences, positions, status), what controls it (start, stop, change frequency, channel or device, subscribe to events) and what a sample source provides. Contracts only, no behavior. | none | Contracts exist, can be imported by other work without error, and document the expected behavior of live changes and shutdown. |
| **S1** | done | Define channels 0 to 8. Channel 0 is the current measured setup, including a frequency offset of −10 kHz from the center frequency (signal at 920.99 MHz with a 921 MHz center). Channels 1 to 8 start as copies of channel 0. Derive the receiver's working parameters from a chosen channel. Adding or editing a channel, including its offset, must be a data-only change that needs no change to decoding logic. | none | Asking for a channel outside 0 to 8 is rejected. All 9 channels resolve to channel 0's parameters and offset. A test shows that changing one channel's parameters or offset affects only that channel. Contributor docs explain how to add a verified channel. |
| **S2** | done | Make frame detection work on a continuous bit stream arriving in pieces, including frames split across pieces and either bit polarity. | none | Feeding the same bits in any piece sizes produces identical frames to processing them all at once. All existing packet tests still pass. |
| **S3** | done | Let the app discover attached SDRs and receive from a chosen one, with configurable gain and frequency correction. Report each device's supported frequency range and sample rates. Any device the existing SDR library supports is allowed, and only the RTL-SDR is tested. | S0 | With no SDR attached, discovery reports an empty list and does not error. Device selection and rate choice are covered by tests that use a stand-in device. |
| **S4** | done | Provide a simulated live signal source that behaves like a real device. For any channel 0 to 8 and any center frequency, it places the signal at the channel's offset, with a chosen SNR and extra carrier error. | S0, S1 | A valid, decodable signal can be produced for each of the 9 channels at its offset. Running it for over 10 s does not grow memory. |
| **S5** | todo | Turn the received signal into a continuous stream of bits for the selected channel, picking out the channel at its offset from the center frequency. Allow switching channel without reopening the device. | S1, S2 | For each of channels 0 to 8, simulated input at 20 dB SNR yields the expected frames end-to-end. Switching channel mid-stream resumes correct decoding within a stated number of frames. |
| **S6** | done | Measure signal quality continuously: signal strength, carrier offset and whether a signal is present. | S0 | Reported values match known values on synthetic signals and noise within stated tolerances. |
| **S7** | done | Hold the user's settings (device, center frequency with 921 MHz as the default, channel, gain, frequency correction, output options), validate them, and save and restore them between runs. Command line options override environment settings, which override the saved file, which overrides defaults. | S1 | Settings survive a save and reload unchanged. Invalid values are rejected with a clear message. A missing or corrupt saved file falls back to defaults without crashing. |
| **S8** | todo | Combine source, demodulation, frame detection, NMEA reassembly and signal metrics into one running receiver that fulfils the S0 contracts. Frequency and channel changes apply while running, and a device change restarts reception. | S2, S3, S4, S5, S6, S7 | A simulated run covers start, a channel change, a frequency change and stop, and produces the expected events. Repeated start and stop leaves nothing running. A source failure is reported as a status event rather than a crash. |
| **S9** | done | Provide ways to consume the receiver's events: human-readable console output (matching today's), a machine-readable log file, and a record of the latest known position. | S0 | Each output matches a reference result for a fixed set of events. |
| **S10** | todo | Provide the application entry point. With arguments, it runs as a CLI that dumps decoded packets as they arrive. The options choose the device and set the center frequency and channel. Other commands list devices, list channels with their parameters and offsets, and view or change saved settings. With no arguments, it opens the GUI (S12). The simulated source stays available so the app can be tried without hardware. | S7, S8, S9 | Running with the simulated source on channel 3 prints the expected packets. Listing devices with no SDR attached succeeds and says none were found. Each command has help text. Running with no arguments hands over to the GUI, and until S12 lands it says the GUI isn't available yet and exits cleanly. |
| **S11** | todo | Add end-to-end tests: all 9 channels through the whole receiver, a live reconfigure, and the CLI itself. Add an opt-in test and a manual procedure for a real SDR at a 921 MHz center on channel 0. | S10 | The full test suite passes with no SDR attached. The real-SDR test is skipped unless explicitly enabled. |
| **S12** | todo | Provide a graphical window where the user picks the device, enters the center frequency, selects the channel, and sees live status, decoded packets and the latest position. It changes settings through the receiver only. | S7, S8, S9, S10 | Every setting can be changed while receiving, and persists across restarts. The window opens when the app is launched with no arguments. Automated GUI tests pass without an SDR. |
| **S13** | todo | Update the documentation and project tasks to describe the finished app (CLI, GUI, channel table and how to extend it), and correct any outdated instructions. | S11, S12 | Someone following only the README from a fresh clone can install, run the simulated source on a chosen channel with the CLI, and open the GUI. |

## Dependency graph and waves

```
Wave 1:  S0   S1   S2          (no dependencies; run in parallel)
Wave 2:  S3←S0   S4←S0,S1   S5←S1,S2   S6←S0   S7←S1   S9←S0   (parallel)
Wave 3:  S8←S2,S3,S4,S5,S6,S7
Wave 4:  S10←S7,S8,S9
Wave 5:  S11←S10      S12←S7,S8,S9,S10   (parallel)
Wave 6:  S13←S11,S12
```

The critical path is S1 → S5 → S8 → S10 → S12 → S13.

## Rules for the subagents

1. Branch from `master`, and rebase onto the S0 contracts once they land.
2. Merge by wave. A step starts only when everything in its Depends-on column is merged.
3. Each subagent decides its own code layout, following the repo's existing style. Where steps touch shared files, keep those edits minimal and resolve conflicts by rebasing.
4. Any change to the S0 contracts goes in its own small PR, merged before dependent work continues.
5. A step is done only when its completion metric is met and the existing test suite still passes. Nothing may be skipped silently, and no step may require hardware or network access.
6. **Verifying that the channel really drives the decoder.** All 9 channels currently have identical parameters, so S4 and S5 must also test with a temporary extra channel whose parameters or offset differ. This proves the channel's values actually reach the decoder, rather than every channel silently falling back to channel 0.

## Assumptions

- **Nine channels:** 0 to 8.
- **The offset is relative to the center frequency.** Channel 0's offset is −10 kHz, from 920.99 MHz against a 921 MHz center.
- **"GUI" means a desktop window.** The toolkit choice is left to the S12 subagent.
