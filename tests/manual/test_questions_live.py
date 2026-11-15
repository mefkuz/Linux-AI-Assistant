"""Opt-in real API and Codex clarification tests with deterministic user replies.

No GUI is opened. Only a temporary workspace is changed. Uses the saved remote
API credentials and the existing Codex login; may consume provider usage.
"""
import argparse
import json
import subprocess
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from src.core.settings import SettingsManager
from src.llm.client import LLMClient


def verify(provider):
    with tempfile.TemporaryDirectory(prefix="linux-ai-question-live-") as workspace:
        settings = SettingsManager().settings.copy()
        settings.update(workspace_dir=workspace, enable_tool_calling=True,
                        require_confirm_on_tool=False, require_confirm_on_write=True,
                        codex_timeout=180, history_max_turns=4)
        if provider == "codex":
            settings.update(llm_mode="cli", llm_cli_tool_key="codex (OpenAI)",
                            llm_cli_model="", llm_cli_extra_args="")
        else:
            settings["llm_mode"] = "remote"
        questions = []
        filename = "kullanici-istanbul-73921.txt"
        def answer(request):
            questions.append(request)
            return filename
        client = LLMClient(settings, question_callback=answer)
        reply = client.generate_response(
            "İstenen işi uygulama araçlarıyla gerçekleştir. Gerekli bilgi eksikse ask_user kullan. "
            "Kullanıcının yanıtını öğrenmeden ona bağlı bir dosya işlemi yapma.",
            'Çalışma klasöründe bir metin dosyası oluştur ve içine tam olarak QUESTION_E2E_73921 yaz. '
            'Dosyanın adını önce bana sorup öğren; kendin isim seçme.')
        print(provider, "ANSWER", reply)
        assert client.last_error is None, client.last_error
        assert len(questions) == 1, questions
        assert Path(workspace, filename).read_text() == "QUESTION_E2E_73921"
        assert {"ask_user", "write_file"} <= set(client.last_tools_used), client.last_tools_used
        assert filename in client.history[0]["content"]

        def no_more_questions(request):
            raise AssertionError("Already answered information must be remembered")
        client.question_callback = no_more_questions
        followup = client.generate_response("Kullanıcının verdiği bilgileri konuşma geçmişinden hatırla.",
                                           "Az önce benim seçtiğim dosya adını yaz. Yalnızca dosya adı.")
        print(provider, "MEMORY", followup)
        assert filename in followup, followup
        assert "ask_user" not in client.last_tools_used
        assert not ({"write_file", "append_file", "run_shell_command", "browser_action", "ask_openclaw"}
                    & set(client.last_tools_used)), client.last_tools_used

        protected = Path(workspace, "protected.txt")
        protected.write_text("KEEP")
        before = {p.name: p.read_bytes() for p in Path(workspace).iterdir()}
        cancellations = []
        def cancel(request):
            cancellations.append(request)
            return None
        client.question_callback = cancel
        reply = client.generate_response(
            "Gerekli bilgi eksikse ask_user ile sor; yanıt olmadan dosyaya dokunma.",
            "Şimdi protected.txt dosyasının içeriğini değiştireceğiz. Yeni içeriği önce bana sor. "
            "Yanıtımı almadan hiçbir dosya oluşturma veya değiştirme.")
        print(provider, "CANCEL", reply)
        assert cancellations, "Model did not ask"
        assert "durduruldu" in reply, reply
        assert client.last_error is None, client.last_error
        after = {p.name: p.read_bytes() for p in Path(workspace).iterdir()}
        assert before == after, "Cancellation changed workspace files"

        root = Path(__file__).resolve().parents[2]
        config_file = root / "settings.json"
        saved_config = config_file.read_bytes()
        terminal = subprocess.run(
            [sys.executable, str(root / "main.py"), "--ai", provider, "--workspace", workspace],
            input=("Tercih ettiğim rengi bilmiyorsun. Önce ask_user ile bana sor, "
                   "cevabımı alınca yalnızca verdiğim rengi yaz. Başka işlem yapma.\n"
                   "MAVİ_73921\nquit\n"),
            capture_output=True, text=True, timeout=210, cwd=root,
        )
        assert terminal.returncode == 0, terminal.stderr
        assert "MAVİ_73921" in terminal.stdout, terminal.stdout
        assert "[LLM Hatası" not in terminal.stdout, terminal.stdout
        assert config_file.read_bytes() == saved_config
        print(json.dumps({"provider": provider, "answer_drives_file": "PASS",
                          "clarification_memory": "PASS", "cancel_stops_actions": "PASS",
                          "terminal_question_answer": "PASS"}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=["codex", "remote", "both"], default="both")
    choice = parser.parse_args().provider
    for name in (["codex", "remote"] if choice == "both" else [choice]):
        verify(name)
