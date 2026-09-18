# MEMORY.md — Project State of Record

> **Protocol:** Read this file in full before every action. Update it after every file edit. If this file and the code disagree, the code wins — reconcile immediately.

_Last updated: 2026-09-19_

---

## Current Goal
End-to-end smoke test on real hardware with the HUD live: install system Tk (`brew install python-tk`), `uv sync --extra groq --extra mlx --extra dev`, populate `.env`, `uv run speako`. Verify the pill overlay appears bottom-center, bars respond to voice amplitude, spinner shows while cloud round-trips, and green check flashes after paste. Report observed p50/p95 latency into `MEMORY.md`.

## Architecture Decisions

- **AD-001 — Local engine dispatch by `platform.machine()`.**
  Apple Silicon (`arm64` on Darwin) → `mlx-whisper`; Intel/AMD (`x86_64`/`amd64`) → `faster-whisper`. Detected once in `LocalEngineFactory.detect()` and cached. Anything else raises `UnsupportedHardwareError`.

- **AD-002 — Four-state key lifecycle (`ACTIVE` / `DEGRADED` / `COOLDOWN` / `INACTIVE`).**
  First transient failure demotes ACTIVE → DEGRADED; second consecutive transient (or quota / fatal) sends → COOLDOWN with exponential backoff; auth failure → INACTIVE. Code: `speako/transcribe/keys.py`.

- **AD-003 — Exponential cooldown `initial * factor**step`, capped at `max_seconds`.**
  Defaults: 30s initial, 4× factor, 600s cap. Uses `time.monotonic()` — no background thread.

- **AD-004 — `BaseTranscriber` is a `typing.Protocol` (`runtime_checkable`).**
  Structural typing lets local engines (no key) and cloud engines share one shape via `KeyHandle | None`.

- **AD-005 — Domain error taxonomy owned inside each provider strategy.**
  `{RateLimitError, QuotaExhaustedError, AuthError, TransientProviderError, FatalProviderError}` — SDK exceptions never leak.

- **AD-006 — In-memory WAV via stdlib `wave` module.**
  Clip → int16 PCM → `io.BytesIO`. No `soundfile` dep, no disk I/O in hot path.

- **AD-007 — Configuration precedence: defaults < project YAML < user YAML < env < CLI.**
  Secrets never from YAML — only from env vars named by `api_keys_env`.

- **AD-008 — `StructuredLogger` wrapper over stdlib `logging`.**
  Rationale: stdlib `Logger.warning(msg, foo=bar)` raises. Wrapper promotes non-passthrough kwargs to `extra=`. Code: `speako/util/logging.py`.

- **AD-009 — Drop-oldest bounded queues on hot paths (with one exception).**
  Hotkey sink queue, RMS `level_sink` queue, and `hud_events` queue all use `put_nowait` and drop-oldest on overflow. Never block the pynput / Tk polling threads.
  **Exception:** the audio capture queue is unbounded during a single capture and uses drop-*newest* at the `max_seconds` cap — see AD-022 for why drop-oldest was wrong there.

- **AD-010 — Python ≥ 3.12, use `uv` for env/deps.**
  Numpy 2.5+ requires 3.12 (uses `type` statements in stubs).

- **AD-011 — Dispatcher retries up to `max_attempts_per_provider` (default 3) per provider before falling through.**
  Each retry acquires a fresh key from `KeyManager` — no same-key retries. On `NoLiveKeysError`, provider skipped immediately.

- **AD-012 — Hotkey name aliases.**
  Config accepts friendly aliases (`right_option`, `right_cmd`, `option`, ...) which resolve to pynput canonicals (`alt_r`, `cmd_r`, `alt`, ...) via a static map in `speako/audio/hotkey.py`. Rationale: the intuitive Mac name doesn't match pynput's naming; fixing that at the boundary is cheaper than every user re-learning.

- **AD-013 — Deprecated `google-generativeai` replaced with `google-genai`.**
  The old package was deprecated in 2024–2025. New SDK: `from google import genai; client = genai.Client(api_key=...); client.models.generate_content(...)`. Error mapping via `google.genai.errors.APIError.code`.

- **AD-014 — HUD runs on the process main thread; runtime moves to a worker.**
  Tk requires main-thread ownership on macOS (Cocoa restriction) and is strongly recommended everywhere. When `ui.enabled: true`, `__main__` spawns `speako-runtime` as a worker and runs `Hud.run()` on the main thread. When disabled (`--no-hud` / `ui.enabled: false`), the runtime stays on the main thread and no GUI is created. See `ARCHITECTURE.md` §1.

- **AD-015 — Single `hud_events: Queue[HudEvent]` bus for the HUD.**
  Union of `HudStateEvent | HudLevelEvent | HudShutdown`. Types live in `speako/ui/events.py` with no Tk import so audio and runtime can produce events without a GUI dependency. Level events come from a 1-line `speako-level-pump` daemon that wraps floats from the capturer's `level_sink` — keeps `AudioCapturer` free of any UI type.

