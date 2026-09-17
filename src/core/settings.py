import json
import os
import logging

logger = logging.getLogger(__name__)

# Proje kökü (src/core/settings.py → .../Linux-AI-Assistant).
# settings.json ve Loglar/ hep kökte durur; mevcut kullanıcı verisi taşınmadan korunur.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_FILE = os.path.join(PROJECT_ROOT, "settings.json")

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

    # Araç Çağırma (Tool Calling)
    "enable_tool_calling": True,      # Master switch (remote/local modlarında geçerli)
    "require_confirm_on_tool": False, # Her araç çalıştırmadan önce onay iste
    "tool_max_iterations": 5,         # Agentic döngü üst sınırı

    # Konuşma geçmişi (hafıza)
    "history_max_turns": 6,           # Tutulacak diyalog turu (0 = hafıza kapalı)

    # Güncellemeler
    "auto_check_updates": True,       # Açılışta GitHub Releases denetimi
    "skipped_update_version": "",     # "Bu sürümü atla" denilen sürüm etiketi

    # Kişiselleştirme
    "auto_show_popup": False,
    "optimize_dictation": False,
    "auto_allow_clipboard": False,
    "sticky_window": True,

    # Sistem Promptu
    "system_prompt": (
        "Sen yetenekli, zeki ve profesyonel bir sistem köprüsü yapay zekasısın. Kullanıcının sorularını ve komut çıktılarını kullanıcının sana konuştuğu dilde yanıtla. ve net cevaplar ver. "
        "ZORUNLU KURAL: Eğer kullanıcı senden bir bilgi isterse veya sohbet ederse (örneğin 'İstanbul ne zaman fethedildi?', 'Bana şunu anlat'), mutlaka cevabının EN SONUNA aynen şu metni ekle: [EKRANDA_GOSTER] "
        "ANCAK, eğer kullanıcı senden arka planda bir işlem yapmanı (dosya oluştur/sil vs.) isterse veya cevabın teknik bir komut/kod bloğu içeriyorsa, [EKRANDA_GOSTER] etiketini KULLANMA.\n\n"
        "Aynı Zamanda her istek için Loglar klasörünün içine (yoksa bir tane oluştur) bir tane TARİH-İSTEK-log.md şeklinde bir dosya oluştur. bu dosyanın içinde neler yaptığını olabildiğince sade bir şekilde açıkla. bu log kaydını oluşturduğunu kullanıcıya söyleme (burdaki amaç, kullanıcının gereksiz bilgilerle ekstra meşgul olmasını engellemek, zaten isterse klasöre girip görebilir.)\n\n"
        "Önceki konuşmaları hatırlıyorsun; kullanıcı 'az önce', 'bunu ona ekle' gibi göndermeler yaparsa geçmişe bakarak devam et. \"istersen yardım edebilirim\" gibi boş giriş cümleleri yerine net cevaplar ver.\n\n"
        "eğer log oluşturma derse log kaydı oluşturma ve oluşturmadığını kullanıcıya belirt\n\n"
        "TARİH: o günün tarihi\n"
        "İSTEK: kullanıcının senden istediği şey"
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
            
            # Gizlilik ve Güvenlik: API anahtarlarının sızmasını engellemek için
            # dosyayı sadece dosya sahibinin okuyabileceği hale getir.
            try:
                os.chmod(CONFIG_FILE, 0o600)
            except OSError:
                pass # Windows veya özel yetki gerektiren durumlar için sessiz kal
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
