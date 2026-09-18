# speako

**Push-to-talk speech-to-text with cloud provider rotation and a hardware-optimized local fallback.**

Hold a hotkey, speak, release — your transcript is pasted into whatever window has focus. Groq or Gemini for cloud speed, `mlx-whisper` (Apple Silicon) or `faster-whisper` (Intel/AMD) for zero-latency offline work. A pool of API keys per provider rotates automatically on rate limits, quota exhaustion, or auth failures.

<p>
  <img alt="Python 3.12+" src="https://img.shields.io/badge/python-3.12%2B-blue">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-green">
  <img alt="Type-checked: mypy --strict" src="https://img.shields.io/badge/typed-mypy%20strict-informational">
  <img alt="Linted: ruff" src="https://img.shields.io/badge/lint-ruff-orange">
  <img alt="Tested: pytest" src="https://img.shields.io/badge/tests-pytest-brightgreen">
</p>

---

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation](#installation)
- [Quick start](#quick-start)
- [Configuration](#configuration)
- [API keys and rotation](#api-keys-and-rotation)
- [Providers](#providers)
- [Running](#running)
- [Platform notes](#platform-notes)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Project layout](#project-layout)
- [Contributing](#contributing)
- [Roadmap](#roadmap)
- [License](#license)
- [Acknowledgements](#acknowledgements)

---

## Features

- **Push-to-talk workflow.** Hold a global hotkey → record → release → paste. Nothing to click.
- **Provider chain with automatic fallback.** Configure Groq → Gemini → local; the dispatcher retries with different keys inside a provider before moving to the next.
- **API key pool with a circuit-breaker per key.** Each key transitions through `ACTIVE → DEGRADED → COOLDOWN → INACTIVE` based on observed errors (429 / 401 / 402 / 5xx). Cooldowns are exponential with a configurable cap.
- **Hardware-aware local engine.** Auto-selects `mlx-whisper` on Apple Silicon (uses the ANE/Metal) and `faster-whisper` on x86_64.
- **Non-blocking audio pipeline.** `sounddevice` captures on the audio driver's thread, `pynput` owns the hotkey — neither ever blocks on I/O or transcription.
- **In-memory audio.** No temp files, no disk I/O in the hot path.
- **Structured JSON logging.** One event per line, easily piped into observability tools.
- **Strict typing throughout** (`mypy --strict`), minimal dependencies, dependency-injected `AppContext` — no globals.
- **MIT licensed.**

## How it works

```
Hotkey (pynput)  ─PRESS/RELEASE─▶  Event queue  ─▶  Main loop
                                                       │
Mic (sounddevice) ─frames─▶  Frame queue  ─drain─▶  AudioClip
                                                       │
                                                       ▼
                                          TranscriberDispatcher
                                          ├─▶ Groq   (rotates through key pool)
                                          ├─▶ Gemini (rotates through key pool)
                                          └─▶ Local  (mlx or faster-whisper)
                                                       │
                                                       ▼
                                          Clipboard + paste chord
                                          (or type-per-character)
```

Deep dives in [`ARCHITECTURE.md`](ARCHITECTURE.md), [`PATTERNS.md`](PATTERNS.md), and [`AGENT.md`](AGENT.md).

## Requirements

- **Python ≥ 3.12** (numpy 2.5+ requires it).
- **[uv](https://docs.astral.sh/uv/)** — used for env management, install, run, and dep updates. If you don't have it: `curl -LsSf https://astral.sh/uv/install.sh | sh`.
- **A working microphone** and OS permission for it.
- **Accessibility permission** (macOS) or equivalent to send synthetic keystrokes.
- **At least one provider ready to use.** Either a Groq / Gemini API key or the local backend for your CPU.

## Installation

Clone and install with the extras that match your setup:

```bash
git clone https://github.com/<your-fork>/speako.git
cd speako

# Apple Silicon → MLX local backend + Groq cloud
uv sync --extra groq --extra mlx --extra dev

# Intel / AMD → faster-whisper local backend + Groq + Gemini
uv sync --extra groq --extra gemini --extra faster --extra dev

# Cloud only, no local (any platform)
uv sync --extra groq --extra gemini --extra dev
```

Extras are additive; you can mix any subset. `--extra dev` pulls test/lint tooling and is optional for end users.

## Quick start

```bash
# 1. Copy the templates
cp .env.example .env
cp config.yaml.example config.yaml

# 2. Put your API keys in .env (comma-separated for pooling)
#    GROQ_API_KEYS=gsk_abc...,gsk_xyz...
#    GEMINI_API_KEYS=AIza...

# 3. Run
uv run speako
```

You'll see a `speako_ready` log line and a system notification. Hold **Right Option** (default), speak, release — the text is pasted into the focused window.

## Configuration

Precedence (lowest → highest):

1. Built-in defaults (safe local-only).
2. Project `./config.yaml`.
3. User `~/.config/speako/config.yaml`.
4. Environment variables (`.env` in cwd + shell).
5. CLI flags (`--config`, `--log-level`, `--log-format`).

**Secrets never load from YAML.** API keys are only read from environment variables named by each provider's `api_keys_env`.

Full reference (`config.yaml.example` is the source of truth):

```yaml
providers:
  # Dispatch order. Include "local" last as a safe terminal fallback.
  priority: [groq, gemini, local]

  groq:
    model: whisper-large-v3-turbo   # or whisper-large-v3
    api_keys_env: GROQ_API_KEYS

  gemini:
    model: gemini-2.0-flash         # or gemini-1.5-flash
    api_keys_env: GEMINI_API_KEYS

  local:
    # mlx-whisper:    e.g. "mlx-community/whisper-large-v3-turbo"
    # faster-whisper: e.g. "large-v3-turbo"
    model: large-v3-turbo
    compute_type: int8              # faster-whisper only; mlx ignores

audio:
  sample_rate: 16000                # 16 kHz mono is the Whisper standard
  channels: 1
  device: null                      # null → OS default input
  max_seconds: 120                  # hard cap per capture

hotkey:
  # pynput Key names ("alt_r", "f19", "cmd_r"), friendly aliases
  # ("right_option", "right_cmd", "option"), or single chars ("a").
  key: right_option
  mode: hold                        # "hold" or "toggle"

output:
  method: paste                     # "paste" (Cmd/Ctrl+V) or "type"
  restore_clipboard: true

cooldown:
  initial_seconds: 30
  factor: 4
  max_seconds: 600

logging:
  level: INFO                       # DEBUG | INFO | WARNING | ERROR | CRITICAL
  format: json                      # "json" or "text"
```

### CLI flags

```
speako [--config PATH] [--log-level {DEBUG,INFO,WARNING,ERROR}] [--log-format {json,text}]
```

CLI flags override YAML and env for the relevant fields.

## API keys and rotation

Put a **comma-separated pool** of keys per provider in `.env`:

```dotenv
GROQ_API_KEYS=gsk_key_1,gsk_key_2,gsk_key_3
GEMINI_API_KEYS=AIza_key_1,AIza_key_2
```

Each key travels through a four-state circuit breaker:

| State      | Meaning                                       | Entered by                                    | Left by                             |
|------------|-----------------------------------------------|-----------------------------------------------|-------------------------------------|
| `ACTIVE`   | Preferred; round-robin ordered.               | Fresh keys; recovered from any lower state.   | Any failure → `DEGRADED`/`COOLDOWN`.|
| `DEGRADED` | Deprioritized after one recoverable failure.  | First 429 / 5xx.                              | Success → `ACTIVE`; next failure → `COOLDOWN`. |
| `COOLDOWN` | Ineligible until timer expires.               | Second consecutive failure, or 402 (quota).   | Timer expires + successful probe.   |
| `INACTIVE` | Terminal for the session.                     | 401 / 403 (auth failure).                     | Only by restart with a new pool.    |

Cooldown timers use exponential backoff (`initial_seconds * factor^step`, capped at `max_seconds`) and are compared against `time.monotonic()` — no background thread.

When a provider's entire pool is unavailable, the dispatcher **falls through** to the next provider in `priority`. The `local` engine has no keys and is always available as a terminal fallback.

## Providers

| Provider | Recommended model         | Package extra           | API key env       |
|----------|---------------------------|-------------------------|-------------------|
| `groq`   | `whisper-large-v3-turbo`  | `uv sync --extra groq`  | `GROQ_API_KEYS`   |
| `gemini` | `gemini-2.0-flash`        | `uv sync --extra gemini`| `GEMINI_API_KEYS` |
| `local`  | `large-v3-turbo` / `mlx-community/whisper-large-v3-turbo` | `uv sync --extra mlx` (arm64 Darwin) or `--extra faster` (x86_64) | — |

Get keys:
- **Groq** — https://console.groq.com/keys
- **Gemini** — https://aistudio.google.com/apikey

## Running

```bash
# Foreground — logs to stderr as JSON
uv run speako

# Text logs, more verbose
uv run speako --log-format text --log-level DEBUG

# Point at a specific config file
uv run speako --config ~/dotfiles/speako.yaml
```

Stop with `Ctrl+C`.

## Platform notes

### macOS

- On first run, macOS prompts for **Microphone** and **Accessibility** permission. Grant both to whichever binary launches speako (Terminal.app, iTerm, VS Code integrated terminal, or a bundled launcher).
- Default hotkey `right_option` (Right ⌥) is unbound on stock macOS.
- The local backend uses `mlx-whisper`, which JIT-loads Metal kernels on first use — expect a small warmup on the first transcription of the session.

### Linux

- Install PortAudio system libs so `sounddevice` can find them:
  ```bash
  sudo apt install libportaudio2                       # Debian/Ubuntu
  sudo dnf install portaudio                           # Fedora
  ```
- `pynput` needs an X11 display or a Wayland-with-xwayland session; pure Wayland input synthesis is limited.
- Right-Alt on many Linux keyboards is AltGr (`Key.alt_gr`), not `alt_r`. Set `hotkey.key` accordingly.

### Windows

- Untested by the maintainer. In principle it works via `pynput` + `sounddevice`; `mlx-whisper` is macOS-only, so use `--extra faster` for the local backend. PRs to fix Windows issues are welcome.

## Troubleshooting

<details>
<summary><b>"no usable providers"</b> on startup</summary>

You configured a provider in `priority` but neither installed its extra nor provided keys. Either `uv sync --extra <name>`, add keys to `.env`, or remove the provider from `priority`.
</details>

<details>
<summary>Paste "works" (log shows success) but nothing appears in the target app</summary>

macOS Accessibility permission. Open *System Settings → Privacy & Security → Accessibility* and enable the app that runs `speako`. Restart your terminal after granting.
</details>

<details>
<summary>Nothing happens when I press the hotkey</summary>

- Check the `hotkey_started` log line to see which key was actually bound.
- On macOS, `pynput` needs Accessibility permission to *observe* keys too.
- Try `--log-level DEBUG` to see `capture_begin` / `capture_end` events.
</details>

<details>
<summary>Transcripts have long latency</summary>

- Groq is usually the fastest cloud option; put it first in `priority`.
- Local models load lazily. `mlx-whisper` warms up on first call; `faster-whisper` builds the model on first call — subsequent calls are much faster.
- Check `audio.max_seconds` isn't clipping long clips prematurely.
</details>

<details>
<summary>Rate limits (429) even with fresh keys</summary>

Increase pool size — add more keys. Each key that recently 429'd goes to `DEGRADED`, then `COOLDOWN`. With multiple keys the dispatcher round-robins across the healthy ones.
</details>

<details>
<summary>`audio_frames_dropped` warnings in logs</summary>

The frame queue is bounded and drops the oldest chunk on overflow to protect the audio driver's realtime thread. If you see many drops during normal use, raise `audio.max_seconds` (queue size scales with it) or investigate a stuck consumer.
</details>

## Development

```bash
git clone <repo>
cd speako
uv sync --extra dev --extra groq --extra gemini --extra mlx     # or --extra faster
```

Common tasks:

```bash
uv run pytest              # run tests (28 tests, ~1s)
uv run pytest -k key       # filter by keyword
uv run mypy speako tests   # strict type check
uv run ruff check .        # lint
uv run ruff format .       # format
```

Everything must pass before opening a PR:

- `uv run ruff check speako tests` → **All checks passed!**
- `uv run mypy speako tests` → **Success: no issues found**
- `uv run pytest` → **all green**

## Project layout

```
speako/
├── __main__.py            # CLI entrypoint, argparse, signal handling
├── app.py                 # AppContext + build_app_context (dependency wiring)
├── runtime.py             # main event loop
├── config/
│   ├── models.py          # frozen AppConfig + sub-configs
│   └── loader.py          # YAML + env + CLI precedence, validation
├── audio/
│   ├── clip.py            # AudioClip + WAV serialization (stdlib wave)
│   ├── capturer.py        # sounddevice InputStream, drop-oldest queue
│   └── hotkey.py          # pynput listener, hold/toggle, key aliases
├── transcribe/
│   ├── base.py            # BaseTranscriber Protocol, KeyHandle, error taxonomy
│   ├── keys.py            # KeyManager: 4-state machine, round-robin, cooldowns
│   ├── dispatcher.py      # provider chain with retry-and-rotate
│   ├── groq_engine.py
│   ├── gemini_engine.py   # google-genai (not deprecated google-generativeai)
│   └── local/
│       ├── factory.py     # platform.machine() → backend
│       ├── mlx.py         # Apple Silicon
│       └── faster.py      # Intel/AMD
├── output/
│   └── injector.py        # clipboard + paste (Cmd/Ctrl+V) or type
└── util/
    ├── logging.py         # StructuredLogger + JSON formatter
    └── notify.py          # osascript / notify-send

tests/                     # pytest (28 tests, no network, no audio device required)
```

## Contributing

Contributions are welcome. Speako is MIT-licensed and open to enhancements, fixes, and platform-specific improvements.

**Workflow:**

1. Open an issue for anything larger than a small fix — happy to discuss shape before you write code.
2. Fork and create a topic branch: `git checkout -b feature/vad-prefilter`.
3. Write code with **strict types** and **tests**. `mypy --strict` and `ruff` must stay clean.
4. Add tests. Every new module should ship with unit tests that don't require an audio device or a live API key. Mock provider SDKs at their exception boundary.
5. Update the docs — `README.md`, `ARCHITECTURE.md`, `PATTERNS.md`, `MEMORY.md` — if you change semantics.
6. Open a PR with a clear description and any manual smoke test steps you ran.

**Areas that would especially benefit from help:**

- Windows testing and tweaks (see [Platform notes](#platform-notes)).
- Additional cloud providers behind the `BaseTranscriber` Protocol (Deepgram, AssemblyAI, OpenAI Whisper API, etc.).
- A minimal system-tray UI (macOS status bar, Linux tray, Windows notification area).
- Optional VAD pre-filter to strip silence before sending to cloud providers.
- Latency instrumentation: per-stage breakdown (capture, encode, network, insert).
- Config file schema published as JSON Schema for editor autocompletion.

**Code style:**

- `ruff` and `mypy --strict` are the enforcers — configuration in `pyproject.toml`.
- Follow the operating contract in [`AGENT.md`](AGENT.md) and the design/anti-patterns in [`PATTERNS.md`](PATTERNS.md).
- Prefer editing existing modules over adding new ones; introduce abstractions on the third repeat, not the first.

## Roadmap

See [`MEMORY.md`](MEMORY.md) for the current pending list. Highlights:

- End-to-end latency instrumentation.
- Accessibility-permission first-run flow on macOS.
- Provider-level tests with mocked SDK responses.
- Optional VAD pre-filter.
- CI pipeline (GitHub Actions) running `ruff`, `mypy`, `pytest` on push.
- Distribution: PyPI package + optional launcher bundle.

## License

MIT — see [`LICENSE`](LICENSE). Copyright © 2026 Malik Muhammad Awan.

## Acknowledgements

Speako stands on:

- [Groq](https://groq.com) & [Google Gemini](https://ai.google.dev) — cloud transcription.
- [`mlx-whisper`](https://github.com/ml-explore/mlx-examples/tree/main/whisper) — Apple Silicon inference.
- [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) — CTranslate2-based Whisper.
- [`sounddevice`](https://python-sounddevice.readthedocs.io), [`pynput`](https://pynput.readthedocs.io), [`pyperclip`](https://github.com/asweigart/pyperclip) — cross-platform I/O.
- [`uv`](https://docs.astral.sh/uv/) — packaging and env management.
