# Config

## Environment variables

### Core paths

- `PAPERCLIP_REAL_KIMI`
  - path to the Kimi CLI binary
  - default: `/usr/local/bin/kimi`

- `PAPERCLIP_HOME`
  - preferred runtime home for state and Kimi session discovery
  - default: current user home

- `PAPERCLIP_WRAPPER_STATE_DIR`
  - directory for session maps and debug prompt dumps
  - default: `$PAPERCLIP_HOME/.paperclip-kimi-wrapper`

- `PAPERCLIP_KIMI_SESSIONS_DIR`
  - base directory that contains Kimi session folders
  - default: `$PAPERCLIP_HOME/.kimi/sessions`

- `PAPERCLIP_ENV_FILE`
  - env file used only as a fallback source for `DATABASE_URL`
  - default: `/etc/paperclip/paperclip.env`

### Debugging

- `PAPERCLIP_WRAPPER_DEBUG_PROMPT`
  - when true, write the final prompt body to `debug-prompts/<run-id>.prompt.txt`
  - accepted truthy values: `1`, `true`, `yes`, `on`

### Strict issue-sync validation

- `PAPERCLIP_VALIDATE_ISSUE_SYNC`
  - enable validation for task-bound runs
  - default: off

- `PAPERCLIP_VALIDATE_ISSUE_SYNC_MODE`
  - validation backend
  - supported values:
    - `db`
    - `off`
  - default: `db` when validation is enabled

- `DATABASE_URL`
  - PostgreSQL URL used by DB validation mode
  - if unset, the wrapper tries `PAPERCLIP_ENV_FILE`

## Paperclip runtime env the wrapper consumes

The wrapper also reads standard Paperclip runtime variables when present:

- `PAPERCLIP_API_URL`
- `PAPERCLIP_API_KEY`
- `PAPERCLIP_TASK_ID`
- `PAPERCLIP_AGENT_ID`
- `PAPERCLIP_RUN_ID`
- `PAPERCLIP_WAKE_COMMENT_ID`
- `PAPERCLIP_WAKE_REASON`

These power:

- issue metadata fetch
- comment-delta injection
- task-authority injection
- optional issue-sync validation

## Recommended defaults

For a second self-hosted Linux install:

- keep DB validation off initially
- enable debug prompts during first rollout
- explicitly set `PAPERCLIP_WRAPPER_STATE_DIR`
- explicitly set `PAPERCLIP_KIMI_SESSIONS_DIR` if Kimi uses a nonstandard home
