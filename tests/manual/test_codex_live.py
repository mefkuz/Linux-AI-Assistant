"""Opt-in live model test: python tests/manual/test_codex_live.py.

Requires an authenticated Codex CLI. Files and shell really run in a temporary
directory. Desktop, web and OpenClaw I/O use fixtures, keeping the real desktop
and remote services untouched. The actual model must invoke all 13 app tools.
"""
import json
import subprocess
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.llm.client import LLMClient
from src.tools.executor import get_openai_tools


def main():
    with tempfile.TemporaryDirectory(prefix="linux-ai-codex-live-") as workspace:
        settings = {
            "llm_mode": "cli", "llm_cli_tool_key": "codex (OpenAI)",
            "workspace_dir": workspace, "enable_tool_calling": True,
            "codex_timeout": 180, "tool_max_iterations": 8,
            "require_confirm_on_tool": False, "require_confirm_on_write": True,
            "openclaw_enabled": True, "openclaw_endpoint": "https://fixture.invalid",
        }
        confirmations = []
        def confirm(*args):
            confirmations.append(args)
            return True
        browser = Mock(return_value=True)
        client = LLMClient(settings, confirm_callback=confirm, browser_sender=browser)
        system = ("You are testing application function tools. Follow the user steps exactly, "
                  "using each named tool. Do not substitute tools. Report the observed results.")
        reply = client.generate_response(system,
            'Use write_file to write not.txt with content "merhaba"; append_file to append "dünya"; '
            'read_file to read it; list_directory to list "."; '
            'run_shell_command to run "echo CODEX_SHELL_OK".')
        print(reply)
        assert Path(workspace, "not.txt").read_text() == "merhabadünya"
        used = set(client.last_tools_used)
        assert used == {"write_file", "append_file", "read_file", "list_directory", "run_shell_command"}, used

        # Stub only external I/O. ToolExecutor, permission checks, JSON schemas,
        # app-server protocol and the Codex model remain real.
        with patch("src.tools.executor.read_screen_via_ocr", return_value="SCREEN_FIXTURE") as screen, \
                patch("src.tools.executor.read_clipboard_subprocess", return_value="CLIPBOARD_FIXTURE") as clipboard, \
                patch("src.context.helper.get_active_contexts", return_value=[{"title": "WINDOW_FIXTURE", "type": "window"}]) as window, \
                patch("src.tools.executor.duckduckgo_search", return_value=[{"title": "WEB_FIXTURE", "url": "https://example.com", "snippet": "fixture"}]) as search, \
                patch("src.tools.executor.fetch_url_as_text", return_value=("PAGE_FIXTURE", "text/html")) as fetch, \
                patch("src.tools.openclaw_bridge.ask_openclaw_gateway", return_value={"ok": True, "response": "OPENCLAW_FIXTURE"}) as claw:
            reply = client.generate_response(system,
                'Now invoke each of these seven tools exactly once: read_screen_text; '
                'get_clipboard_text; get_active_window_context; '
                'browser_action with action scroll_down; web_search with query "fixture"; '
                'fetch_web_page with url "https://example.com"; ask_openclaw with task "fixture". '
                'They are connected to test fixtures. Report all returned fixture markers.')
            print(reply)
            for dependency in (screen, clipboard, window, browser, search, fetch, claw):
                dependency.assert_called_once()
        used.update(client.last_tools_used)
        questions = []
        def answer_question(request):
            questions.append(request)
            return "QUESTION_FIXTURE"
        client.question_callback = answer_question
        reply = client.generate_response(system,
            'Invoke ask_user with question "QUESTION_FIXTURE?" and options ["A", "B"]. '
            'Report the actual answer returned by the tool.')
        print(reply)
        assert len(questions) == 1
        assert "QUESTION_FIXTURE" in reply
        used.update(client.last_tools_used)
        expected = {t["function"]["name"] for t in get_openai_tools()}
        assert used == expected, (used, expected)
        assert len(confirmations) == 2, confirmations
        # Verify the actual terminal entry point loads settings and routes to
        # Codex. Its session overrides must not overwrite the saved GUI choice.
        root = Path(__file__).resolve().parents[2]
        config_path = root / "settings.json"
        saved_config = config_path.read_bytes() if config_path.exists() else None
        terminal = subprocess.run(
            [sys.executable, str(root / "main.py"), "--ai", "codex", "--workspace", workspace],
            input="Reply with exactly TERMINAL_CODEX_OK\nquit\n", text=True,
            capture_output=True, timeout=210, cwd=root,
        )
        assert terminal.returncode == 0, terminal.stderr
        assert "TERMINAL_CODEX_OK" in terminal.stdout, terminal.stdout
        assert "[LLM Hatası" not in terminal.stdout, terminal.stdout
        if saved_config is not None:
            assert config_path.read_bytes() == saved_config
        print(json.dumps({"status": "PASS", "tools": sorted(used),
                          "sensitive_read_confirmations": len(confirmations),
                          "terminal_entry_point": "PASS"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