- **AD-016 — RMS meter uses rolling noise floor + asymmetric smoothing.**
  Floor drifts *only* downward (EMA α=0.03 on sub-floor observations) so a quiet room adapts but transient loud speech doesn't push the floor up. Level output: fast attack (α=0.55), slow release (α=0.18) — matches user intuition of "spike then decay". Full scale = floor + 35 dB. Code: `speako/audio/level.py`.

- **AD-017 — HUD falls back gracefully when Tk is missing.**
  `Hud.run()` catches `tk.TclError` on init and raises `HudUnavailableError` with an actionable message. `__main__._run_with_hud` catches it, logs `hud_unavailable_falling_back_headless`, and waits on `stop` while the runtime worker keeps transcribing without the overlay. Rationale: uv's bundled cpython omits Tk; forcing the whole app to die would be user-hostile.

- **AD-018 — HUD renders with Tkinter Canvas, not a heavier toolkit.**
  Tkinter is stdlib (no dep) and enough for a 240×64 pill with dot / bars / spinner / check. PyQt/PySide would add ~50 MB and platform-specific packaging headaches. Trade-off: no per-pixel transparency (whole-window alpha only) and rounded corners are drawn via `create_arc` + `create_rectangle` compositing.

- **AD-019 — HUD hides via alpha, never via `withdraw` / `deiconify`.**
  On macOS the withdraw/deiconify cycle activates the window when it comes back, which (a) steals focus from whatever the user was typing into, breaking paste, and (b) drags the current Space back to wherever the HUD lives. Alpha-only visibility (`wm_attributes "-alpha" 0.0` → hidden, `opacity` → visible) is a pure compositor operation with none of those side effects. The window is created once and stays realized for the process lifetime.

