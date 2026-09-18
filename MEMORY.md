# MEMORY.md — Project State of Record

> **Protocol:** Read this file in full before every action. Update it after every file edit. If this file and the code disagree, the code wins — reconcile immediately.

_Last updated: 2026-09-19_

---

## Current Goal
Environment setup and configuration schemas. Specifically: lock down the `AppConfig` dataclass (providers, key pools, hotkey, audio device, local-engine preferences), the `.env` + `config.yaml` loader precedence, and the `.env.example` template. No transcription code yet.

## Architecture Decisions

- **AD-001 — Local engine dispatch by `platform.machine()`.**
  Apple Silicon (`arm64` on Darwin) → `mlx-whisper`; Intel/AMD (`x86_64`) → `faster-whisper`. Decision made because MLX exploits ANE/Metal for a 3–5× speedup on M-series and CTranslate2 dominates on CPU. Detected once at startup, cached on `AppContext`. See `ARCHITECTURE.md` §4.

- **AD-002 — Four-state key lifecycle (`ACTIVE` / `DEGRADED` / `COOLDOWN` / `INACTIVE`).**
  Chosen over a binary open/closed breaker so a single transient blip does not immediately quarantine a key. `DEGRADED` allows continued use with deprioritization; escalation to `COOLDOWN` requires two consecutive failures. `INACTIVE` is terminal for the session and only entered via auth failure. See `ARCHITECTURE.md` §5.

- **AD-003 — Exponential cooldown 30s → 120s → 600s (capped).**
  Balances provider recovery time against user-visible latency spikes. Cap prevents indefinite quarantine on flaky-but-live keys.

- **AD-004 — Provider strategies are `typing.Protocol`, not `ABC`.**
  Structural typing keeps local engines (which have no API key) and cloud engines behind the same interface without inheritance ceremony. `KeyHandle | None` in the signature encodes this cleanly. See `PATTERNS.md` §1.

- **AD-005 — Domain error taxonomy owned by the dispatcher layer, not the SDKs.**
  Each provider strategy maps SDK-native exceptions to `{RateLimitError, QuotaExhaustedError, AuthError, TransientProviderError, FatalProviderError}`. Dispatcher and `KeyManager` only ever see domain errors. See `PATTERNS.md` §6.

- **AD-006 — Audio buffer is not disk-backed.**
  In-memory `numpy` float32 → `io.BytesIO` (WAV) for SDK calls. Temp files are a last-resort fallback and live on tmpfs. Rationale: eliminates ~10–30ms of I/O per capture and avoids leftover recordings on disk.

- **AD-007 — Configuration precedence.**
  `config.yaml` (project) < user config (`~/.config/speako/config.yaml`) < environment (`.env` / shell) < CLI flags. Secrets never resolve from YAML; only from `.env` or OS keychain. See `ARCHITECTURE.md` §7.

## Completed
_(none)_

## Pending Tasks
1. **Configuration** — define `AppConfig` dataclass, YAML schema, `.env.example`, loader with precedence + validation, unit tests for the loader.
2. **Audio Engine** — `AudioCapturer` around `sounddevice.InputStream`, bounded frame queue, `AudioClip` assembly on release.
3. **Hotkey Listener** — `pynput` global listener, debouncing, PRESS/RELEASE event publication, tap-vs-hold policy.
4. **KeyManager** — pool structure, state machine, round-robin selection within tier, monotonic-clock cooldown, unit tests covering every transition.
5. **Transcriber Implementations** — Groq (`whisper-large-v3` / `whisper-large-v3-turbo`), Gemini (`gemini-2.0-flash` / `gemini-1.5-flash`), MLX-Whisper, faster-whisper. Each behind the `BaseTranscriber` Protocol with its own error-mapping helper.
6. **Transcriber Dispatcher** — provider priority chain, key acquisition, retry-with-rotation, terminal fallback to local engine.
7. **Output Injector** — clipboard write + platform-specific paste/type (macOS via `pyobjc`, Linux via `xdotool`/`wtype`, Windows via `pynput`).
8. **Entrypoint & AppContext** — `__main__.py` wiring, structured logging, notifier, graceful shutdown.
9. **Packaging** — `pyproject.toml`, optional dependency groups per provider, launcher script.

## Notes / Open Questions
- Tap-vs-hold hotkey policy: single-tap toggle vs. hold-to-record. Default to hold; revisit after first end-to-end run.
- Whether to add a VAD pre-filter before transcription. Deferred until we measure baseline latency.
- macOS accessibility permissions flow: needs first-run detection and a clear user message. Track in the OutputInjector task.
