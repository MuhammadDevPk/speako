# ARCHITECTURE.md — speako

## 1. System Overview

speako is a single-process, multi-threaded desktop application. One thread owns the OS hotkey, one owns the audio device, one or more workers own transcription I/O, and the main thread owns lifecycle and configuration.

## 2. Data Flow

```
        ┌────────────────────┐
        │  Hotkey Listener   │   pynput global listener
        │  (dedicated thread)│   emits: PRESS / RELEASE events
        └─────────┬──────────┘
                  │  threading.Event: recording_active
                  ▼
        ┌────────────────────┐
        │   Audio Capturer   │   sounddevice InputStream callback
        │  (device thread)   │   appends float32 frames → ring buffer
        └─────────┬──────────┘
                  │  on RELEASE: snapshot buffer → AudioClip
                  ▼
        ┌────────────────────┐
        │ Transcriber        │   dispatches AudioClip to provider
        │ Dispatcher         │   consults KeyManager for a live key
        └─────────┬──────────┘
                  │
       ┌──────────┼──────────┬───────────────┐
       ▼          ▼          ▼               ▼
    ┌──────┐  ┌──────┐  ┌────────────┐  ┌──────────────┐
    │ Groq │  │Gemini│  │mlx-whisper │  │faster-whisper│
    └──┬───┘  └──┬───┘  └─────┬──────┘  └──────┬───────┘
       │         │            │                 │
       └─────────┴──────┬─────┴─────────────────┘
                        ▼
              ┌──────────────────┐
              │ Transcript (str) │
              └────────┬─────────┘
                       ▼
              ┌──────────────────┐
              │  OS Insertion    │  clipboard → simulated paste
              │  (pyperclip +    │  or direct type via pynput/pyobjc
              │   platform hook) │
              └──────────────────┘
```

## 3. Component Contracts

| Component              | Owns                                    | Consumes                       | Produces                     |
|------------------------|-----------------------------------------|--------------------------------|------------------------------|
| `HotkeyListener`       | pynput listener, key-state debouncing   | user keypress                  | `RecordEvent` on a Queue     |
| `AudioCapturer`        | sounddevice stream, ring buffer         | `RecordEvent`                  | `AudioClip` (numpy float32)  |
| `TranscriberDispatcher`| provider selection, retry loop          | `AudioClip`, `KeyManager` view | `Transcript` or fatal error  |
| `KeyManager`           | per-provider key pool + state machine   | provider error signals         | live key handle              |
| `BaseTranscriber` impls| provider SDK calls                      | `AudioClip`, key handle        | `Transcript` or typed error  |
| `OutputInjector`       | clipboard + paste/type                  | `Transcript`                   | side effect on focused app   |
| `ConfigLoader`         | `.env` + `config.yaml` parsing          | filesystem                     | typed `AppConfig`            |

All inter-thread handoffs go through `queue.Queue` or `threading.Event`. No component holds a reference to another component's mutable internals.

## 4. Hardware Detection (Local Fallback)

Detection runs once at startup inside `LocalEngineFactory.detect()`:

```
platform.system() == "Darwin" AND platform.machine() == "arm64"
    → import mlx_whisper; return MLXWhisperTranscriber
platform.machine() in {"x86_64", "AMD64"}
    → import faster_whisper; return FasterWhisperTranscriber
otherwise
    → raise UnsupportedHardwareError (with actionable message)
```

Rationale:
- **Apple Silicon (arm64):** `mlx-whisper` uses Metal Performance Shaders and the ANE; consistently 3–5× faster than `faster-whisper` on M-series with lower power draw.
- **Intel / AMD (x86_64):** `faster-whisper` (CTranslate2) gives the best CPU throughput and supports int8 quantization out of the box; also works with CUDA if available.

The chosen local engine is cached on the `AppContext`; no runtime re-detection.

## 5. KeyManager State Machine

Each API key transitions through four states. Transitions are triggered by observed provider responses and by a monotonic-clock cooldown timer.

```
     ┌──────────┐   429 / 5xx (transient)   ┌──────────┐
     │  ACTIVE  │ ────────────────────────▶ │ DEGRADED │
     └────┬─────┘                            └────┬─────┘
          │                                       │
          │  repeated failure                     │  cooldown expires
          │  OR 402 / hard quota                  │  AND health check ok
          ▼                                       ▼
     ┌──────────┐    401 / 403 (auth fail)  ┌──────────┐
     │ COOLDOWN │ ────────────────────────▶ │ INACTIVE │
     └────┬─────┘                            └──────────┘
          │  cooldown timer elapsed                (terminal for session;
          │                                         requires config reload)
          ▼
     ┌──────────┐
     │  ACTIVE  │
     └──────────┘
```

- **ACTIVE:** eligible for immediate use, round-robin ordered within the provider pool.
- **DEGRADED:** one recent recoverable failure; still eligible but deprioritized. Two consecutive failures escalate to COOLDOWN.
- **COOLDOWN:** ineligible until `cooldown_until` (monotonic seconds). Default backoff: 30s → 120s → 600s (exponential with cap).
- **INACTIVE:** permanently removed for this session. Reached via auth failure (401/403) or explicit user invalidation.

Dispatcher selection rule (per provider):
1. Filter pool to `ACTIVE ∪ DEGRADED` where `cooldown_until <= now`.
2. Prefer `ACTIVE`; within tier, round-robin by `last_used_at`.
3. If pool is empty → raise `NoLiveKeysError`; the dispatcher then attempts the next configured provider in the priority chain, ending at the local engine.

## 6. Failure Semantics

- **Audio buffer is never dropped** while transcription is retried. The `AudioClip` lives in the worker until success or exhaustion of all providers.
- **Local engine has no keys**, so it is the terminal fallback. If it fails, the error surfaces to the user with the original cloud error chained for diagnosis.
- **User-visible errors** are delivered via a lightweight notifier (macOS `osascript`, Linux `notify-send`, Windows toast) — never a modal that steals focus mid-typing.

## 7. Configuration Precedence

`config.yaml` (project) < `~/.config/speako/config.yaml` (user) < environment variables (`.env` or shell) < CLI flags.

Secrets (API keys) resolved **only** from `.env` or the OS keychain — never `config.yaml`.

## 8. Package Layout (target)

```
speako/
├── __main__.py             # entrypoint
├── config/                 # ConfigLoader, AppConfig dataclasses
├── audio/                  # HotkeyListener, AudioCapturer, AudioClip
├── transcribe/
│   ├── base.py             # BaseTranscriber Protocol, error types
│   ├── dispatcher.py       # TranscriberDispatcher
│   ├── keys.py             # KeyManager, KeyState, cooldown policy
│   ├── groq_engine.py
│   ├── gemini_engine.py
│   └── local/
│       ├── factory.py      # hardware detection
│       ├── mlx.py
│       └── faster.py
├── output/                 # OutputInjector (per-platform)
└── util/                   # logging, notifications
```