- **AD-020 — macOS HUD uses pyobjc to reach the NSWindow directly.**
  Tkinter can't set the NSWindow collectionBehavior, level, or IgnoresMouseEvents — all critical for a system-style HUD. `_apply_platform_hud_behavior` (in `speako/ui/hud.py`) finds the NSWindow via `NSApp.windows()` matched on the unique title `__speako_hud__`, then sets: `CanJoinAllSpaces | Stationary | IgnoresCycle | FullScreenAuxiliary` (visible on every Space, doesn't animate with Space transitions, skipped by window-cycle, and *can* appear over same-app fullscreen), `NSPopUpMenuWindowLevel` (101 — above every other app's windows *including their fullscreen windows*, below the system menu bar), `ignoresMouseEvents = YES` (clicks pass through). pyobjc is transitively installed by pynput on macOS, so no new declared dep.

  **Level choice history:** originally `NSStatusWindowLevel` (25). That was enough for regular windows but macOS renders another app's fullscreen Space above status level, so the HUD was invisible over e.g. YouTube fullscreen in Brave. `NSPopUpMenuWindowLevel` (101) is the standard trick used by menu-bar utilities (Rectangle, Karabiner HUD) to render over any other app's fullscreen. Going to `NSScreenSaverWindowLevel` (1000) would also cover the system menu bar and notifications, which we do *not* want.

- **AD-021 — macOS process is downgraded to `NSApplicationActivationPolicyAccessory`.**
  Setting NSWindow `collectionBehavior` alone is **not enough**. The moment `Tk()` runs, Cocoa promotes the Python process to a `Regular` (foreground GUI) application. Every subsequent HUD update is then treated by macOS as a foreground-app event that must occur on the app's origin Space — which drags the user back whenever the HUD state changes. Fix: call `NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)` (value 1) **before** the NSWindow config. `Accessory` = background agent: no Dock icon, no Cmd-Tab entry, no Space bindings. The window's `collectionBehavior` still governs where it appears; the process policy governs whether macOS thinks a Space change is warranted when it updates. Both are required together. Verified: `activationPolicy()` transitions from 2 (Prohibited) to 1 (Accessory) after the call.

- **AD-022 — Audio capture queue: unbounded + drop-*newest* at the cap.**
  Original design (per AD-009) used a bounded `queue.Queue` with drop-*oldest* on overflow. The maxsize was sized as `20 chunks/s × max_seconds` = 2400 chunks at defaults, but sounddevice's default macOS blocksize (~256 samples at 16 kHz) yields ~62 chunks/s. Queue filled after ~38 s; every subsequent chunk pushed the oldest one out. **Symptom:** users speaking longer than ~38 s got only the last few words transcribed while the HUD kept showing LISTENING — the state machine was fine, but the pipeline was silently truncating the *start* of the recording.

  Fix (in `speako/audio/capturer.py`):
  * Queue is unbounded — `put_nowait` on an unbounded queue never raises.
  * Memory is bounded by counting samples inside the callback: once `buffered_samples >= sample_rate × max_seconds` we drop **newest** chunks (the tail past the cap) and log once via `audio_max_seconds_reached`.
  * Level publication still runs for every chunk (even past the cap) so the HUD bars keep responding to the user's voice.
  * `_dropped_at_cap` counter is emitted at `end_capture` time as `audio_capture_truncated_at_max_seconds` so operators can tell when the cap was hit.

  Trade-off: user speaking longer than `max_seconds` loses the tail rather than the start. Truncated-tail is far less confusing than truncated-start ("only my last sentence appeared" vs "everything I said appeared, but it cut off at 2 minutes"). Users needing longer captures raise `audio.max_seconds` in config.

## Completed
- [x] Scaffolding: `pyproject.toml` (per-provider extras + dev), `.gitignore`, `.env.example`, `config.yaml.example`, `README.md`, `LICENSE`.
- [x] `speako/util/logging.py` — `StructuredLogger` + `_JsonFormatter`.
- [x] `speako/util/notify.py` — macOS `osascript` / Linux `notify-send`.
- [x] `speako/config/models.py` + `loader.py` — frozen dataclasses, YAML precedence, env-only secrets, validation. Now includes `UIConfig`.
- [x] `speako/audio/clip.py` — `AudioClip` + stdlib WAV encoding + mono downmix.
- [x] `speako/audio/capturer.py` — sounddevice InputStream, drop-oldest frame queue, RMS metering with optional `level_sink`.
- [x] `speako/audio/level.py` — `RmsLevelMeter` (rolling noise floor, attack/release smoothing).
- [x] `speako/audio/hotkey.py` — pynput listener, hold/toggle, alias map (`right_option → alt_r`, ...).
- [x] `speako/transcribe/base.py` — `BaseTranscriber` Protocol, `KeyHandle`, error taxonomy.
- [x] `speako/transcribe/keys.py` — thread-safe `KeyManager`, 4-state machine, round-robin, exponential backoff.
- [x] `speako/transcribe/dispatcher.py` — retry-and-rotate per provider, terminal fallthrough to local.
- [x] `speako/transcribe/groq_engine.py` — Groq with SDK error mapping, string-response handling.
- [x] `speako/transcribe/gemini_engine.py` — `google-genai` (new SDK) with inline WAV upload.
- [x] `speako/transcribe/local/{factory,mlx,faster}.py` — hardware-aware local backend.
- [x] `speako/output/injector.py` — clipboard + paste chord or type; optional clipboard restore.
- [x] `speako/ui/events.py` — `HudState` enum + event dataclasses (Tk-free).
- [x] `speako/ui/hud.py` — Tkinter frameless topmost pill; LISTENING (dot + 5 dancing bars), TRANSCRIBING (spinner + halo), PASTED (checkmark + fade); `HudUnavailableError` for missing Tk.
- [x] `speako/app.py` — `AppContext` + wiring; level-pump daemon; graceful provider degradation.
- [x] `speako/runtime.py` — main loop, HUD state emissions, shutdown sentinel.
- [x] `speako/__main__.py` — argparse (`--no-hud`), Tk-on-main / runtime-on-worker split, HUD unavailable fallback.
- [x] Tests (45 passing): config loader, UI config, hotkey parse, audio clip, RMS meter, HUD event apply, KeyManager, local factory.
- [x] Verified: `uv run ruff check` clean, `uv run mypy speako tests` strict-clean on 39 files, `uv run pytest -q` 45 passed.
- [x] Docs synced: `AGENT.md`, `ARCHITECTURE.md` (UI event bus + RMS meter sections), `PATTERNS.md`, `README.md` (HUD section, `--no-hud`, Tk-install troubleshooting), this file.

## Pending Tasks
1. **End-to-end smoke test with HUD.** See Current Goal.
2. **Latency instrumentation** — per-stage log line (capture ms, WAV encode ms, provider ms, insert ms).
3. **Provider-level tests with mocked SDK responses** to exercise every error-mapping branch.
4. **CI hook** — GitHub Actions workflow gating merges on `ruff check`, `mypy --strict`, `pytest`.
5. **VAD pre-filter** — evaluate after smoke test measures baseline latency.
6. **HUD polish** — real per-pixel transparency (PyQt fallback?), animated fade-in on state changes, retina scale factor.
7. **Publish / packaging** — PyPI + optional standalone launcher bundle so users don't need `uv` to run.

## Notes / Open Questions
- `uv`'s bundled cpython omits Tk. Currently we fall back gracefully; longer term consider recommending `uv python pin` to a system Python where Tk is present.
- macOS Accessibility permission is needed for both key observation (pynput) and key injection (paste). One prompt covers both, but the user has to know to accept it.
- HUD click behavior: currently swallows Button-1 with a no-op. Consider making it draggable so users can reposition without editing config.
- Toggle-mode hotkey debounces OS auto-repeat but not rapid tap-tap sequences — measure real-world impact before adding more debouncing.
