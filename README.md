# TraceCoder

TraceCoder is a small coding-agent harness implemented without LangChain, LlamaIndex, an Agents SDK,
or hosted code-execution tools. Its core is an explicit model-tool loop around five local tools:

- `list_files`: bounded repository inspection
- `search_text`: regex-based code localization
- `read_file`: numbered, range-limited source reads
- `apply_patch`: validated unified-diff editing
- `run_command`: timeout-limited commands and tests

The surrounding harness adds workspace containment, secret and dangerous-command blocking, deterministic
completion checks, compacted history, repeated-action detection, token accounting, and JSONL trajectories.

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
  'Fix the failing parser test without weakening tests'
```

Git targets use `git apply` and `git diff`. Plain directories use GNU `patch`, an in-memory
pre-task snapshot, SHA-256 file tracking, and `difflib` to produce the same `final.diff`. TraceCoder
does not create a Git repository in a plain target.

### Interactive live CLI

Start a reusable prompt for one target project:

```bash
python3 -m tracecoder.cli --interactive --cwd /path/to/project
```

Enter a coding task at `tracecoder>`. The terminal immediately reports model waits, planned tool calls,
file reads and patches, command output line by line, test results, and completion verification. Use `/help`
for prompt commands and `/quit` to exit. A one-shot task also shows live progress by default; pass `--quiet`
for the previous final-result-only output.

Useful controls:

```text
--max-turns N             hard turn budget, default 30
--context-chars N         deterministic compaction threshold
--no-require-tests        allow completion without a successful test command
--allow-network-commands  let the agent invoke network CLIs (off by default)
```

Network access in the outer development environment does not imply network access for the model-controlled
shell. The latter stays disabled unless explicitly enabled.

## Completion policy

A `finish` request is rejected when there is no diff, whitespace validation fails, the latest test failed,
no test was run (default), the change is excessively large, or assertions appear to have been removed.
Git targets use `git diff --check`; plain directories validate their generated unified diff.
A model claim alone is never treated as proof of completion. After a passing test, the harness explicitly
asks the model to finish instead of rereading without a concrete risk. If the turn budget is exhausted before
that call, the same deterministic verifier runs automatically: a valid diff and latest passing test produce
`verified_complete_auto`; otherwise the run remains a `max_turns` failure.

## Run artifacts

Every invocation writes only under `<target>/.runs/<timestamp>/`:

- `events.jsonl`: model and tool events
- `summary.json`: outcome, files and test evidence
- `final.diff`: final patch, including untracked files
- `transcript.md`: compact human-readable trajectory

API keys are read by the model client and are not inserted into prompts or logs.

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The suite uses temporary Git repositories and ordinary directories under `/mnt/82_store/mj`, exercises
the real tools in both modes, and includes deterministic scripted-model repairs from a failing test to a
verifier-approved finish.

## Design rationale

The implementation follows the strongest practical lessons from current coding-agent work: keep the core
loop linear and inspectable; use executable feedback; localize before loading context; edit with narrow
patches; separate the model's completion request from deterministic verification; and preserve trajectories
for debugging and later learning. It intentionally avoids multi-agent orchestration, vector databases, and
training infrastructure until the single-agent baseline is reliable.
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
