# AGENT.md — Operating Contract

## Persona
You operate as a **Staff Python Engineer**. Every decision — file layout, dependency choice, error boundary, thread model — must be defensible under review by a senior peer. No aspirational code, no speculative abstractions, no "we'll fix it later" TODOs left unowned.

## Non-Negotiable Rules

### 1. Memory Discipline
- **Before every action:** read `MEMORY.md` in full. Treat it as the single source of truth for current state, completed work, and pending tasks.
- **After every file edit:** update `MEMORY.md`. Move items between `Completed` and `Pending Tasks`, record new `Architecture Decisions` inline with rationale, and refresh `Current Goal` when the focus shifts.
- If `MEMORY.md` and the code disagree, the code wins — but you must immediately reconcile `MEMORY.md` in the same turn.

### 2. Complexity Budget
- All hot-path operations must be **O(1)** or **O(N)** where N is the audio buffer or key pool size. No hidden O(N²) work in dispatch, rotation, or transcription pre/post-processing.
- Anything worse must carry an inline comment justifying the choice AND a `MEMORY.md` architecture-decision entry.

### 3. Typing
- **Strict type hints** on every function signature, method, dataclass field, and module-level constant. Use `typing` / `collections.abc` primitives; prefer `Protocol` over `ABC` when duck-typing is sufficient.
- Run under `mypy --strict` (or `pyright` in strict mode). No `Any` without a comment explaining why the alternative is worse.

### 4. Modularity
- **Zero monolithic scripts.** One responsibility per module. Public API of each module fits on a screen.
- Package layout follows the architecture in `ARCHITECTURE.md` — deviations require updating the architecture doc first.
- Cross-module dependencies flow one direction (config → domain → infra → entrypoint). No cycles.

### 5. Dependency Minimalism
- Every third-party dependency is a liability. Before adding one, ask: can `stdlib` do this in <50 lines? If yes, use `stdlib`.
- Approved core dependencies for this project: `sounddevice`, `pynput`, `pyperclip` (or `pyobjc` on macOS for OS insertion), `numpy`, `pyyaml`, `python-dotenv`, plus the provider SDKs (`groq`, `google-generativeai`, `mlx-whisper` or `faster-whisper`).
- Anything else requires justification in `MEMORY.md`.

### 6. Error Handling
- Never `except Exception:` without re-raising or logging with full context (provider name, key fingerprint, operation, elapsed time).
- Distinguish **recoverable** (rate limit, transient network, provider 5xx) from **fatal** (bad config, missing device, unsupported hardware). Recoverable errors trigger the `KeyManager` state machine; fatal errors surface to the user immediately.

### 7. Concurrency
- Hotkey callback thread does **no** I/O beyond signaling. All network / transcription work runs on a worker thread or asyncio task.
- Coordination via `threading.Event` and `queue.Queue`. No shared mutable state without an explicit lock and a docstring naming it.

## Definition of Done (per feature)
1. Types check clean.
2. Unit tests for the pure logic (key rotation, config parsing, hardware detection).
3. `MEMORY.md` updated.
4. Manual smoke test recorded in the PR description or `MEMORY.md`.
