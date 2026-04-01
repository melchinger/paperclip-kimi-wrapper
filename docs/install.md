# Install

## Target

This wrapper is intended for self-hosted Linux Paperclip installs where agents use `adapterType: codex_local`.

## Prerequisites

- Python 3.11+
- Kimi CLI installed on the host
- a working self-hosted Paperclip instance
- a Paperclip agent configured with `adapterType: codex_local`

## Recommended placement

Place the package outside the live Paperclip app tree. Example:

```bash
mkdir -p /srv/paperclip/tools
cp -a paperclip-kimi-wrapper /srv/paperclip/tools/
chmod +x /srv/paperclip/tools/paperclip-kimi-wrapper/bin/kimi-paperclip-wrapper
```

## Agent configuration

Point the agent command at the wrapper:

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

## Minimal environment

Required:

- `PAPERCLIP_REAL_KIMI` if `kimi` is not at `/usr/local/bin/kimi`

Optional but usually useful:

- `PAPERCLIP_HOME`
- `PAPERCLIP_WRAPPER_STATE_DIR`
- `PAPERCLIP_KIMI_SESSIONS_DIR`
- `PAPERCLIP_WRAPPER_DEBUG_PROMPT`

Optional for strict issue-sync validation:

- `PAPERCLIP_VALIDATE_ISSUE_SYNC=1`
- `PAPERCLIP_VALIDATE_ISSUE_SYNC_MODE=db`
- `DATABASE_URL` or `PAPERCLIP_ENV_FILE`

## Smoke test

Run a low-risk task and inspect stderr:

```text
[paperclip-kimi-wrapper] mode=fresh_session session=...
```

Expected behavior:

- Paperclip receives `thread.started`
- successful runs emit `turn.completed`
- if Kimi output contains assistant text, an `agent_message` event is emitted
