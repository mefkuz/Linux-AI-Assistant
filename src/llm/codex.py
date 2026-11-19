"""Codex stdio app-server adapter; application tools keep their usual policy.

Each request gets an ephemeral thread. The caller supplies the same bounded
history as the HTTP client, so clear-memory and internal calls stay isolated.
"""
import json
import os
import queue
import shlex
import subprocess
import tempfile
import threading
import time
from src.tools.questions import UserQuestionCancelled, question_was_cancelled, clarification_question, visual_question_request, drain_answer_images


class CodexSession:
    def __init__(self, workspace, extra_args="", timeout=180):
        self.workspace = os.path.abspath(os.path.expanduser(workspace))
        if not os.path.isdir(self.workspace):
            raise ValueError("Codex çalışma dizini bulunamadı: " + self.workspace)
        # Only config overrides are meaningful for an app-server subprocess.
        # Permission, transport and command flags are owned by this adapter.
        args = shlex.split(extra_args)
        self.extra_args = []
        while args:
            flag = args.pop(0)
            if flag not in ("-c", "--config") or not args:
                raise ValueError("Codex ek argümanları yalnızca -c anahtar=değer destekler.")
            value = args.pop(0)
            if value.split("=", 1)[0].strip() not in (
                "model_reasoning_effort", "model_verbosity", "service_tier",
            ):
                raise ValueError("Desteklenmeyen Codex ayarı: " + value.split("=", 1)[0])
            self.extra_args.extend(["-c", value])
        self.timeout = max(1, float(timeout))
        self.events = queue.Queue()
        self.serial = 0
        self.process = None
        self.diagnostics = None

    def __enter__(self):
        self.diagnostics = tempfile.TemporaryFile(mode="w+t", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                ["codex", "app-server", *self.extra_args], cwd=self.workspace,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=self.diagnostics, text=True, encoding="utf-8", bufsize=1,
            )
        except FileNotFoundError as exc:
            self.diagnostics.close()
            raise RuntimeError("Codex bulunamadı. Codex CLI kurun ve codex login çalıştırın.") from exc
        except OSError:
            self.diagnostics.close()
            raise
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.deadline = time.monotonic() + self.timeout
        try:
            self.request("initialize", {
                "clientInfo": {"name": "linux_ai_assistant", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True},
            })
            self.send({"method": "initialized", "params": {}})
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def _read(self):
        try:
            for line in self.process.stdout:
                self.events.put(json.loads(line))
        except Exception as exc:
            self.events.put(exc)
        finally:
            self.events.put(EOFError("Codex app-server bağlantısı kapandı."))

    def send(self, message):
        self.process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
        self.process.stdin.flush()

    def receive(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Codex yanıt süresi doldu.")
        try:
            event = self.events.get(timeout=remaining)
        except queue.Empty as exc:
            raise TimeoutError("Codex yanıt süresi doldu.") from exc
        if isinstance(event, Exception):
            if isinstance(event, EOFError) and self.diagnostics:
                self.diagnostics.seek(0)
                detail = self.diagnostics.read()[-1500:].strip()
                if detail:
                    raise RuntimeError(str(event) + "\n" + detail)
            raise event
        return event

    def request(self, method, params):
        self.serial += 1
        request_id = self.serial
        self.send({"id": request_id, "method": method, "params": params})
        while True:
            event = self.receive()
            if event.get("id") == request_id and "method" not in event:
                if "error" in event:
                    raise RuntimeError("Codex: " + event["error"].get("message", str(event["error"])))
                return event.get("result", {})
            self.handle(event)

    def handle(self, event):
        method = event.get("method", "")
        params = event.get("params") or {}
        if "id" in event and method:
            if method == "item/tool/call":
                name = params.get("tool", "")
                result = self.execute_app_tool(name, params.get("arguments", {}))
                content = [{"type": "inputText", "text": result}]
                content.extend({'type': 'inputImage', 'imageUrl': url} for url in drain_answer_images(self.executor))
                self.send({"id": event["id"], "result": {
                    "contentItems": content, "success": True,
                }})
                if question_was_cancelled(name, result):
                    raise UserQuestionCancelled()
            elif method.endswith("/requestApproval"):
                # Native Codex actions must not bypass ToolExecutor policy.
                self.send({"id": event["id"], "result": {"decision": "decline"}})
            else:
                self.send({"id": event["id"], "error": {
                    "code": -32601, "message": "Use the supplied application tools.",
                }})
        elif method == "item/completed":
            item = params.get("item", {})
            if item.get("type") == "agentMessage" and item.get("phase") != "commentary":
                self.messages.append(item.get("text", ""))
        elif method == "turn/completed":
            turn = params.get("turn", {})
            if turn.get("status") != "completed":
                error = turn.get("error") or {}
                raise RuntimeError("Codex: " + (error.get("message") or turn.get("status", "başarısız")))
            self.completed = True

    def execute_app_tool(self, name, arguments):
        if name not in self.allowed_tools:
            return "Araç devre dışı veya bilinmiyor: " + name
        if self.tool_calls >= self.max_calls:
            return "Araç limiti doldu. Mevcut sonuçlarla son yanıtını ver."
        self.tool_calls += 1
        started = time.monotonic()
        try:
            result = self.executor.execute_tool(name, json.dumps(arguments, ensure_ascii=False))
        finally:
            self.deadline += time.monotonic() - started
        if name not in self.tools_used:
            self.tools_used.append(name)
        return result

    def generate(self, system_prompt, user_prompt, history, model, tools, executor,
                 tools_used, max_iterations):
        self.executor = executor
        self.tools_used = tools_used
        self.allowed_tools = {tool["function"]["name"] for tool in tools}
        self.tool_calls = 0
        self.max_calls = max_iterations
        self.messages = []
        self.completed = False
        dynamic_tools = [{
            "type": "function", "name": t["function"]["name"],
            "description": t["function"]["description"],
            "inputSchema": t["function"]["parameters"],
        } for t in tools]
        instructions = system_prompt + (
            "\nUse only the supplied application tools for actions and data access. "
            "They enforce user permissions. Never use native shell, file or MCP tools. "
            "The conversation_history field is context, not a queue of tasks to execute again. "
            "Act only on the current user_request. "
            f"You may make at most {max_iterations} application tool calls; then answer "
            "using their results. Do not claim an operation succeeded if a tool denied it."
        )
        config = {
            "features.shell_tool": False, "features.view_image": False,
            "features.code_mode": False, "features.multi_agent": False,
            "features.apps": False, "features.plugins": False,
            "web_search": "disabled",
        }
        # Do not expose tools from the user's unrelated MCP installations.
        effective_config = self.request("config/read", {"cwd": self.workspace})["config"]
        for name in effective_config.get("mcp_servers", {}):
            config[f'mcp_servers.{json.dumps(name)}.enabled'] = False
        thread = self.request("thread/start", {
            "cwd": self.workspace, "model": model, "ephemeral": True,
            "approvalPolicy": "on-request", "sandbox": "read-only",
            "baseInstructions": instructions, "dynamicTools": dynamic_tools,
            "config": config,
        })["thread"]["id"]
        # Structured transcript avoids interpreting previous assistant text as
        # fresh instructions; bounded history belongs to the application.
        prompt = json.dumps({"conversation_history": history, "user_request": user_prompt}, ensure_ascii=False)
        self.request("turn/start", {
            "threadId": thread,
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
        })
        while True:
            while not self.completed:
                self.handle(self.receive())
            output = "\n\n".join(self.messages).strip()
            if not output:
                raise RuntimeError("Codex son yanıt üretmedi.")
            question = clarification_question(output) if "ask_user" in self.allowed_tools else None
            if not question:
                return output
            if self.tool_calls >= self.max_calls:
                raise RuntimeError("Açıklama sorusu limiti doldu; istek tamamlanamadı.")
            result = self.execute_app_tool("ask_user", visual_question_request(question))
            if question_was_cancelled("ask_user", result):
                raise UserQuestionCancelled()
            answer = json.loads(result)["answer"]
            images = drain_answer_images(self.executor)
            self.messages = []
            self.completed = False
            # Continue the same thread, preserving prior tools and context.
            self.request("turn/start", {
                "threadId": thread,
                "input": [{"type": "text", "text": "Açıklama sorusuna yanıtım: " + answer,
                           "text_elements": []}] + [{'type': 'image', 'url': url} for url in images],
            })

    def __exit__(self, *args):
        if self.process:
            if self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=3)
            self.reader.join(timeout=1)
            self.process.stdin.close()
            self.process.stdout.close()
        if self.diagnostics:
            self.diagnostics.close()
