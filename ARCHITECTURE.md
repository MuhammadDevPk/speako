# ARCHITECTURE.md — speako

## 1. System Overview

speako is a single-process, multi-threaded desktop application. When the HUD is enabled, **the main thread owns the Tk mainloop** (mandatory on macOS) and every other component runs on a worker thread it spawns. When the HUD is disabled (`--no-hud` / `ui.enabled: false`), the runtime loop moves back to the main thread and no GUI is created. Threads:

* **Main thread** — Tk mainloop (HUD mode) *or* runtime loop (headless mode).
* **`speako-runtime` worker** — hotkey-event consumer, capture orchestration, dispatch.
* **pynput listener thread** — global keyboard observer, owned by pynput.
* **sounddevice callback thread** — audio driver realtime thread, owned by PortAudio.
* **`speako-level-pump` daemon** — 1-line adapter: `float` levels → `HudLevelEvent`.

## 2. Data Flow

```
   ┌────────────────────┐
   │  Hotkey Listener   │   pynput global listener
   │  (dedicated thread)│   emits: PRESS / RELEASE events
   └─────────┬──────────┘
             │  hotkey_events queue
             ▼
   ┌────────────────────┐             ┌───────────────────────┐
   │  Runtime loop      │◀────────────│   Audio Capturer      │
   │  (worker thread)   │  drain on   │   (device thread)     │
   │                    │  RELEASE    │   float32 → frame_q   │
   │  emits state on    │             │                       │
   │  hud_events queue  │             │   also RMS meter →    │
   └─────────┬──────────┘             │   level_q (drop-old)  │
             │                        └───────────┬───────────┘
             │  AudioClip                         │  float 0..1
             ▼                                    ▼
   ┌────────────────────┐             ┌───────────────────────┐
   │ Transcriber        │             │ Level Pump (daemon)   │
   │ Dispatcher         │             │ float → HudLevelEvent │
   │ + KeyManager       │             └───────────┬───────────┘
   └─────────┬──────────┘                         │
             │                                    │
   ┌──── providers ────┐                          │
   ▼          ▼          ▼                        │
 Groq     Gemini      Local                       │
   │          │          │                        │
   └────┬─────┴──────┬───┘                        │
        ▼            ▼                            │
   ┌────────────┐  ┌──────────────┐               │
   │ Transcript │  │ OS Insertion │               │
   └─────┬──────┘  │  (paste/type)│               │
         │         └──────────────┘               │
         │                                        │
         └──▶ HudStateEvent(PASTED) ──────┐       │
                                          ▼       ▼
                              ┌────────────────────────────┐
                              │  HUD (main thread, Tk)     │
                              │  drains hud_events @ 30 Hz │
                              │  states: HIDDEN /          │
                              │  LISTENING (dot + bars) /  │
                              │  TRANSCRIBING (spinner) /  │
                              │  PASTED (check → fade)     │
                              └────────────────────────────┘
```

The **UI event bus** (`hud_events: Queue[HudEvent]`) carries three message types:
`HudStateEvent`, `HudLevelEvent`, `HudShutdown`. All producers use `put_nowait`; overflow drops silently for level events and is logged for state events. The HUD polls the bus with `Tk.after(33 ms, ...)`, draining up to 32 events per frame.

## 3. Component Contracts

