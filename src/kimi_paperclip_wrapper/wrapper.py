#!/usr/bin/env python3
import json
import os
import pwd
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path


MARKER = "The above agent instructions were loaded from "
WRAPPER_PREFIX = "[paperclip-kimi-wrapper]"
UNKNOWN_SESSION_RE = re.compile(
    r"unknown (session|thread)|session .* not found|thread .* not found|conversation .* not found",
    re.IGNORECASE,
)
REFERENCE_TAG_RE = re.compile(r"\[R:([A-Z]+-\d+)\]")


def env_str(name: str) -> str:
    return os.environ.get(name, "").strip()


def env_flag(name: str, default: bool = False) -> bool:
    raw = env_str(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def strip_paperclip_instructions(prompt: str) -> tuple[str, str]:
    marker_index = prompt.find(MARKER)
    if marker_index < 0:
        return prompt, "parse_fallback"
    delimiter_index = prompt.find("\n\n", marker_index)
    if delimiter_index < 0:
        return prompt, "parse_fallback"
    stripped = prompt[delimiter_index + 2 :]
    if not stripped.strip():
        return prompt, "parse_fallback"
    return stripped, "resume_stripped"


def resolve_real_kimi() -> str:
    return env_str("PAPERCLIP_REAL_KIMI") or "/usr/local/bin/kimi"


def resolve_state_dir() -> Path:
    configured = env_str("PAPERCLIP_WRAPPER_STATE_DIR")
    if configured:
        return Path(configured)
    return resolve_runtime_home() / ".paperclip-kimi-wrapper"


def resolve_state_file() -> Path:
    return resolve_state_dir() / "kimi-session-map.json"


def resolve_debug_prompt_dir() -> Path:
    return resolve_state_dir() / "debug-prompts"


def resolve_paperclip_env_file() -> Path:
    configured = env_str("PAPERCLIP_ENV_FILE")
    if configured:
        return Path(configured)
    return Path("/etc/paperclip/paperclip.env")


def load_state() -> dict:
    try:
        return json.loads(resolve_state_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"agents": {}}
    except Exception:
        return {"agents": {}}


def save_state(data: dict) -> None:
    state_dir = resolve_state_dir()
    state_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=state_dir, delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp, ensure_ascii=False, indent=2, sort_keys=True)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp_path = Path(tmp.name)
    tmp_path.replace(resolve_state_file())


def debug_prompt_enabled() -> bool:
    value = env_str("PAPERCLIP_WRAPPER_DEBUG_PROMPT").lower()
    return value in {"1", "true", "yes", "on"}


def write_debug_prompt(prompt: str, mode: str) -> None:
    if not debug_prompt_enabled():
        return
    run_id = env_str("PAPERCLIP_RUN_ID") or "unknown-run"
    debug_dir = resolve_debug_prompt_dir()
    debug_dir.mkdir(parents=True, exist_ok=True)
    path = debug_dir / f"{run_id}.prompt.txt"
    header = [
        f"# wrapper mode: {mode}",
        f"# run id: {run_id}",
        f"# agent id: {env_str('PAPERCLIP_AGENT_ID') or 'unknown'}",
        "",
    ]
    path.write_text("\n".join(header) + prompt, encoding="utf-8")
    print(f"{WRAPPER_PREFIX} debug prompt written to {path}", file=sys.stderr)


