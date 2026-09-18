# PATTERNS.md — Design Contracts & Anti-Patterns

## Design Patterns (use these)

### 1. Strategy Pattern — Transcription Engines
- `BaseTranscriber` is a `typing.Protocol` (structural) exposing a single method:
  `def transcribe(self, clip: AudioClip, key: KeyHandle | None) -> Transcript`.
- Concrete strategies (`GroqTranscriber`, `GeminiTranscriber`, `MLXWhisperTranscriber`, `FasterWhisperTranscriber`) are constructed once at startup and held in an immutable registry keyed by `ProviderId`.
- The dispatcher never `isinstance`-checks the strategy. All provider-specific knowledge lives inside the strategy or its error-mapping helper.

### 2. Circuit Breaker + Round-Robin — API Key Pool
- Each `KeyHandle` carries its own breaker state (see `ARCHITECTURE.md` §5).
- Round-robin selection within the eligible tier prevents key starvation and hot-keying a single credential.
- The breaker opens on repeated failures and closes automatically after a monotonic-clock cooldown. Half-open probing is implicit: the first request after cooldown is the probe; success returns the key to `ACTIVE`, failure re-arms cooldown with exponential backoff.

### 3. Producer / Consumer — Audio Pipeline
- **Producer:** `AudioCapturer` writes fixed-size frame chunks to a `queue.Queue[AudioFrame]` from the sounddevice callback (which must never block).
- **Consumer:** on hotkey release, the capturer drains the queue into a contiguous `numpy.ndarray` and hands the resulting `AudioClip` to the transcriber worker via a second `queue.Queue[AudioClip]`.
- Backpressure: the frame queue is bounded; overflow drops the oldest frame and increments a metric, never blocks the audio callback.

### 4. Observer — Hotkey → Recorder
- `HotkeyListener` publishes `RecordEvent(kind=PRESS|RELEASE, at=monotonic_ns)` to a subscriber queue.
- Multiple subscribers (recorder, UI indicator, metrics) may attach without the listener knowing about them.

### 5. Factory — Local Engine Selection
- `LocalEngineFactory.detect()` returns a ready-to-use `BaseTranscriber` for the current hardware. The rest of the app is oblivious to whether MLX or CTranslate2 is behind the interface.

### 6. Typed Result / Error Mapping
- Provider SDK exceptions are caught **only inside the provider strategy** and mapped to a small closed set of domain errors: `RateLimitError`, `QuotaExhaustedError`, `AuthError`, `TransientProviderError`, `FatalProviderError`.
- The dispatcher and `KeyManager` react to these domain errors — they never see a raw `groq.APIError` or `google.api_core.exceptions.*`.

## Anti-Patterns (do not do these)

### 1. Hardcoded Secrets
- No API key ever appears in a `.py`, `.yaml`, or committed file. Keys resolve from `.env` (git-ignored) or the OS keychain. The `.env.example` file lists variable names only.

### 2. `time.sleep()` for Coordination
- Never poll. Use `threading.Event.wait(timeout=...)`, `queue.Queue.get(timeout=...)`, or `sounddevice`'s own callback-driven flow.
- Cooldown timers are compared against `time.monotonic()` on demand — not implemented via a background thread that sleeps.

### 3. Global Mutable State
- No module-level dicts, singletons, or `global` writes. Wire dependencies through an `AppContext` dataclass constructed in `__main__.py` and passed explicitly.
- Read-only registries (e.g., provider strategies) may be module-level `Final` bindings.

### 4. Bare / Overbroad `except`
- `except Exception:` without a re-raise or a structured log (provider, key fingerprint (last 4 chars), operation, elapsed ms) is forbidden.
- `except:` is forbidden outright.

### 5. Blocking the Hotkey Thread
- pynput's callback thread must return in <5 ms. It signals via `Event.set()` or `Queue.put_nowait()` and nothing else. Any network, disk, or transcription work runs elsewhere.

### 6. Disk I/O in the Hot Path
- Audio is captured, buffered, and sent to providers as in-memory `numpy` arrays or `io.BytesIO`. Temporary files, if unavoidable for an SDK, live on `tmpfs`/`/tmp` and are deleted in a `finally`.

### 7. Retrying Without Rotating
- Retrying the same failed key against the same provider is a bug. The dispatcher must either (a) pick a different key from the pool, or (b) fall through to the next provider. Same-key retries only apply to genuinely idempotent transient network errors and are capped at 1.

### 8. Silent Fallback
- Every fallback (key rotated, provider switched, dropped to local engine) is logged at INFO with the reason. The user optionally sees a subtle notifier ping. Failures must be observable.

### 9. Speculative Abstraction
- No `BaseProviderAdapterFactoryBuilder`. If there are two implementations, write two implementations. Introduce an abstraction on the third.

### 10. Catching to Convert to `None`
- Functions return `Transcript` or raise. They do not return `Optional[Transcript]` to signal failure. `None` means "no value semantically," never "something went wrong."
