"""
OpenClaw Tek Yönlü İstemci Köprüsü (Outbound-Only Air-Gap Bridge)

Güvenlik ilkeleri:
1. Dinleyen port / soket / webhook YOKTUR (Inbound port: 0).
2. Tüm iletişimi Linux AI Assistant başlatır (Outbound HTTP/HTTPS).
3. Gelen yanıt yalnızca saf metin/markdown olarak ele alınır, yerel olarak asla kod veya komut çalıştırmaz.
"""

import json
import logging
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


def ask_openclaw_gateway(
    prompt: str,
    endpoint: str,
    token: str = "",
    agent_id: str = "main",
    timeout: int = 60,
    session_key: Optional[str] = None
) -> Dict[str, Any]:
    """
    OpenClaw Gateway HTTP endpoint'ine tek yönlü prompt iletir.
    
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

    # OpenClaw webhook/chat API yapısı
    # /api/agent/run veya /v1/chat/completions veya gateway prompt endpoint
    url = f"{endpoint}/api/agent/{agent_id}/run" if "/api/" not in endpoint else endpoint

    payload = {
        "message": prompt.strip(),
        "stream": False
    }
    if session_key:
        payload["sessionKey"] = session_key

    data = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "Linux-AI-Assistant/1.0 (Outbound-Client)"
    }
    if token and token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            body_bytes = resp.read()
            body_str = body_bytes.decode("utf-8", errors="replace")
            
            try:
                res_json = json.loads(body_str)
                # Yanıt ayıklama
                if isinstance(res_json, dict):
                    # Yaygın anahtarlar: response, reply, text, content, result
                    reply = (
                        res_json.get("response") or
                        res_json.get("reply") or
                        res_json.get("text") or
                        res_json.get("content") or
                        res_json.get("result") or
                        body_str
                    )
                    return {"ok": True, "response": str(reply).strip(), "error": None}
                else:
                    return {"ok": True, "response": body_str.strip(), "error": None}
            except json.JSONDecodeError:
                return {"ok": True, "response": body_str.strip(), "error": None}

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
