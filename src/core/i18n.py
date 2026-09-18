"""GUI-dışı katmanlar için merkezi mini çeviri (TR/EN).

GUI (src/gui/app.py) kendi TRANSLATIONS sözlüğünü kullanmaya devam eder;
bu modül tools/llm/core/context katmanlarının KULLANICIYA GÖSTERİLEN
metinlerini (onay diyalogları, hata/fallback mesajları, bağlam etiketleri)
çevirir. LLM'e giden prompt/şema metinleri bilerek çevrilmez — model
bunları anlayıp kullanıcının dilinde yanıt üretir.

Varsayılan TR'dir; GUI açılışta ve dil değişiminde set_language() çağırır.
"""

_lang = "tr"

TRANSLATIONS = {
    # Onay diyalogları (araç çalıştırma)
    "Komut çalıştırılsın mı?": "Run this command?",
    "Şu komut çalıştırılacak:": "The following command will run:",
    "Dosya yazılsın mı?": "Write this file?",
    "Şu dosyaya yazılacak ({size} karakter):": "Will be written to this file ({size} characters):",
    "Dosyaya eklensin mi?": "Append to this file?",
    "Şu dosyanın sonuna eklenecek ({size} karakter):": "Will be appended to this file ({size} characters):",
    "Tarayıcıda işlem yapılsın mı?": "Allow browser action?",
    "Yapay zeka {what} istiyor.": "The AI wants to {what}.",
    "açık sekmeyi kapatmak": "close the open tab",
    "sayfayı aşağı kaydırmak": "scroll the page down",
    "sayfayı yukarı kaydırmak": "scroll the page up",
    "forma metin yazmak": "type text into the form",
    "yeni sekme açmak": "open a new tab",
    "'{action}' işlemini yapmak": "perform '{action}'",
    "Yazılacak metin: ": "Text to type: ",
    "Adres: ": "URL: ",
    "Ekran okunsun mu?": "Allow screen reading?",
    "Yapay zeka ekranının görüntüsünü okumak istiyor.": "The AI wants to read your screen.",
    "Pano okunsun mu?": "Allow clipboard reading?",
    "Yapay zeka panondaki metni okumak istiyor.": "The AI wants to read your clipboard text.",
    "Onay": "Confirm",
    "Araç: ": "Tool: ",
    "Argüman: ": "Arguments: ",
    "GÜVENLİK": "SECURITY",
    "Detay: ": "Details: ",
    "Onaylıyor musunuz? (y/n): ": "Confirm? (y/n): ",
    "Geçersiz komut sözdizimi: {e}": "Invalid command syntax: {e}",
    "Kullanıcı bu okuma işlemine izin vermedi. Ekran/pano içeriğini görmeden, genel bilgiyle cevapla.": (
        "The user denied this read. Answer from general knowledge, "
        "without screen/clipboard content."
    ),
    "Kullanıcı bu işlemi reddetti. İşlem yapılmadı.": "The user declined. Nothing was done.",
    # LLM istemcisi (kullanıcının gördüğü hata/fallback'lar)
    "\n... ({n} karakter kesildi)": "\n... ({n} characters truncated)",
    "(Yapay zeka yanıt üretemedi veya sadece düşünce bloğu gönderdi. Terminaldeki [DEBUG] logunu kontrol edin.)": (
        "(The AI produced no answer or only a reasoning block. "
        "Check the [DEBUG] log in the terminal.)"
    ),
    "[LLM Hatası ({mode})]: {e}": "[LLM Error ({mode})]: {e}",
    "İşlem tamamlandı (kullanılan araçlar: {tools}).": "Done (tools used: {tools}).",
    "Detay için Loglar klasörüne bakabilirsiniz.": "See the Loglar folder for details.",
    "CLI modu için ayarlar yüklenmedi.": "Settings not loaded for CLI mode.",
    "Uzak mod için ayarlar yüklenmedi.": "Settings not loaded for remote mode.",
    "REMOTE_LLM_API_KEY boş. Ayarlardan doldurun.": "REMOTE_LLM_API_KEY is empty. Fill it in Settings.",
    "'{cmd}' aracından çıktı alınamadı. Aracın doğru kurulduğunu ve PATH'te olduğunu kontrol edin.": (
        "No output from '{cmd}'. Check that the tool is installed and on PATH."
    ),
    # Router / CLI (doğrudan kullanıcıya dönenler)
    "Boş girdi alındı.": "Empty input.",
    "KOMUT ÇIKTISI": "COMMAND OUTPUT",
    "YAPAY ZEKA": "AI",
    "(çıktı yok)": "(no output)",
    "Yapay zeka yanıt veremedi ({e}).": "The AI couldn't respond ({e}).",
    "İpucu: Ayarlar → Yapay Zeka sekmesinden LLM modunu yapılandırın.": (
        "Hint: configure the LLM mode under Settings → AI."
    ),
    "Boş komut algılandı.": "Empty command.",
    "İşlem kullanıcı tarafından iptal edildi.": "Cancelled by user.",
    "Komut bulunamadı: '{cmd}'": "Command not found: '{cmd}'",
    # Bağlam etiketleri (overlay butonları)
    "YouTube Videosunu": "YouTube video",
    "Çalan Şarkıyı": "currently playing song",
    "Spotify Şarkısı": "Spotify song",
    "Terminali": "terminal",
    "Açık Klasörü": "open folder",
    "Üzerinde Çalışılan Belgeyi": "document in progress",
    "Açık Sekmeyi": "open tab",
    "Açık Pencereyi": "open window",
    # Terminal arayüzü (main.py)
    "Mod: Bağlam Duyarlı CLI / LLM Arabirimi": "Mode: Context-aware CLI / LLM interface",
    "Çıkmak için 'exit' veya 'quit' yazın.": "Type 'exit' or 'quit' to leave.",
    "[Kullanıcı] >> ": "[You] >> ",
    "Oturum sonlandırılıyor...": "Closing session...",
    "[Kritik Hata]:": "[Critical Error]:",
    # Güncelleyici
    "Bu klasör bir Git deposu değil. Güncellemek için yeni sürümü manuel indirin.": (
        "This folder is not a Git checkout. Download the new release manually to update."
    ),
    "Git bulunamadı (PATH'te yok).": "Git not found (not on PATH).",
    "Zaten güncel.": "Already up to date.",
    "Bağımlılıklar güncellenemedi (devam ediliyor): {e}": (
        "Dependencies couldn't be refreshed (continuing): {e}"
    ),
    "Güncelleme tamamlandı.": "Update complete.",
    "Git güncellemesi başarısız: {e}": "Git update failed: {e}",
    "GitHub'a ulaşılamadı: {e}": "Couldn't reach GitHub: {e}",
    # Araç sonuçları (LLM'e dönen + Loglar'a yazılan metinler)
    "Araç argümanı JSON olarak çözümlenemedi: {e}": "Couldn't parse tool arguments as JSON: {e}",
    "Bilinmeyen araç: '{name}'.": "Unknown tool: '{name}'.",
    "Araç çalıştırılırken hata ({name}): {e}": "Tool error ({name}): {e}",
    "\n... (çıktı çok uzun olduğu için kesildi)": "\n... (output too long, truncated)",
    "\n... (hata mesajı çok uzun, {n} karakter kesildi)": "\n... (error too long, {n} characters truncated)",
    "Hata: 'command' parametresi boş.": "Error: 'command' is empty.",
    "\nNot: Komut kullanıcı tarafından iptal edildi.": "\nNote: command cancelled by user.",
    "Hata: 'path' parametresi boş.": "Error: 'path' is empty.",
    "Dosya bulunamadı: '{path}'": "File not found: '{path}'",
    "Dosya okunamadı: {e}": "Couldn't read file: {e}",
    "\n... (dosya çok uzun, kesildi)": "\n... (file too long, truncated)",
    "Klasör oluşturulamadı: {e}": "Couldn't create folder: {e}",
    "Dosya yazılamadı: {e}": "Couldn't write file: {e}",
    "Dosya yazıldı: {real} ({n} karakter)": "Wrote file: {real} ({n} characters)",
    "Dosyaya eklenemedi: {e}": "Couldn't append to file: {e}",
    "Dosyaya eklendi: {real} (+{n} karakter)": "Appended to file: {real} (+{n} characters)",
    "Dosya oluşturulup yazıldı: {real} (+{n} karakter)": "Created and wrote file: {real} (+{n} characters)",
    "Klasör bulunamadı: '{path}'": "Folder not found: '{path}'",
    "Klasör listelenemedi: {e}": "Couldn't list folder: {e}",
    "... (+{n} öğe daha)": "... (+{n} more items)",
    "[{real}] ({n} öğe):": "[{real}] ({n} items):",
    "(Pano boş veya okunamadı)": "(Clipboard empty or unreadable)",
    "[Panodaki Metin]:": "[Clipboard Text]:",
    "(Ekrandan metin okunamadı)": "(Couldn't read screen text)",
    "[Ekrandaki Metin (OCR)]:": "[On-Screen Text (OCR)]:",
    "Bağlam yardımcısı yüklenemedi: {e}": "Couldn't load context helper: {e}",
    "(Aktif bağlam bulunamadı)": "(No active context found)",
    "Hata: 'action' parametresi boş.": "Error: 'action' is empty.",
    "Tarayıcı bağlantısı yok (eklenti sunucusu çalışmıyor). Kullanıcıya tarayıcı eklentisini kurmasını hatırlat.": "No browser connection (extension server not running). Remind the user to install the browser extension.",
    "Tarayıcı komutu gönderilemedi: {e}": "Couldn't send browser command: {e}",
    "Tarayıcı komutu gönderildi: {action}": "Browser command sent: {action}",
    "Güvenlik: '{path}' çalışma alanı dışına taşıyor (workspace: {ws}).": "Security: '{path}' escapes the workspace ({ws}).",
    # Workspace kaçış uyarısı (shell + dosya araçları)
    "Yapay zeka çalışma alanının dışına çıkmak istiyor!": "The AI wants to leave the workspace!",
    "Bu işlem çalışma alanı dışındaki dosyalara erişiyor:": "This operation reaches outside the workspace:",
    "İzin verilsin mi? (sadece bu seferlik)": "Allow it? (this time only)",
    "Komut:": "Command:",
    "Araç:": "Tool:",
    "Çalışma alanı:": "Workspace:",
    "Dışarı taşan yollar:": "Paths outside the workspace:",
    "Kullanıcı çalışma alanı dışına erişime izin vermedi. Sadece çalışma alanı içindeki dosyalarla devam et.": (
        "The user denied access outside the workspace. Continue using only files inside the workspace."
    ),
    # İstek günlüğü (Loglar/YYYY-MM-DD-*-log.md — PC tarafında yazılır)
    "İşlem Günlüğü": "Activity Log",
    "Tarih:": "Date:",
    "İstek:": "Request:",
    "Yol:": "Route:",
    "Kullanılan araçlar:": "Tools used:",
    "(araç kullanılmadı)": "(no tools used)",
    "Yanıt Özeti": "Response Summary",
    "\n... (yanıt uzun olduğu için kesildi, toplam {n} karakter)": "\n... (response truncated, {n} characters total)",
}


def set_language(lang):
    """Aktif dili ayarlar ('tr' veya 'en', başka değer TR sayılır)."""
    global _lang
    _lang = "en" if lang == "en" else "tr"


def get_language():
    return _lang


def tr(text):
    """TR metni aktif dile çevirir (TR ise aynen döner)."""
    if _lang == "en":
        return TRANSLATIONS.get(text, text)
    return text
