# Operations

## Session reuse behavior

The wrapper keeps its own session map and tries to reuse sessions in this order:

1. same task id
2. same parent id plus assignee
3. explicit title reference tag like `[R:MEL-25]`

If an injected resume session is rejected by Kimi as unknown, the wrapper:

- clears that mapping
- retries once with a fresh session

## Prompt injection behavior

When Paperclip task env exists, the wrapper appends:

- a compact task-authority block
- a comment-delta block for comment-driven resumes

This keeps task-bound runs short while still surfacing the newest operational context.

## State files

State is written under `PAPERCLIP_WRAPPER_STATE_DIR`:

- `kimi-session-map.json`
- `debug-prompts/<run-id>.prompt.txt`

## Usage reporting

The wrapper reads the active Kimi session `wire.jsonl` and maps the latest `StatusUpdate.payload.token_usage` into:

- `input_tokens`
- `cached_input_tokens`
- `output_tokens`

If usage extraction fails, the run still completes and logs a warning.

## Strict issue-sync mode

With DB validation enabled, a task-bound run is considered successful only if the current run produced:

- `issue.updated`, or
- `issue.comment_added`

A run that only checked out the issue is treated as an error.

## Troubleshooting

### No usage data

Check:

- `PAPERCLIP_KIMI_SESSIONS_DIR`
- whether Kimi actually writes `wire.jsonl`
- whether the wrapper user and the Kimi runtime user are the same

### Resume fails unexpectedly

Check:

- `kimi-session-map.json`
- Kimi stderr for unknown-session errors
- whether the state directory is shared across restarts

### Comment delta missing

Check:

- `PAPERCLIP_WAKE_COMMENT_ID`
- `PAPERCLIP_API_URL`
- `PAPERCLIP_API_KEY`

### Strict sync validation fails

Check:

- `PAPERCLIP_RUN_ID`
- `DATABASE_URL`
- whether the host actually logs issue mutations in `public.activity_log`
