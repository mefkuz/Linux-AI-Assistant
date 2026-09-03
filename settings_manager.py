import json
import os
import logging

logger = logging.getLogger(__name__)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "settings.json")

DEFAULT_SETTINGS = {
    # Genel
    "hotkey": "<ctrl>+<alt>+a",
    "language": "tr-TR",
    "app_language": "tr",
    "workspace_dir": "",           # Komutların çalıştırılacağı klasör (boş = home dizini)

    # Dinleme Overlay Ayarları
    "overlay_position": "Bottom-Right",
    "overlay_timeout_seconds": 5,
    "overlay_display_seconds": 3,
    "mic_sensitivity": 3000,
    "pause_threshold": 1.5,
    "phrase_time_limit": 30,

    # LLM Ayarları
    "llm_mode": "local",
    "local_api_url": "http://localhost:8080/v1/chat/completions",
    "local_model": "local-model",
    "remote_api_key": "",
    "remote_api_url": "https://api.openai.com/v1/chat/completions",
    "remote_model": "gpt-4",
    # CLI modu (Registry tabanlı)
    "llm_cli_tool_key": "agy (Antigravity)",  # KNOWN_CLI_TOOLS'daki anahtar
    "llm_cli_model": "",                       # Seçilen model (yoksa boş)
    "llm_cli_extra_args": "",                  # Opsiyonel ek argümanlar

    # Kullanıcı tanımlı CLI yönlendirme araçları (router)
    "custom_cli_tools": ["antigravity", "codex", "agy"],

    # Güvenlik
    "require_confirm_on_write": True,
    "allow_sudo": False,

    # Kişiselleştirme
    "auto_show_popup": False,
    "optimize_dictation": False,
    "auto_allow_clipboard": False,

    # Sistem Promptu
    "system_prompt": (
        "Sen yetenekli, zeki ve profesyonel bir sistem köprüsü yapay zekasısın. Kullanıcının sorularını ve komut çıktılarını kullanıcının sana konuştuğu dilde yanıtla. Kısa, öz ve net cevaplar ver. "
        "ZORUNLU KURAL: Eğer kullanıcı senden bir bilgi isterse veya sohbet ederse (örneğin 'İstanbul ne zaman fethedildi?', 'Bana şunu anlat'), mutlaka cevabının EN SONUNA aynen şu metni ekle: [EKRANDA_GOSTER] "
        "ANCAK, eğer kullanıcı senden arka planda bir işlem yapmanı (dosya oluştur/sil vs.) isterse veya cevabın teknik bir komut/kod bloğu içeriyorsa, [EKRANDA_GOSTER] etiketini KESİNLİKLE KULLANMA."
    ),
}

class SettingsManager:
    def __init__(self):
        self.settings = DEFAULT_SETTINGS.copy()
        self._load_settings()

    def _load_settings(self):
        """Ayarları disk üzerinden okur. Eksik anahtarları varsayılanlarla tamamlar."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    for key in DEFAULT_SETTINGS:
                        if key in loaded:
                            self.settings[key] = loaded[key]
                logger.info("Ayarlar başarıyla yüklendi.")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Ayarlar okunamadı, varsayılanlar kullanılacak: {e}")
        else:
            self.save_settings()

    def save_settings(self):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4, ensure_ascii=False)
        except IOError as e:
            logger.error(f"Ayarlar kaydedilemedi: {e}")

    def get(self, key, fallback=None):
        return self.settings.get(key, fallback if fallback is not None else DEFAULT_SETTINGS.get(key))

    def set(self, key, value):
        if key in DEFAULT_SETTINGS:
            self.settings[key] = value
            self.save_settings()
        else:
            logger.warning(f"Bilinmeyen ayar anahtarı yok sayıldı: {key}")