def api_get_json(path: str) -> dict:
    base = env_str("PAPERCLIP_API_URL").rstrip("/")
    token = env_str("PAPERCLIP_API_KEY")
    if not base or not token:
        return {}
    req = urllib.request.Request(
        f"{base}{path}",
        headers={"authorization": f"Bearer {token}", "accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def load_database_url() -> str:
    value = env_str("DATABASE_URL")
    if value:
        return value
    try:
        for raw_line in resolve_paperclip_env_file().read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            if key.strip() != "DATABASE_URL":
                continue
            return raw_value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def resolve_runtime_home() -> Path:
    paperclip_home = env_str("PAPERCLIP_HOME")
    if paperclip_home:
        return Path(paperclip_home)
    home = env_str("HOME")
    if home and home != "/root":
        return Path(home)
    try:
        passwd_home = pwd.getpwuid(os.getuid()).pw_dir
        if passwd_home and passwd_home != "/root":
            return Path(passwd_home)
    except Exception:
        pass
    return Path.home()


def resolve_kimi_sessions_dir() -> Path:
    configured = env_str("PAPERCLIP_KIMI_SESSIONS_DIR")
    if configured:
        return Path(configured)
    return resolve_runtime_home() / ".kimi" / "sessions"


def resolve_skills_dir() -> Path:
    configured = env_str("PAPERCLIP_KIMI_SKILLS_DIR")
    if configured:
        return Path(configured)
    return resolve_runtime_home() / ".codex" / "skills"


def resolve_kimi_mcp_config_file() -> Path:
    configured = env_str("PAPERCLIP_KIMI_MCP_CONFIG_FILE")
    if configured:
        return Path(configured)
    return resolve_runtime_home() / ".kimi" / "mcp.enabled.json"


def is_writable_directory(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.W_OK | os.X_OK)
    except Exception:
        return False


def slugify_path(path: Path) -> str:
    value = str(path).strip().replace(os.sep, "_")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return value.strip("._-") or "workspace"


def resolve_kimi_work_dir() -> tuple[Path, list[Path]]:
    cwd = Path(os.getcwd())
    add_dirs: list[Path] = []
    if is_writable_directory(cwd):
        return cwd, add_dirs

    fallback_root = env_str("PAPERCLIP_KIMI_WORK_DIR")
    candidate_roots = []
    if fallback_root:
        candidate_roots.append(Path(fallback_root))
    candidate_roots.append(resolve_runtime_home() / ".kimi" / "workdirs")
    candidate_roots.append(Path("/tmp") / "paperclip-kimi-workdirs")

    for root in candidate_roots:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        if not is_writable_directory(root):
            continue
        work_dir = root / slugify_path(cwd)
        try:
            work_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        if not is_writable_directory(work_dir):
            continue
        add_dirs.append(cwd)
        return work_dir, add_dirs

    return cwd, add_dirs


def args_contain_option(args: list[str], option: str) -> bool:
    prefix = f"{option}="
    return any(arg == option or arg.startswith(prefix) for arg in args)


def sql_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def fetch_issue_run_actions(task_id: str, run_id: str) -> tuple[set[str] | None, str | None]:
    database_url = load_database_url()
    if not database_url:
        return None, "DATABASE_URL unavailable for issue-sync validation"
    sql = f"""
        select coalesce(string_agg(action, ',' order by action), '')
        from public.activity_log
        where entity_type = 'issue'
          and entity_id = {sql_quote(task_id)}
          and run_id = {sql_quote(run_id)}::uuid
          and action in ('issue.checked_out', 'issue.updated', 'issue.comment_added');
    """
    try:
        proc = subprocess.run(
            ["psql", database_url, "-Atqc", sql],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        return None, f"issue-sync validation query failed: {exc}"
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        return None, f"issue-sync validation query failed: {stderr or 'psql returned non-zero'}"
    raw_actions = (proc.stdout or "").strip()
    actions = {action.strip() for action in raw_actions.split(",") if action.strip()}
    return actions, None


def validate_issue_sync(meta: dict) -> tuple[bool, str | None]:
    if not env_flag("PAPERCLIP_VALIDATE_ISSUE_SYNC"):
        return True, None
    mode = env_str("PAPERCLIP_VALIDATE_ISSUE_SYNC_MODE") or "db"
    if mode != "db":
        return True, None
    task_id = str(meta.get("taskId") or "").strip()
    run_id = env_str("PAPERCLIP_RUN_ID")
    if not task_id:
        return True, None
    if not run_id:
        return False, "task-bound run missing PAPERCLIP_RUN_ID; cannot validate issue sync"
    actions, error = fetch_issue_run_actions(task_id, run_id)
    if actions is None:
        return False, error
    if "issue.updated" in actions or "issue.comment_added" in actions:
        return True, None
    if "issue.checked_out" in actions:
        return False, "run checked out the issue but produced no comment or status update; local handoff is not a substitute"
    return False, "task-bound run produced no observable issue update"


def build_comment_delta(meta: dict) -> str:
    issue_id = meta.get("taskId", "")
    comment_id = env_str("PAPERCLIP_WAKE_COMMENT_ID")
    wake_reason = env_str("PAPERCLIP_WAKE_REASON")
    if not issue_id or not comment_id:
        return ""
    try:
        comment = api_get_json(f"/api/issues/{issue_id}/comments/{comment_id}")
    except Exception:
        return ""
    body = str(comment.get("body") or "").strip()
    if not body:
        return ""
    identifier = meta.get("identifier", "") or issue_id
    title = meta.get("title", "")
    lines = [
        "Resume delta update from Paperclip:",
        f"- wake reason: {wake_reason or 'comment'}",
        f"- issue: {identifier}",
    ]
    if title:
        lines.append(f"- title: {title}")
    lines.extend(
        [
            f"- comment id: {comment_id}",
            "- new comment body follows:",
            body,
            "",
            "Treat this comment as the primary new input for this resumed run.",
        ]
    )
    return "\n".join(lines).strip()


def build_task_authority(meta: dict) -> str:
    task_id = meta.get("taskId", "")
    if not task_id:
        return ""
    wake_reason = env_str("PAPERCLIP_WAKE_REASON")
    identifier = meta.get("identifier", "") or task_id
    title = meta.get("title", "")
    lines = [
        "Paperclip task authority:",
        f"- wake reason: {wake_reason or 'unknown'}",
        f"- current issue: {identifier}",
        f"- task id: {task_id}",
    ]
    if title:
        lines.append(f"- title: {title}")
    if meta.get("projectId"):
        lines.append(f"- project id: {meta['projectId']}")
    if meta.get("parentId"):
        lines.append(f"- parent id: {meta['parentId']}")
    lines.extend(
        [
            "- Treat this task as the current authority for the run.",
            "- Keep narration short. Do the work, write durable results to files, and avoid long meta-explanations.",
            "- Do not validate the run by checking shell env visibility of PAPERCLIP_* variables.",
            "- Before exit, make the current issue observable through a deliverable, blocker, or concise progress update.",
        ]
    )
    return "\n".join(lines).strip()


def fetch_issue_context() -> dict:
    task_id = env_str("PAPERCLIP_TASK_ID")
    agent_id = env_str("PAPERCLIP_AGENT_ID")
    if not task_id or not agent_id:
        return {}
    try:
        issue = api_get_json(f"/api/issues/{task_id}")
    except Exception:
        return {"taskId": task_id, "agentId": agent_id}
    return {
        "taskId": task_id,
        "agentId": agent_id,
        "projectId": issue.get("projectId") or "",
        "parentId": issue.get("parentId") or "",
        "assigneeAgentId": issue.get("assigneeAgentId") or "",
        "identifier": issue.get("identifier") or "",
        "title": issue.get("title") or "",
    }


def should_force_fresh_session(meta: dict) -> bool:
    wake_reason = env_str("PAPERCLIP_WAKE_REASON")
    task_id = str(meta.get("taskId") or "").strip()
    # Timer-only dispatcher-style wakes should not accumulate one giant chat thread.
    return wake_reason == "heartbeat_timer" and not task_id


def issue_keys(meta: dict) -> list[str]:
    agent_id = meta.get("agentId", "")
    assignee_id = meta.get("assigneeAgentId", "") or agent_id
    keys: list[str] = []
    if meta.get("taskId"):
        keys.append(f"task:{meta['taskId']}")
    if meta.get("parentId") and assignee_id:
        keys.append(f"parent:{meta['parentId']}:agent:{assignee_id}")
    return keys


def extract_reference_identifier(meta: dict) -> str:
    title = str(meta.get("title") or "")
    match = REFERENCE_TAG_RE.search(title)
    if not match:
        return ""
    return match.group(1).strip()


def resolve_reference_resume(meta: dict, sessions: dict) -> tuple[str | None, str | None]:
    ref_identifier = extract_reference_identifier(meta)
    if not ref_identifier:
        return None, None
    for key, value in sessions.items():
        if not isinstance(value, dict):
            continue
        if str(value.get("identifier") or "").strip() != ref_identifier:
            continue
        session_id = str(value.get("sessionId") or "").strip()
        if session_id:
            return session_id, f"ref:{ref_identifier}"
    return None, None


def resolve_resume_session(meta: dict) -> tuple[str | None, str | None]:
    agent_id = meta.get("agentId", "")
    if not agent_id:
        return None, None
    state = load_state()
    agent_state = state.get("agents", {}).get(agent_id, {})
    sessions = agent_state.get("sessions", {})
    for key in issue_keys(meta):
        value = sessions.get(key)
        if isinstance(value, dict):
            session_id = (value.get("sessionId") or "").strip()
            if session_id:
                return session_id, key
    ref_session_id, ref_key = resolve_reference_resume(meta, sessions)
    if ref_session_id:
        return ref_session_id, ref_key
    return None, None


def update_state_with_session(meta: dict, session_id: str) -> None:
    agent_id = meta.get("agentId", "")
    if not agent_id or not session_id:
        return
    data = load_state()
    agent_state = data.setdefault("agents", {}).setdefault(agent_id, {"sessions": {}})
    sessions = agent_state.setdefault("sessions", {})
    payload = {
        "sessionId": session_id,
        "taskId": meta.get("taskId", ""),
        "projectId": meta.get("projectId", ""),
        "parentId": meta.get("parentId", ""),
        "assigneeAgentId": meta.get("assigneeAgentId", ""),
        "identifier": meta.get("identifier", ""),
    }
    for key in issue_keys(meta):
        sessions[key] = payload
    save_state(data)


def clear_state_key(agent_id: str, key: str | None) -> None:
    if not agent_id or not key:
        return
    data = load_state()
    agent_state = data.get("agents", {}).get(agent_id, {})
    sessions = agent_state.get("sessions", {})
    if key in sessions:
        del sessions[key]
        save_state(data)


def is_unknown_session(stdout: str, stderr: str) -> bool:
    haystack = f"{stdout}\n{stderr}"
    return bool(UNKNOWN_SESSION_RE.search(haystack))


def parse_codex_args(argv: list[str]) -> dict:
    model = ""
    resume_session = None
    passthrough: list[str] = []
    idx = 0
    while idx < len(argv):
        arg = argv[idx]
        if arg in {"exec", "--json", "-"}:
            idx += 1
            continue
        if arg == "resume":
            if idx + 1 < len(argv):
                resume_session = argv[idx + 1].strip() or None
                idx += 2
                continue
            idx += 1
            continue
        if arg == "--model":
            if idx + 1 < len(argv):
                model = argv[idx + 1].strip()
                idx += 2
                continue
            idx += 1
            continue
        if arg == "-c":
            idx += 2
            continue
        if arg in {"--search", "--dangerously-bypass-approvals-and-sandbox"}:
            idx += 1
            continue
        passthrough.append(arg)
        idx += 1
    return {
        "model": model,
        "resume_session": resume_session,
        "passthrough": passthrough,
    }


def _extract_stream_texts(event: dict) -> list[str]:
    if not isinstance(event, dict):
        return []
    event_type = _event_type(event)
    if event_type not in {"message.delta", "response.output_text.delta"}:
        return []
    texts: list[str] = []
    for part in _content_parts(event):
        if part.get("type") != "text":
            continue
        text = str(part.get("text") or "")
        if text:
            texts.append(text)
    return texts


def _extract_progress_message(event: dict) -> str | None:
    if not isinstance(event, dict):
        return None
    event_type = _event_type(event)
    message = event.get("message")
    payload = message.get("payload") if isinstance(message, dict) else None

    if event_type == "stepbegin" and isinstance(payload, dict):
        step = payload.get("n")
        if step is not None:
            return f"step {step} started"
        return "step started"

    if event_type == "statusupdate" and isinstance(payload, dict):
        token_usage = payload.get("token_usage") or {}
        output_tokens = token_usage.get("output")
        context_tokens = payload.get("context_tokens")
        parts = ["status update"]
        if context_tokens is not None:
            parts.append(f"context={context_tokens}")
        if output_tokens is not None:
            parts.append(f"output={output_tokens}")
        return " ".join(parts)

    if event_type == "toolcall" and isinstance(payload, dict):
        inner = payload.get("function") or {}
        name = inner.get("name") or payload.get("type") or "tool"
        return f"tool start: {name}"

    if event_type == "toolresult" and isinstance(payload, dict):
        result = payload.get("return_value") or {}
        if result.get("is_error"):
            detail = result.get("message") or "error"
            return f"tool error: {detail}"
        message_text = result.get("message") or "ok"
        return f"tool done: {message_text}"

    return None


def stream_process(args: list[str], prompt: str, on_text=None) -> tuple[int, str, str]:
    inactivity_timeout = max(env_int("PAPERCLIP_KIMI_INACTIVITY_TIMEOUT_SECONDS", 900), 0)
    kill_grace = max(env_int("PAPERCLIP_KIMI_KILL_GRACE_SECONDS", 15), 1)
    progress_interval = max(env_int("PAPERCLIP_KIMI_PROGRESS_LOG_INTERVAL_SECONDS", 60), 0)
    proc = subprocess.Popen(
        [resolve_real_kimi(), *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    assert proc.stderr is not None

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    activity_lock = threading.Lock()
    last_activity = time.monotonic()
    last_progress_log = 0.0
    timeout_message: list[str] = []

    def mark_activity() -> None:
        nonlocal last_activity
        with activity_lock:
            last_activity = time.monotonic()

    def stdout_reader() -> None:
        nonlocal last_progress_log
        for chunk in proc.stdout:
            mark_activity()
            stdout_chunks.append(chunk)
            if on_text is None:
                pass
            for raw_line in chunk.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                progress = _extract_progress_message(event)
                if progress:
                    now = time.monotonic()
                    event_type = _event_type(event)
                    should_log = True
                    if event_type == "statusupdate" and progress_interval > 0:
                        should_log = (now - last_progress_log) >= progress_interval
                    if should_log:
                        last_progress_log = now
                        progress_line = f"{WRAPPER_PREFIX} {progress}\n"
                        stderr_chunks.append(progress_line)
                        sys.stderr.write(progress_line)
                        sys.stderr.flush()
                for text in _extract_stream_texts(event):
                    if on_text is not None:
                        on_text(text)

    def stderr_reader() -> None:
        for chunk in proc.stderr:
            mark_activity()
            stderr_chunks.append(chunk)
            sys.stderr.write(chunk)
            sys.stderr.flush()

    def watchdog() -> None:
        if inactivity_timeout <= 0:
            return
        while proc.poll() is None:
            time.sleep(5)
            with activity_lock:
                idle_seconds = time.monotonic() - last_activity
            if idle_seconds < inactivity_timeout:
                continue
            message = (
                f"{WRAPPER_PREFIX} terminating Kimi after {int(idle_seconds)}s without stdout/stderr activity"
            )
            timeout_message.append(message)
            stderr_chunks.append(message + "\n")
            sys.stderr.write(message + "\n")
            sys.stderr.flush()
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + kill_grace
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.2)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    return
            return

    stdout_thread = threading.Thread(target=stdout_reader)
    stderr_thread = threading.Thread(target=stderr_reader)
    watchdog_thread = threading.Thread(target=watchdog, daemon=True)
    stdout_thread.start()
    stderr_thread.start()
    watchdog_thread.start()
    proc.stdin.write(prompt)
    proc.stdin.close()
    stdout_thread.join()
    stderr_thread.join()
    return proc.wait(), "".join(stdout_chunks), "".join(stderr_chunks)


def _event_role(event: dict) -> str:
    for key in ("role", "speaker", "author_role"):
        value = str(event.get(key) or "").strip().lower()
        if value:
            return value
    message = event.get("message")
    if isinstance(message, dict):
        for key in ("role", "speaker", "author_role"):
            value = str(message.get(key) or "").strip().lower()
            if value:
                return value
    return ""


def _event_type(event: dict) -> str:
    for key in ("type", "event", "kind"):
        value = str(event.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def _content_parts(event: dict) -> list[dict]:
    content = event.get("content")
    if isinstance(content, list):
        return [part for part in content if isinstance(part, dict)]
    message = event.get("message")
    if isinstance(message, dict):
        inner = message.get("content")
        if isinstance(inner, list):
            return [part for part in inner if isinstance(part, dict)]
    return []


def _is_assistant_event(event: dict) -> bool:
    role = _event_role(event)
    if role == "assistant":
        return True
    event_type = _event_type(event)
    if event_type in {
        "assistant",
        "assistant_message",
        "response.completed",
        "message.completed",
        "message.delta",
        "response.output_text.delta",
        "response.output_text.done",
    }:
        return True
    return False


def extract_assistant_text(stdout: str) -> str:
    messages: list[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        if not _is_assistant_event(event):
            continue
        for part in _content_parts(event):
            if part.get("type") != "text":
                continue
            text = str(part.get("text") or "").strip()
            if text:
                messages.append(text)
    if messages:
        return "\n\n".join(messages).strip()
    return ""


def resolve_kimi_session_wire_path(session_id: str) -> Path | None:
    if not session_id:
        return None
    try:
        matches = sorted(resolve_kimi_sessions_dir().glob(f"*/{session_id}/wire.jsonl"))
    except Exception:
        return None
    if matches:
        return matches[-1]
    return None


def parse_kimi_usage(session_id: str) -> tuple[dict, str | None]:
    wire_path = resolve_kimi_session_wire_path(session_id)
    if wire_path is None:
        return (
            {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
            },
            f"Kimi usage unavailable: no wire.jsonl found for session {session_id} under {resolve_kimi_sessions_dir()}",
        )

    last_token_usage = None
    try:
        with wire_path.open("r", encoding="utf-8", errors="ignore") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if not isinstance(event, dict):
                    continue
                message = event.get("message")
                if not isinstance(message, dict):
                    continue
                if str(message.get("type") or "").strip() != "StatusUpdate":
                    continue
                payload = message.get("payload")
                if not isinstance(payload, dict):
                    continue
                token_usage = payload.get("token_usage")
                if not isinstance(token_usage, dict):
                    continue
                last_token_usage = token_usage
    except Exception as exc:
        return (
            {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
            },
            f"Kimi usage unavailable: failed reading {wire_path}: {exc}",
        )

    if not isinstance(last_token_usage, dict):
        return (
            {
                "input_tokens": 0,
                "cached_input_tokens": 0,
                "output_tokens": 0,
            },
            f"Kimi usage unavailable: no StatusUpdate token_usage found in {wire_path}",
        )

    input_other = int(last_token_usage.get("input_other") or 0)
    input_cache_read = int(last_token_usage.get("input_cache_read") or 0)
    input_cache_creation = int(last_token_usage.get("input_cache_creation") or 0)
    output = int(last_token_usage.get("output") or 0)
    return (
        {
            "input_tokens": input_other + input_cache_read + input_cache_creation,
            "cached_input_tokens": input_cache_read,
            "output_tokens": output,
        },
        None,
    )


def emit_event(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def build_kimi_args(
    session_id: str, model: str, extra_args: list[str], work_dir: Path, add_dirs: list[Path]
) -> list[str]:
    args = [
        "--session",
        session_id,
        "--work-dir",
        str(work_dir),
        "--print",
        "--input-format",
        "text",
        "--output-format",
        "stream-json",
    ]
    if model:
        args.extend(["--model", model])
    skills_dir = resolve_skills_dir()
    if skills_dir.exists() and not args_contain_option(extra_args, "--skills-dir"):
        args.extend(["--skills-dir", str(skills_dir)])
    for add_dir in add_dirs:
        if args_contain_option(extra_args, "--add-dir"):
            break
        args.extend(["--add-dir", str(add_dir)])
    if not args_contain_option(extra_args, "--mcp-config-file"):
        if env_flag("PAPERCLIP_KIMI_ENABLE_MCP"):
            mcp_config_file = resolve_kimi_mcp_config_file()
            if mcp_config_file.exists():
                args.extend(["--mcp-config-file", str(mcp_config_file)])
    args.extend(extra_args)
    return args


def run(argv: list[str], prompt: str) -> int:
    parsed_args = parse_codex_args(argv)
    incoming_resume_session = parsed_args["resume_session"]
    prompt_mode = "pass_through"
    rewritten_prompt = prompt
    meta = fetch_issue_context()
    wrapper_key = None
    wrapper_injected_resume = False
    force_fresh_session = should_force_fresh_session(meta)
    work_dir, add_dirs = resolve_kimi_work_dir()

    if incoming_resume_session and not force_fresh_session:
        session_id = incoming_resume_session
        rewritten_prompt, prompt_mode = strip_paperclip_instructions(prompt)
    else:
        session_id = None
        if not force_fresh_session:
            session_id, wrapper_key = resolve_resume_session(meta)
        if session_id:
            wrapper_injected_resume = True
            rewritten_prompt, stripped_mode = strip_paperclip_instructions(prompt)
            prompt_mode = f"wrapper_resume:{stripped_mode}"
        else:
            session_id = str(uuid.uuid4())
            prompt_mode = "fresh_session"
            if force_fresh_session:
                prompt_mode = "fresh_session:timer_no_task"

    task_authority = build_task_authority(meta)
    if task_authority:
        rewritten_prompt = f"{rewritten_prompt.rstrip()}\n\n{task_authority}\n"
        prompt_mode = f"{prompt_mode}+task_authority"

    comment_delta = build_comment_delta(meta)
    if comment_delta:
        rewritten_prompt = f"{rewritten_prompt.rstrip()}\n\n{comment_delta}\n"
        prompt_mode = f"{prompt_mode}+comment_delta"

    write_debug_prompt(rewritten_prompt, prompt_mode)
    print(f"{WRAPPER_PREFIX} mode={prompt_mode} session={session_id}", file=sys.stderr)

    emit_event({"type": "thread.started", "thread_id": session_id})

    kimi_args = build_kimi_args(
        session_id,
        parsed_args["model"],
        parsed_args["passthrough"],
        work_dir,
        add_dirs,
    )
    exit_code, stdout_text, stderr_text = stream_process(
        kimi_args,
        rewritten_prompt,
        on_text=lambda text: emit_event({"type": "item.completed", "item": {"type": "agent_message", "text": text}}),
    )

    if wrapper_injected_resume and exit_code != 0 and is_unknown_session(stdout_text, stderr_text):
        print(
            f"{WRAPPER_PREFIX} injected resume session unavailable; retrying fresh",
            file=sys.stderr,
        )
        clear_state_key(meta.get("agentId", ""), wrapper_key)
        session_id = str(uuid.uuid4())
        emit_event({"type": "thread.started", "thread_id": session_id})
        kimi_args = build_kimi_args(
            session_id,
            parsed_args["model"],
            parsed_args["passthrough"],
            work_dir,
            add_dirs,
        )
        exit_code, stdout_text, stderr_text = stream_process(
            kimi_args,
            prompt,
            on_text=lambda text: emit_event({"type": "item.completed", "item": {"type": "agent_message", "text": text}}),
        )

    if exit_code == 0:
        assistant_text = extract_assistant_text(stdout_text)
        sync_ok, sync_error = validate_issue_sync(meta)
        if not sync_ok:
            emit_event({"type": "error", "message": sync_error or "Issue sync validation failed"})
            return 1
        usage, usage_warning = parse_kimi_usage(session_id)
        if usage_warning:
            print(f"{WRAPPER_PREFIX} {usage_warning}", file=sys.stderr)
        if assistant_text:
            emit_event(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": assistant_text},
                }
            )
        emit_event(
            {
                "type": "turn.completed",
                "usage": usage,
            }
        )
        update_state_with_session(meta, session_id)
        return 0

    error_message = stderr_text.strip() or stdout_text.strip() or f"Kimi exited with code {exit_code}"
    emit_event({"type": "error", "message": error_message})
    return exit_code


def entrypoint() -> int:
    return run(sys.argv[1:], sys.stdin.read())


if __name__ == "__main__":
    raise SystemExit(entrypoint())
