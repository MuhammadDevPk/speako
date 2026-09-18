# MEMORY.md — Project State of Record

> **Protocol:** Read this file in full before every action. Update it after every file edit. If this file and the code disagree, the code wins — reconcile immediately.

_Last updated: 2026-09-19_

---

## Current Goal
End-to-end smoke test on real hardware: `uv sync --extra groq --extra mlx --extra dev`, populate `.env`, run `uv run speako`, verify hold-to-record → transcribe → paste with at least one cloud provider and the local fallback. Feed observed latency and any hotkey/permissions surprises back into config defaults.

## Architecture Decisions

- **AD-001 — Local engine dispatch by `platform.machine()`.**
  Apple Silicon (`arm64` on Darwin) → `mlx-whisper`; Intel/AMD (`x86_64`/`amd64`) → `faster-whisper`. Detected once in `LocalEngineFactory.detect()` and cached. Anything else raises `UnsupportedHardwareError`. See `ARCHITECTURE.md` §4, code: `speako/transcribe/local/factory.py`.

- **AD-002 — Four-state key lifecycle (`ACTIVE` / `DEGRADED` / `COOLDOWN` / `INACTIVE`).**
  First transient failure demotes ACTIVE → DEGRADED; second consecutive transient (or quota / fatal) sends → COOLDOWN with exponential backoff; auth failure → INACTIVE (terminal for session). Success from any recoverable state resets to ACTIVE. Code: `speako/transcribe/keys.py`.

- **AD-003 — Exponential cooldown `initial * factor**step`, capped at `max_seconds`.**
  Defaults: 30s initial, 4× factor, 600s cap. Configurable per install via `cooldown` YAML block. Uses `time.monotonic()` — no background thread.

- **AD-004 — `BaseTranscriber` is a `typing.Protocol` (`runtime_checkable`).**
  Structural typing lets local engines (no key) and cloud engines share one shape via `KeyHandle | None`. No inheritance ceremony.

- **AD-005 — Domain error taxonomy owned inside each provider strategy.**
  `{RateLimitError, QuotaExhaustedError, AuthError, TransientProviderError, FatalProviderError}` — provider SDK exceptions never leak out of the strategy. Dispatcher and `KeyManager` react only to these types.

- **AD-006 — In-memory WAV via stdlib `wave` module.**
  `AudioClip.to_wav_bytes()` clips float32 to `[-1, 1]`, converts to int16, writes to `io.BytesIO`. Avoids `soundfile` dep and any disk I/O in the hot path.

- **AD-007 — Configuration precedence: defaults < project YAML < user YAML < env < CLI.**
  Secrets never resolve from YAML — only from environment variables named by `api_keys_env`. YAML absence of a config file is fine; defaults produce a working local-only setup.

- **AD-008 — `StructuredLogger` wrapper over stdlib `logging`.**
  Rationale: stdlib `Logger.warning(msg, foo=bar)` raises because kwargs go to `Logger._log`. Wrapper promotes non-passthrough kwargs to `extra=`, keeping call sites like `_log.info("event", key=value)` clean. `_JsonFormatter` emits one JSON object per record with the extras merged in. Code: `speako/util/logging.py`.

- **AD-009 — Drop-oldest bounded queues on hot paths.**
  `AudioCapturer` frame queue and `HotkeyListener` sink queue both use `put_nowait` and drop-oldest on overflow rather than blocking. Blocking either would freeze the audio driver / pynput callback thread — a violation of `PATTERNS.md` anti-pattern #5. Overflow is logged, never silent.

- **AD-010 — Python ≥ 3.12, use `uv` for env/deps.**
  Numpy 2.5+ requires 3.12 (uses `type` statements in stubs), so `requires-python = ">=3.12"` and `tool.mypy.python_version = "3.12"`. All install/run/test commands documented and used via `uv`. See README.

