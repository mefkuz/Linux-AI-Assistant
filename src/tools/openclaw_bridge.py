"""
OpenClaw Tek Yönlü İstemci Köprüsü (Outbound-Only Air-Gap Bridge)

Güvenlik İlkeleri:
1. Dinleyen port / soket / webhook YOKTUR (Inbound port: 0).
2. Tüm iletişimi Linux AI Assistant başlatır (Outbound HTTP/HTTPS).
3. Gelen yanıt yalnızca saf metin/markdown olarak ele alınır; yerel olarak asla kod veya komut çalıştırmaz.
4. Token-frugal mimari: Dönen metin aşırı uzunsa (~4000 karakter) kontrollü olarak kırpılır.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# Token tasarrufu tavanı (~1000 token civarı)
MAX_OPENCLAW_RESPONSE_CHARS = 4000

# Cloudflare WAF / bot korumasını tetiklememek için standart istemci başlığı
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _extract_response_text(data: Any, raw_fallback: str) -> str:
    """Farklı API formatlarından (OpenAI compatible, OpenClaw Agent, Webhook) metni ayıklar."""
    if isinstance(data, dict):
        # 1. Standart OpenAI / v1 formatı: choices[0].message.content
        choices = data.get("choices")
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                msg = first_choice.get("message")
                if isinstance(msg, dict) and msg.get("content"):
                    return str(msg.get("content")).strip()
                if first_choice.get("text"):
                    return str(first_choice.get("text")).strip()

        # 2. OpenClaw / Agentic API anahtarları
        for key in ("response", "reply", "text", "content", "result", "output"):
            val = data.get(key)
            if val is not None and str(val).strip():
                return str(val).strip()

        # 3. İçiçe mesaj objesi
        if "message" in data and isinstance(data["message"], str):
            return data["message"].strip()

    return raw_fallback.strip()


def ask_openclaw_gateway(
    prompt: str,
    endpoint: str,
    token: str = "",
    agent_id: str = "main",
    timeout: int = 60,
    session_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    OpenClaw Gateway veya OpenAI-uyumlu uç noktaya tek yönlü prompt iletir.
    
    Dönüş formatı:
    {
        "ok": bool,
        "response": str,
        "error": Optional[str]
    }
    """
    if not prompt or not prompt.strip():
        return {"ok": False, "response": "", "error": "Boş soru/görev gönderilemez."}

    endpoint = endpoint.strip().rstrip("/")
    if not endpoint:
        return {"ok": False, "response": "", "error": "OpenClaw sunucu adresi (endpoint) belirtilmemiş."}

    if not (endpoint.startswith("http://") or endpoint.startswith("https://")):
        return {"ok": False, "response": "", "error": "Endpoint http:// veya https:// ile başlamalıdır."}

    # URL Çözümleme:
    # Kullanıcı yalnızca kök adres girdiyse (örn: https://openclaw.mefkuz.com),
    # OpenClaw'ın standart OpenAI-uyumlu sohbet endpoint'i olan /v1/chat/completions kullanılır.
    if "/v1/" in endpoint or "/api/" in endpoint or "/webhook" in endpoint:
        url = endpoint
    else:
        url = f"{endpoint}/v1/chat/completions"

    # Açık ve standart kimlik başlığı ekleyerek OpenClaw tarafının kimle konuştuğunu bilmesini sağla
    formatted_prompt = f"[Linux PC Assistant (User's Personal Desktop AI)]: {prompt.strip()}"

    payload = {
        "model": "openclaw",
        "messages": [
            {"role": "user", "content": formatted_prompt}
        ],
        "stream": False
    }

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": BROWSER_USER_AGENT
    }
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    if session_key and session_key.strip():
        headers["x-openclaw-session-key"] = session_key.strip()

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body_bytes = resp.read()
            body_str = body_bytes.decode("utf-8", errors="replace")
            
            try:
                res_json = json.loads(body_str)
                reply = _extract_response_text(res_json, raw_fallback=body_str)
            except json.JSONDecodeError:
                reply = body_str.strip()

            # Token tasarrufu kontrolü
            if len(reply) > MAX_OPENCLAW_RESPONSE_CHARS:
                reply = reply[:MAX_OPENCLAW_RESPONSE_CHARS] + "\n... (OpenClaw yanıtı uzun olduğu için kesildi)"

            return {"ok": True, "response": reply, "error": None}

    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return {
            "ok": False,
            "response": "",
            "error": f"Sunucu HTTP hatası ({e.code}): {err_body if err_body else e.reason}"
        }
    except urllib.error.URLError as e:
        return {
            "ok": False,
            "response": "",
            "error": f"Bağlantı kurulamadı: {e.reason}"
        }
    except TimeoutError:
        return {
            "ok": False,
            "response": "",
            "error": f"İstek zaman aşımına uğradı ({timeout}s)."
        }
    except Exception as e:
        return {
            "ok": False,
            "response": "",
            "error": f"Beklenmeyen hata: {str(e)}"
        }
