# TraceCoder

## AGENTS.md project instructions

TraceCoder v0.2 automatically loads project instructions from `AGENTS.md`. Rules are hierarchical: a root file applies to the whole workspace, while a file in a nested directory applies only below that directory and overrides conflicting parent rules. Root instructions are supplied at startup; newly discovered nested instructions are shown before the first affected edit and the edit must then be retried.

Example:

```text
project/AGENTS.md
project/backend/AGENTS.md
project/backend/app.py
```

When working on `backend/app.py`, both instruction files apply, with `backend/AGENTS.md` taking precedence. Each file is limited to 32,000 characters and the applicable chain to 64,000 characters.

TraceCoder is a small coding-agent harness implemented without LangChain, LlamaIndex, an Agents SDK,
or hosted code-execution tools. Its core is an explicit model-tool loop around five local tools:

- `list_files`: bounded repository inspection
- `search_text`: regex-based code localization
- `read_file`: numbered, range-limited source reads
- `apply_patch`: validated unified-diff editing
- `run_command`: bounded command execution when the user explicitly requests runtime work

The surrounding harness adds workspace containment, secret protection, deterministic
completion checks, compacted history, repeated-action detection, token accounting, and JSONL trajectories.
Context compaction preserves the original objective, discovered `AGENTS.md` rules, modified files and unresolved failures. It is triggered by both the configured character budget and the previous API request's prompt-token usage; full raw events remain in `events.jsonl`.


## Requirements

- Python 3.10+
- GNU patch (for non-Git project directories)
- Git is optional for target directories, but used when available
- An OpenAI-compatible chat-completions endpoint with native tool calling

No Python runtime dependencies are required.

## Configure DeepSeek

Copy the tracked blank example to the ignored local config, then edit it:

```bash
cp tracecoder.example.json tracecoder.local.json
vim tracecoder.local.json
```

```json
{
  "api_key": "your-key",
  "base_url": "https://api.deepseek.com/v1",
  "model": "deepseek-v4-pro",
  "max_turns": 30,
  "context_chars": 80000
}
```

`tracecoder.local.json` is ignored by Git. Never put a real key in the tracked example: replacing
a committed secret with an empty value does not remove it from Git history. Command-line options override
the local file; environment variables remain a fallback. Use `--config /path/file.json` for another file.

## Run

The target can be either a Git repository or an ordinary project directory:

```bash
python3 -m tracecoder.cli \
  --cwd /path/to/project \
  'Create a small command-line todo application with usage instructions'
```

Git targets use `git apply` and `git diff`. Plain directories use GNU `patch`, an in-memory
pre-task snapshot, SHA-256 file tracking, and `difflib` to produce the same `final.diff`. TraceCoder
does not create a Git repository in a plain target.

### Interactive live CLI

Start a reusable prompt for one target project:

```bash
python3 -m tracecoder.cli --interactive --cwd /path/to/project
```

Enter a coding task at `tracecoder>`. The terminal immediately reports model waits, planned file operations,
file reads, patches and the final delivery check. Use `/help`
for prompt commands and `/quit` to exit. A one-shot task also shows live progress by default; pass `--quiet`
for the previous final-result-only output.

Useful controls:

```text
--max-turns N             hard turn budget, default 30
--context-chars N         deterministic compaction threshold
--state-dir PATH          override TraceCoder's own state directory
--allow-network-commands  explicit override for ambiguous network-related task wording
```

## Project delivery policy

TraceCoder's primary job is to write the requested project code. It does not create tests merely to satisfy
the harness, and it does not install packages, create environments, execute the project, probe the runtime or
run tests by default.

For a generated Python project, the agent records third-party packages in `requirements.txt` (or preserves
the dependency manifest already used by the project). The project README contains the commands that a user
can run later to create an environment, install those declared packages and start the application. Other
ecosystems follow the same rule using their native manifest, such as `package.json` or `Cargo.toml`.

When the user's task explicitly asks TraceCoder to install dependencies, build an environment, run or test
the project, validate behavior, download resources or perform related command work, `run_command` is available
and its output is streamed to the terminal. These optional actions do not become a universal completion gate.

## Delivery check

A `finish` request is rejected when there is no diff, patch-format validation fails, or the change is excessively
large. This is a file-level integrity check, not project
execution or environment validation.
Git targets use `git diff --check`; plain directories validate their generated unified diff.
If the model reaches the turn limit without calling `finish`, the same file-level check runs automatically.

## Run artifacts

Every invocation writes under TraceCoder's own `<tracecoder>/.tracecoder/runs/<timestamp>/`, not inside
the user's target project.
Use `--state-dir PATH` to choose another external state location. The target directory therefore receives
only the code and project files needed for the task.

The live CLI prints each model turn, tool call, requested command output, delivery-check result and a final
count of tool steps and modified files. These events are visible during execution, not only after it ends.

- `events.jsonl`: model and tool events
- `summary.json`: outcome and affected files
- `final.diff`: final patch, including untracked files
- `transcript.md`: compact human-readable trajectory

API keys are read by the model client and are not inserted into prompts or logs.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

These are private development tests for TraceCoder itself. They are not exposed to the model and do not cause
TraceCoder to test user projects.

## Design rationale

The implementation follows the strongest practical lessons from current coding-agent work: keep the core
loop linear and inspectable; localize before loading context; edit with narrow patches; deliver reproducible
dependency manifests and documentation; and preserve trajectories for debugging and later learning. It
intentionally avoids target-project execution, multi-agent orchestration, vector databases, and training
infrastructure until the code-writing baseline is reliable.
## Research references

The implementation is original, but its design choices were informed by:

- mini-SWE-agent: minimal linear loop and shell-oriented baseline
  https://github.com/SWE-agent/mini-swe-agent
- SWE-agent: explicit agent-computer interfaces and trajectory inspection
  https://github.com/SWE-agent/SWE-agent
- Dive into Claude Code (2026): context compaction, permissions and harness design
  https://arxiv.org/abs/2604.14228
- What Context Does a Coding Agent Actually Need to Act? (2026): focused source context
  https://arxiv.org/abs/2607.09691
- SWE-smith: executable software-engineering tasks and trajectory-driven evaluation
  https://github.com/SWE-bench/SWE-smith