| Component              | Owns                                    | Consumes                       | Produces                     |
|------------------------|-----------------------------------------|--------------------------------|------------------------------|
| `HotkeyListener`       | pynput listener, key-state debouncing   | user keypress                  | `RecordEvent` on a Queue     |
| `AudioCapturer`        | sounddevice stream, ring buffer, `RmsLevelMeter` | mic frames             | `AudioClip` + level pushes on `level_sink` |
| `RmsLevelMeter`        | rolling noise-floor + attack/release EMA | float32 chunks                | normalized level in `[0.0, 1.0]` |
| `TranscriberDispatcher`| provider selection, retry loop          | `AudioClip`, `KeyManager` view | `Transcript` or fatal error  |
| `KeyManager`           | per-provider key pool + state machine   | provider error signals         | live key handle              |
| `BaseTranscriber` impls| provider SDK calls                      | `AudioClip`, key handle        | `Transcript` or typed error  |
| `OutputInjector`       | clipboard + paste/type                  | `Transcript`                   | side effect on focused app   |
| `Hud`                  | Tk root, canvas, per-frame render       | `HudEvent` from bus            | pixels on screen             |
| Level pump (daemon)    | float → `HudLevelEvent` translation     | `level_sink` floats            | `HudLevelEvent`s on bus      |
| `Runtime.run`          | state machine (idle / recording / dispatching) | `RecordEvent`s          | `HudStateEvent`s + insertion side effects |
| `ConfigLoader`         | `.env` + `config.yaml` parsing          | filesystem                     | typed `AppConfig`            |

All inter-thread handoffs go through `queue.Queue` or `threading.Event`. No component holds a reference to another component's mutable internals.

### 3.1 UI event bus

```python
class HudState(Enum):
    HIDDEN | LISTENING | TRANSCRIBING | PASTED

HudEvent = HudStateEvent(state) | HudLevelEvent(level) | HudShutdown()
```

Producers → bus:
- `AudioCapturer._on_audio` (via `level_sink` → level-pump daemon) → `HudLevelEvent`
- `runtime.run` → `HudStateEvent(LISTENING)` on PRESS, `TRANSCRIBING` after RELEASE, `PASTED` after successful insert, `HIDDEN` on empty transcript or failure
- `runtime.run` finally block → `HudShutdown` (so Tk mainloop returns)
- `__main__` shutdown path → `HudShutdown` (belt-and-braces)

Consumer:
- `Hud._tick` (Tk `after(33 ms)`) drains up to 32 events, updates state, renders one frame.

### 3.1a HUD visibility model and macOS Spaces

The HUD is created once and stays realized. Show / hide is a pure alpha operation (`wm_attributes "-alpha"`) — no `withdraw` or `deiconify`. Rationale: on macOS `deiconify` activates the window, which steals focus from whatever the user is typing into (breaking paste) and forces a Space switch back to wherever the HUD lives. Alpha is a compositor operation with none of those side effects.

For the HUD to appear on **every macOS Space** (including fullscreen apps) and to **never receive events**, we reach past Tk into the underlying NSWindow via `pyobjc` (`_apply_platform_hud_behavior` in `speako/ui/hud.py`):

| NSWindow property               | Value                                                                                                                                                                                                                                    |
|---------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `collectionBehavior`            | `CanJoinAllSpaces \| Stationary \| IgnoresCycle \| FullScreenAuxiliary` — window is visible on every Space, doesn't animate with Space transitions, is skipped by Cmd-` cycle, and appears over fullscreen apps. |
| `level`                         | `NSStatusWindowLevel` (25) — above normal windows, below system menu/dock.                                                                                                                                                                |
| `ignoresMouseEvents`            | `YES` — clicks pass through to whatever is below, so the HUD never intercepts input.                                                                                                                                                     |

The NSWindow is located via `NSApp.windows()` matched on the unique title `__speako_hud__`. On non-macOS platforms this helper is a no-op — Linux/Windows already get the right behavior from `overrideredirect` + `-topmost` (and `-type dock` on X11).

### 3.2 RMS level meter

Fed one audio chunk per sounddevice callback:

1. `rms = sqrt(mean(chunk²))` (float32).
2. `db = 20·log10(rms + ε)`.
3. Rolling noise floor: EMA (`α = 0.03`) on sub-floor observations only — floor drifts *down* toward true silence but never up.
4. `raw = clamp((db − floor_db) / 35 dB, 0, 1)`.
5. Asymmetric smoothing on the output: attack `α = 0.55`, release `α = 0.18`, so the meter jumps to loud speech and decays gently.

Result is pushed to a bounded (`maxsize=8`) `level_sink` with drop-oldest overflow — the meter converges quickly so skipped chunks are invisible.

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
