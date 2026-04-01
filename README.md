# Paperclip Kimi Wrapper

Portable Kimi CLI wrapper for self-hosted Linux Paperclip installations.

This package adapts Kimi CLI runs to the event stream Paperclip expects from `codex_local` agents, while keeping the host-specific parts configurable.

## What it does

- launches Kimi CLI and forwards the prompt
- emits Paperclip-compatible events:
  - `thread.started`
  - `item.completed` with `agent_message`
  - `turn.completed`
  - `error`
- supports wrapper-side session reuse
- injects compact task authority and comment deltas when Paperclip task env is present
- can enforce task-bound issue-sync validation
- reads token usage from Kimi `wire.jsonl` session logs when available

## What it is not

- not a generic Paperclip plugin
- not tested for managed/cloud Paperclip installs
- not a drop-in copy of a live host setup with fixed filesystem paths

## Quickstart

1. Install or copy this repo to the target host.
2. Ensure `kimi` is installed and reachable.
3. Point the Paperclip agent command to this wrapper:

```json
{
  "adapterType": "codex_local",
  "adapterConfig": {
    "cwd": "/srv/paperclip/workspaces/acme",
    "model": "kimi-code/kimi-for-coding",
    "search": false,
    "command": "/srv/paperclip/tools/paperclip-kimi-wrapper/bin/kimi-paperclip-wrapper",
    "instructionsFilePath": "/srv/paperclip/workspaces/acme/agents/architect/AGENTS.md",
    "dangerouslyBypassApprovalsAndSandbox": true
  }
}
```

4. Configure the environment variables from [`examples/paperclip.env.example`](examples/paperclip.env.example).
5. Run a low-risk task and inspect the run stderr for a line like:

```text
[paperclip-kimi-wrapper] mode=fresh_session session=...
```

## Package layout

- `bin/kimi-paperclip-wrapper`
- `src/kimi_paperclip_wrapper/wrapper.py`
- `docs/install.md`
- `docs/config.md`
- `docs/operations.md`
- `examples/agent-config.json`
- `examples/paperclip.env.example`
- `tests/test_wrapper.py`

## Optional strict issue-sync validation

Issue-sync validation is available but optional.

- `PAPERCLIP_VALIDATE_ISSUE_SYNC=1`
- `PAPERCLIP_VALIDATE_ISSUE_SYNC_MODE=db`

In that mode, the wrapper checks whether the current task-bound run produced an observable issue mutation instead of only local work. Hosts without local PostgreSQL access can leave validation off.

## Development

Run tests:

```bash
python3 -m unittest discover -s tests -v
```
