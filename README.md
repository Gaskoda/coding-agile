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
- Git
- An OpenAI-compatible chat-completions endpoint with native tool calling

No Python runtime dependencies are required.

## Configure DeepSeek

Keep credentials in the process environment. Never commit them:

```bash
export DEEPSEEK_API_KEY='...'
export OPENAI_BASE_URL='https://api.deepseek.com/v1'
export TRACECODER_MODEL='deepseek-v4-pro'
```

## Run

The target must be a Git repository:

```bash
python3 -m tracecoder.cli \
  --cwd /path/to/repository \
  --model deepseek-v4-pro \
  'Fix the failing parser test without weakening tests'
```

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

A `finish` request is rejected when there is no diff, `git diff --check` fails, the latest test failed,
no test was run (default), the change is excessively large, or assertions appear to have been removed.
A model claim alone is never treated as proof of completion.

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

The suite uses temporary Git repositories under `/mnt/82_store/mj`, exercises the real tools, and includes
a deterministic scripted-model repair from a failing test to a verifier-approved finish.

## Design rationale

The implementation follows the strongest practical lessons from current coding-agent work: keep the core
loop linear and inspectable; use executable feedback; localize before loading context; edit with narrow
patches; separate the model's completion request from deterministic verification; and preserve trajectories
for debugging and later learning. It intentionally avoids multi-agent orchestration, vector databases, and
training infrastructure until the single-agent baseline is reliable.