- **AD-011 — Dispatcher retries up to `max_attempts_per_provider` (default 3) per provider before falling through.**
  Each retry acquires a fresh key from `KeyManager` — no same-key retries (see `PATTERNS.md` anti-pattern #7). On `NoLiveKeysError`, the provider is skipped immediately. Full attempt history is included in the terminal `DispatchError`.

## Completed
- [x] Project scaffolding: `pyproject.toml` (with per-provider extras and dev extras), `.gitignore`, `.env.example`, `config.yaml.example`, `README.md`.
- [x] `speako/util/logging.py` — `StructuredLogger` + `_JsonFormatter` + idempotent `configure_logging`.
- [x] `speako/util/notify.py` — macOS `osascript` / Linux `notify-send`, logs on other platforms.
- [x] `speako/config/models.py` — frozen `AppConfig` and all sub-config dataclasses (`ProviderId` enum, `Providers/Audio/Hotkey/Output/Cooldown/Logging`).
- [x] `speako/config/loader.py` — precedence merge, YAML parse, env-only secret resolution, validation.
- [x] `tests/test_config_loader.py` — 7 tests covering defaults, YAML override, CLI override, env override, priority-requires-block, secrets-never-from-YAML, invalid mode, missing file.
- [x] `speako/audio/clip.py` — `AudioClip` dataclass, `to_wav_bytes` (stdlib `wave`), `as_mono_float32`.
- [x] `speako/audio/capturer.py` — `AudioCapturer` on `sounddevice.InputStream`, drop-oldest bounded queue, `begin_capture` / `end_capture` API.
- [x] `speako/audio/hotkey.py` — `HotkeyListener` on `pynput`, hold + toggle modes, PRESS/RELEASE events with monotonic timestamps.
- [x] `tests/test_audio_clip.py` — 4 tests: duration, WAV roundtrip, clipping, stereo→mono downmix.
- [x] `speako/transcribe/base.py` — `BaseTranscriber` Protocol, `KeyHandle` (with `fingerprint`), `Transcript`, full domain-error hierarchy.
- [x] `speako/transcribe/keys.py` — `KeyManager` (thread-safe, `RLock`), 4-state machine, round-robin, exponential cooldown, `snapshot()` for tests/debug.
- [x] `speako/transcribe/dispatcher.py` — `TranscriberDispatcher` with retry-and-rotate per provider, fall-through to next provider, terminal `DispatchError` with full attempt history.
- [x] `tests/test_key_manager.py` — 7 tests: round-robin, degrade→cooldown, auth→inactive, quota→max-cooldown, success recovery, empty pool, unregistered provider.
- [x] `speako/transcribe/groq_engine.py` — Groq (`whisper-large-v3-turbo` etc.) with SDK error mapping.
- [x] `speako/transcribe/gemini_engine.py` — Gemini Flash inline-WAV upload with `google.api_core` error mapping.
- [x] `speako/transcribe/local/factory.py` — `LocalEngineFactory.detect()` platform routing.
- [x] `speako/transcribe/local/mlx.py` — `MLXWhisperTranscriber` (Apple Silicon).
- [x] `speako/transcribe/local/faster.py` — `FasterWhisperTranscriber` (x86_64), lazily loads the model on first call.
- [x] `tests/test_local_factory.py` — 3 tests: unsupported hardware, Apple Silicon routing, x86_64 routing.
- [x] `speako/output/injector.py` — `OutputInjector` with paste (Cmd/Ctrl+V) and type strategies, optional clipboard restore.
- [x] `speako/app.py` — `AppContext` + `build_app_context()` wiring; degrades gracefully when optional provider deps are missing.
- [x] `speako/runtime.py` — main-thread loop consuming hotkey events, driving capture → transcribe → insert.
- [x] `speako/__main__.py` — argparse CLI (`--config`, `--log-level`, `--log-format`), SIGINT/SIGTERM handling.
- [x] Verified: `uv run pytest` → 22 passed; `uv run mypy speako` → strict-clean (26 files).

## Pending Tasks
1. **End-to-end smoke test** on real hardware — see Current Goal.
2. **Latency instrumentation** — add a summary log line per transcription with breakdown (capture ms, WAV encode ms, network ms, insert ms). Currently we only log total.
3. **Accessibility permissions flow (macOS)** — detect on first paste failure and surface a one-time notification with the settings URL, not just a log line.
4. **Ruff / mypy CI hook** — wire a pre-commit or GitHub Actions workflow so `uv run ruff check` + `uv run mypy` gate merges.
5. **VAD pre-filter** — deferred until smoke test measures baseline latency; only add if silence trimming meaningfully reduces provider cost.
6. **Provider-level tests with mocked SDK responses** — currently the error-mapping code paths in `groq_engine.py` and `gemini_engine.py` are only exercised at runtime.
7. **Publish / packaging** — decide on distribution: PyPI package, Homebrew tap, or standalone bundle. Deferred until the app is dogfood-stable.

## Notes / Open Questions
- Default hotkey is `right_option` (macOS-friendly, rarely bound). Revisit after first end-to-end run — some Linux desktops don't emit distinct left/right modifiers.
- Toggle-mode debounce currently only guards against OS auto-repeat within a single key-down; rapid tap-tap sequences could still race. Not a real-world issue for push-to-talk but worth measuring.
- `sounddevice` on Linux needs PortAudio system libs (`libportaudio2`); document in README once we hit that on real Linux hardware.
- macOS Accessibility permission must be granted to whichever binary launches speako (Terminal.app, iTerm, or a bundled launcher). Prompt appears on first paste attempt.
