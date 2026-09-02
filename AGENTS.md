# TraceCoder Repository Guide

## Project overview

TraceCoder is a lightweight and auditable coding-agent harness for Python projects. It runs an explicit model-tool loop against either a Git repository or an ordinary directory.

The runtime supports Python 3.10+ and uses only the Python standard library. Keep the implementation small and explicit. Do not introduce an agent framework when the existing model-tool loop can support the requested feature.

## Repository map

| Path | Responsibility |
|---|---|
| `tracecoder/agent.py` | Model-tool loop, run state, context handling, completion verification and event emission |
| `tracecoder/model.py` | OpenAI-compatible model client and response normalization |
| `tracecoder/tools.py` | Model-callable file, search, editing and command tools |
| `tracecoder/safety.py` | Workspace confinement, secret protection and command policy |
| `tracecoder/workspace.py` | Git-independent snapshots and unified diffs |
| `tracecoder/instructions.py` | Hierarchical `AGENTS.md` discovery and loading |
| `tracecoder/context.py` | Deterministic context compaction and evidence retention |
| `tracecoder/task_state.py` | Durable task facts that survive context compaction |
| `tracecoder/cli.py` | One-shot and interactive command-line interfaces |
| `tracecoder/config.py` | Local JSON configuration parsing |
| `tests/` | Local unit and integration tests |

Put behavior in the module that owns it. Do not duplicate safety checks in `agent.py` when they belong in `safety.py`, or workspace logic in `tools.py` when it belongs in `workspace.py`.

## Architecture boundaries

Preserve these properties in every change:

- Target projects may be Git repositories or ordinary directories.
- Git must remain optional for target projects.
- Model-controlled paths must stay inside the selected workspace.
- All model-controlled file paths pass through `SafetyPolicy`.
- Symbolic links must not provide access outside the workspace.
- Code delivery is primary; command execution and runtime probing occur only when the user's task explicitly
  requests them.
- Tool input and output must be bounded before entering model context.
- API credentials must not enter prompts, events or run artifacts.
- Completion must depend on observable evidence, not only a model claim.
- Runtime functionality must not depend on test or development packages.

- TraceCoder runtime state must stay outside the user's target project.
- Do not create tests merely to satisfy the harness or require tests for every completion.
- Install dependencies, create environments, execute or test only when explicitly requested by the user.
- Record Python dependencies in `requirements.txt` or the project's existing dependency manifest.
- Document environment creation, dependency installation and startup commands in the project's README.
Do not weaken these properties to make a task or test pass.

## Model-tool loop

The normal loop is:

1. Inspect the relevant project files.
2. Form a concrete implementation hypothesis.
3. Make the smallest coherent change.
4. Ensure dependencies are declared and usage is documented.
5. Perform installation, execution, testing or validation only if the task explicitly requests it.
6. Request completion when the requested deliverables are ready.

When changing the loop, preserve structured tools, bounded retries, event logging and file-level delivery checks.

## Tool implementation

A model-callable tool belongs in `tracecoder/tools.py`. Every tool must:

- have one clear responsibility;
- define a bounded JSON schema;
- validate all arguments;
- enforce workspace containment through `SafetyPolicy`;
- bound file size, output size and execution time where applicable;
- return a structured `ToolResult`;
- expose useful verification metadata;
- avoid leaking unrestricted process environment values.

Do not add arbitrary Python evaluation or an unrestricted shell tool.

## AGENTS.md loading

Project instructions are hierarchical:

- Load the workspace-root `AGENTS.md` at task startup.
- Apply nested `AGENTS.md` files only below their containing directories.
- Order applicable files from the workspace root toward the target file.
- More specific nested instructions take precedence on conflict.
- Expose newly discovered nested instructions before the affected edit.
- Refresh cached instructions after an `AGENTS.md` file changes.
- Reject instruction files that escape the workspace through symbolic links.
- Keep instruction input bounded.

Do not recursively load every instruction file at startup.

## Verification

After changing TraceCoder Python code, run:

```bash
python3 -m unittest discover -s tests -q
python3 -m compileall -q tracecoder
git diff --check
```

During development, run a focused test first when possible. Run the full suite before reporting completion.

A completion result should be rejected when no relevant change exists, diff validation fails, explicitly required tests were not run,
the latest required test failed, or assertions were removed to obtain a passing result.

Passing test evidence is associated with the workspace digest that produced it. Do not accept stale evidence after source files change.

## Code style

- Match the existing Python style.
- Prefer explicit control flow and small data structures.
- Use type hints for public interfaces and persistent state.
- Avoid import-time side effects and hidden global state.
- Keep CLI output understandable to users who do not know the internals.
- Comments should explain invariants and non-obvious decisions.

## Scope control

Do not add these components unless the task explicitly requires them:

- LangChain, LlamaIndex or another agent framework
- a web frontend
- multi-agent orchestration
- an MCP runtime
- a database
- Docker or Kubernetes
