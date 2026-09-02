# TraceCoder development review through v0.2

This document records the meaningful engineering questions encountered from the initial task review through the current v0.2 implementation. It intentionally excludes credentials, private test data and generated run contents.

## 1. Initial objective

The project began as an implementation response to a coding-agent assessment. The practical goal became a small agent that a user could point at a project, describe a task to, watch in real time and later audit.

The chosen baseline was an explicit single-agent model-tool loop using a DeepSeek OpenAI-compatible endpoint. The implementation avoided LangChain and similar frameworks so that state transitions, safety decisions and completion checks remained inspectable.

## 2. Research that shaped the design

The design was compared with Codex, Claude Code, OpenHands, harness engineering and loop engineering work.

The useful common ideas were:

- keep project instructions close to the code and load them by scope;
- separate agent control flow, tools, workspace access and model transport;
- treat tool results as evidence rather than trusting model prose;
- preserve a readable trajectory and final diff;
- bound context, tool output, retries, time and permissions;
- use explicit verification and stopping rules;
- add infrastructure only when the current task needs it.

This led to a deliberately small Python implementation rather than a copy of a larger agent platform.

## 3. Meaningful problems and changes

### Configuration and secret handling

Problem: entering the API key through shell exports was inconvenient and easy to repeat incorrectly.

Change: add an ignored `tracecoder.local.json`, a blank tracked example and strict config-key validation. Credentials are passed only to the model transport and are not inserted into prompts or run artifacts.

### Targets were initially assumed to be Git repositories

Problem: users also need to create or repair projects in ordinary directories.

Change: add bounded snapshots and unified diffs for non-Git directories while retaining Git-native diff behavior when `.git` is present.

### The original CLI hid too much progress

Problem: a user could not tell whether the model was reasoning, reading, editing, testing or stuck.

Change: add an interactive CLI and observer events for model waits, tool calls, streamed command output, verification and final status.

### Passing work could stop at the turn limit

Problem: a real run passed ten tests but used its final turn rereading files and never called `finish`.

Change: after a passing test, explicitly ask the model to finish. At the hard turn limit, run the same deterministic verifier automatically rather than failing solely because the model omitted the final tool call.

### Project instructions were absent

Problem: the model had no durable repository-specific architecture, workflow or verification guidance.

Change: implement hierarchical `AGENTS.md` loading. Root instructions load at startup; nested instructions load before the first affected operation, deeper rules take precedence, caches refresh after instruction edits, and symlink escapes are rejected.

### The first repository AGENTS.md was not useful enough

Problem: it mostly listed dependency and publication restrictions. It did not explain module ownership, architectural invariants or concrete verification.

Change: rewrite it as a repository guide based on patterns from Codex, Claude Code and OpenHands: repository map, architecture boundaries, tool contract, loading semantics, exact checks and scope control.

## 4. Current highest-risk problem

Before this change, a successful test record was not associated with the workspace state it tested. The following sequence could be incorrectly accepted:

1. edit source;
2. run tests successfully;
3. edit source again and introduce a regression;
4. call `finish`;
5. accept the earlier passing result.

Test detection also used substring matching, so a command such as `echo pytest` could be misclassified as a successful test.

## 5. Resolution

A successful test event now records a SHA-256 digest of the observable workspace after the command finishes. Completion recomputes the digest and rejects evidence when the workspace has changed.

The digest covers relative file names and file-content digests for both Git and ordinary directories. Runtime artifacts, caches, virtual environments, dependency directories and known secret files use the existing snapshot exclusions.

`run_command` now accepts a `purpose` value: `auto`, `test`, `build` or `inspect`. Explicit `test` purpose is preferred. Automatic fallback recognizes command structure and known runners instead of searching arbitrary substrings, so `echo pytest` is not test evidence.

## 6. Verification expectations

Changes to TraceCoder are checked with:

```bash
python3 -m unittest discover -s tests -q
python3 -m compileall -q tracecoder
git diff --check
```

The regression suite must demonstrate both sides of the evidence rule: unchanged post-test workspaces may complete, while any post-test source modification makes the previous evidence stale.

## 7. Context reliability improvement

The original compactor retained the latest messages and short slices of earlier tool output. It could discard project rules, task intent, failure detail and current verification state while still marking nested instructions as already exposed.

The agent now maintains a structured `TaskState` independently from chat history. A dependency-free `ContextManager` rebuilds compacted context from the original objective, all discovered applicable `AGENTS.md` files, modified and read files, unresolved failures, important evidence and the latest test digest. Successful command logs are reduced to metadata; failed output receives a larger bounded allowance. Compaction uses both a character limit and the preceding API response's prompt-token count.

Regression tests use sentinel values to prove that task intent, instructions, failure evidence and test state survive compaction.

## 8. Remaining work

The next reliability work should improve progress detection. Repeated-action detection currently recognizes only three identical consecutive events and does not distinguish useful revisits from loops.

MCP, multi-agent orchestration and a frontend remain deliberate non-goals until a concrete use case justifies their additional lifecycle, permission and context complexity.
