"""Her istek için otomatik istek günlüğü (PC-tabanlı, sıfır token).

Eskiden bu iş system prompt talimatıyla AI'ya yaptırılıyordu: AI her istekte
`date` + `mkdir` + `write_file` olmak üzere 3 ekstra tool turu harcıyordu.
Bu modül aynı işi Python tarafında yapar — LLM'e hiç istek gitmez.

Kullanım:
    from src.core.request_log import write_request_log
    write_request_log(user_input, response, tools_used=[...], mode="remote")

Dosya adı: Loglar/YYYY-MM-DD-<istek-ozeti>-log.md
"""

import logging
import os
import re
import time

from src.core.i18n import tr

logger = logging.getLogger(__name__)

MAX_RESPONSE_CHARS = 2000  # Loga düşen yanıt özeti üst sınırı
MAX_SLUG_WORDS = 6         # Dosya adındaki istek özeti kelime sınırı


def _slugify(text, max_words=MAX_SLUG_WORDS):
    """İstek metnini dosya-adı güvenli kısa özete çevirir."""
    import unicodedata
    text = (text or "").strip().lower()
    # "İ" gibi harfleri ASCII'ye indir (NFKD + birleşen işaretleri at)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    # Kalan Türkçe karakterler için ek güvence
    tr_map = str.maketrans("çğıöşü", "cgiosu")
    text = text.translate(tr_map)
    # Harf/rakam dışını tire yap, art arda tireleri birleştir
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    words = [w for w in text.split("-") if w][:max_words]
    return "-".join(words) or "istek"


def write_request_log(user_input, response, tools_used=None, mode="",
                      llm_mode="", log_dir=None, project_root=None):
    """İstek günlüğünü diske yazar. Hata durumunda sessizce atlar (asla patlamaz).

    user_input: ham kullanıcı sorusu/komutu
    response:   kullanıcıya gösterilen final yanıt
    tools_used: kullanılan araç adları listesi (örn. ["read_file", ...])
    mode:       "cli" veya "llm" (hangi yoldan yanıtlandığı)
    llm_mode:   llm ayarı ("local"/"remote"/"cli")
    log_dir / project_root: testler için override edilebilir
    Döner: yazılan dosyanın yolu veya None (hata/atlama durumunda).
    """
    try:
        if project_root is None:
            from src.core.settings import PROJECT_ROOT as _ROOT
            project_root = _ROOT
        if log_dir is None:
            log_dir = os.path.join(project_root, "Loglar")
        os.makedirs(log_dir, exist_ok=True)

        day = time.strftime("%Y-%m-%d")
        clock = time.strftime("%H:%M:%S")
        slug = _slugify(user_input)

        # Aynı saniyede çakışmayı önle: dosya varsa sayaç ekle
        base = os.path.join(log_dir, f"{day}-{slug}-log.md")
        path = base
        counter = 2
        while os.path.exists(path):
            path = os.path.join(log_dir, f"{day}-{slug}-{counter}-log.md")
            counter += 1

        tools_used = tools_used or []
        tools_line = ", ".join(f"`{t}`" for t in tools_used) if tools_used else tr("(araç kullanılmadı)")

        resp = (response or "").strip()
        truncated = len(resp) > MAX_RESPONSE_CHARS
        if truncated:
            resp = resp[:MAX_RESPONSE_CHARS]

        lines = [
            f"# {tr('İşlem Günlüğü')}",
            f"- **{tr('Tarih:')}** {day} {clock}",
            f"- **{tr('İstek:')}** {user_input.strip()}",
            f"- **{tr('Yol:')}** {mode}" + (f" ({llm_mode})" if llm_mode else ""),
            f"- **{tr('Kullanılan araçlar:')}** {tools_line}",
            "",
            f"## {tr('Yanıt Özeti')}",
            resp,
        ]
        if truncated:
            lines.append(tr("\n... (yanıt uzun olduğu için kesildi, toplam {n} karakter)").format(n=len(response.strip())))

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        logger.info(f"İstek günlüğü yazıldı: {path}")
        return path
    except OSError as e:
        logger.warning(f"İstek günlüğü yazılamadı: {e}")
        return None
