import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kimi_paperclip_wrapper import wrapper


class WrapperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.env_patch = mock.patch.dict(
            os.environ,
            {
                "HOME": self.tempdir.name,
                "PAPERCLIP_WRAPPER_STATE_DIR": str(Path(self.tempdir.name) / "state"),
                "PAPERCLIP_KIMI_SESSIONS_DIR": str(Path(self.tempdir.name) / "kimi-sessions"),
            },
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def test_extract_assistant_text_ignores_non_assistant_events(self) -> None:
        stdout = "\n".join(
            [
                json.dumps({"type": "tool_output", "content": [{"type": "text", "text": "ignore"}]}),
                json.dumps({"type": "assistant_message", "content": [{"type": "text", "text": "hello"}]}),
                json.dumps({"role": "assistant", "content": [{"type": "text", "text": "world"}]}),
            ]
        )
        self.assertEqual(wrapper.extract_assistant_text(stdout), "hello\n\nworld")

    def test_parse_kimi_usage_reads_latest_status_update(self) -> None:
        session_id = "session-123"
        wire_path = Path(os.environ["PAPERCLIP_KIMI_SESSIONS_DIR"]) / "a" / session_id / "wire.jsonl"
        wire_path.parent.mkdir(parents=True, exist_ok=True)
        wire_path.write_text(
            "\n".join(
                [
                    json.dumps({"message": {"type": "StatusUpdate", "payload": {"token_usage": {"input_other": 1, "input_cache_read": 2, "input_cache_creation": 3, "output": 4}}}}),
                    json.dumps({"message": {"type": "StatusUpdate", "payload": {"token_usage": {"input_other": 5, "input_cache_read": 6, "input_cache_creation": 7, "output": 8}}}}),
                ]
            ),
            encoding="utf-8",
        )
        usage, warning = wrapper.parse_kimi_usage(session_id)
        self.assertIsNone(warning)
        self.assertEqual(
            usage,
            {"input_tokens": 18, "cached_input_tokens": 6, "output_tokens": 8},
        )

    def test_validate_issue_sync_skips_when_disabled(self) -> None:
        with mock.patch.dict(os.environ, {"PAPERCLIP_VALIDATE_ISSUE_SYNC": "0"}, clear=False):
            ok, error = wrapper.validate_issue_sync({"taskId": "issue-1"})
        self.assertTrue(ok)
        self.assertIsNone(error)

    def test_validate_issue_sync_requires_mutation_when_enabled(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"PAPERCLIP_VALIDATE_ISSUE_SYNC": "1", "PAPERCLIP_RUN_ID": "run-1"},
            clear=False,
        ):
            with mock.patch.object(wrapper, "fetch_issue_run_actions", return_value=({"issue.checked_out"}, None)):
                ok, error = wrapper.validate_issue_sync({"taskId": "issue-1"})
        self.assertFalse(ok)
        self.assertIn("produced no comment or status update", error)

    def test_resolve_resume_session_prefers_same_task(self) -> None:
        state_path = Path(os.environ["PAPERCLIP_WRAPPER_STATE_DIR"]) / "kimi-session-map.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "agents": {
                        "agent-1": {
                            "sessions": {
                                "task:task-1": {"sessionId": "session-task-1", "identifier": "MEL-1"},
                                "parent:parent-1:agent:agent-1": {"sessionId": "session-parent-1", "identifier": "MEL-2"},
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        session_id, key = wrapper.resolve_resume_session({"agentId": "agent-1", "taskId": "task-1", "parentId": "parent-1"})
        self.assertEqual(session_id, "session-task-1")
        self.assertEqual(key, "task:task-1")

    def test_should_force_fresh_session_for_timer_without_task(self) -> None:
        with mock.patch.dict(os.environ, {"PAPERCLIP_WAKE_REASON": "heartbeat_timer"}, clear=False):
            self.assertTrue(wrapper.should_force_fresh_session({}))
            self.assertTrue(wrapper.should_force_fresh_session({"taskId": ""}))
            self.assertFalse(wrapper.should_force_fresh_session({"taskId": "task-1"}))

    def test_run_emits_events_and_updates_state(self) -> None:
        with mock.patch.object(wrapper, "fetch_issue_context", return_value={"agentId": "agent-1", "taskId": "task-1", "identifier": "MEL-1"}):
            with mock.patch.object(
                wrapper,
                "stream_process",
                return_value=(
                    0,
                    json.dumps({"type": "assistant_message", "content": [{"type": "text", "text": "done"}]}) + "\n",
                    "",
                ),
            ):
                with mock.patch.object(wrapper, "parse_kimi_usage", return_value=({"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 2}, None)):
                    with mock.patch.object(wrapper, "validate_issue_sync", return_value=(True, None)):
                        with mock.patch.object(wrapper.uuid, "uuid4", return_value="new-session-id"):
                            with mock.patch("sys.stdout", new_callable=mock.MagicMock()) as stdout:
                                exit_code = wrapper.run(["exec", "--json", "-"], "prompt")
        self.assertEqual(exit_code, 0)
        writes = "".join(call.args[0] for call in stdout.write.call_args_list)
        self.assertIn('"type": "thread.started"', writes)
        self.assertIn('"thread_id": "new-session-id"', writes)
        self.assertIn('"type": "turn.completed"', writes)
        self.assertIn('"text": "done"', writes)


if __name__ == "__main__":
    unittest.main()
