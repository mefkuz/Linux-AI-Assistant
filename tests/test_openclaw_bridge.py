"""OpenClaw Köprüsü Birim Testleri ve Kalite Güvencesi"""
import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.tools.openclaw_bridge import ask_openclaw_gateway, _extract_response_text, MAX_OPENCLAW_RESPONSE_CHARS
from src.tools.executor import ToolExecutor, get_openai_tools


class FakeSettings:
    def __init__(self, data):
        self.data = data

    def get(self, key, fallback=None):
        return self.data.get(key, fallback)


class TestOpenClawBridge(unittest.TestCase):

    def test_empty_prompt(self):
        res = ask_openclaw_gateway("", "https://openclaw.mefkuz.com")
        self.assertFalse(res["ok"])
        self.assertIn("Boş", res["error"])

    def test_invalid_endpoint(self):
        res = ask_openclaw_gateway("Test soru", "ftp://openclaw.mefkuz.com")
        self.assertFalse(res["ok"])
        self.assertIn("http", res["error"])

    def test_empty_endpoint(self):
        res = ask_openclaw_gateway("Test soru", "")
        self.assertFalse(res["ok"])
        self.assertIn("belirtilmemiş", res["error"])

    def test_extract_response_openai_format(self):
        payload = {
            "choices": [
                {"message": {"role": "assistant", "content": "OpenAI formatında yanıt"}}
            ]
        }
        extracted = _extract_response_text(payload, "fallback")
        self.assertEqual(extracted, "OpenAI formatında yanıt")

    def test_extract_response_agent_format(self):
        payload = {"reply": "Ajan yanıtı"}
        self.assertEqual(_extract_response_text(payload, "fallback"), "Ajan yanıtı")

        payload2 = {"output": "İşlem sonucu"}
        self.assertEqual(_extract_response_text(payload2, "fallback"), "İşlem sonucu")

    @patch("urllib.request.urlopen")
    def test_successful_response_json(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'{"response": "Merhaba, sunucu durumu iyi."}'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = ask_openclaw_gateway("Sunucu durumu nasıl?", "https://openclaw.mefkuz.com", token="secret")
        self.assertTrue(res["ok"])
        self.assertEqual(res["response"], "Merhaba, sunucu durumu iyi.")
        self.assertIsNone(res["error"])

    @patch("urllib.request.urlopen")
    def test_successful_response_truncation_for_tokens(self, mock_urlopen):
        long_content = "x" * 6000
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = f'{{"response": "{long_content}"}}'.encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = ask_openclaw_gateway("Uzun yanıt iste", "https://openclaw.mefkuz.com")
        self.assertTrue(res["ok"])
        self.assertIn("kesildi", res["response"])
        self.assertTrue(len(res["response"]) <= MAX_OPENCLAW_RESPONSE_CHARS + 100)

    @patch("urllib.request.urlopen")
    def test_successful_response_text(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = b'D\xc3\xbcz metin yan\xc4\xb1t.'
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        res = ask_openclaw_gateway("Test", "https://openclaw.mefkuz.com")
        self.assertTrue(res["ok"])
        self.assertEqual(res["response"], "Düz metin yanıt.")

    def test_executor_disabled_by_default(self):
        settings = FakeSettings({"openclaw_enabled": False})
        executor = ToolExecutor(settings=settings)
        out = executor.execute_tool("ask_openclaw", {"task": "Sunucuya bak"})
        self.assertIn("kapalı", out)

    @patch("src.tools.openclaw_bridge.ask_openclaw_gateway")
    def test_executor_enabled_runs_bridge(self, mock_bridge):
        mock_bridge.return_value = {"ok": True, "response": "İşlem tamamlandı."}
        settings = FakeSettings({
            "openclaw_enabled": True,
            "openclaw_endpoint": "https://openclaw.mefkuz.com",
            "openclaw_token": "secret-token",
            "openclaw_timeout": 30
        })
        executor = ToolExecutor(settings=settings)
        out = executor.execute_tool("ask_openclaw", {"task": "Görevi yap"})
        self.assertIn("İşlem tamamlandı", out)
        self.assertIn("[OpenClaw Yanıtı]", out)
        mock_bridge.assert_called_once_with(
            prompt="Görevi yap",
            endpoint="https://openclaw.mefkuz.com",
            token="secret-token",
            timeout=30
        )

    def test_schema_exists(self):
        tools = get_openai_tools()
        names = [t["function"]["name"] for t in tools]
        self.assertIn("ask_openclaw", names)


if __name__ == "__main__":
    unittest.main()
