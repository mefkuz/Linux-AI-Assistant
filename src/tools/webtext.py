"""Soru-odaklı metin kırpma (sıfır token, saf Python).

fetch_web_page büyük sayfaları ham haliyle LLM'e verirse token yakar.
Bu modül sayfayı paragraflara bölüp sorudaki kelimelerle en çok
örtüşen bölümleri seçer — model sadece ilgili kısımları görür.

Kullanım:
    from src.tools.webtext import pick_relevant_sections
    short = pick_relevant_sections(page_text, "Minecraft Wesper mod", limit=4000)
"""

import re
import unicodedata

# Kısa/işlevsel kelimeler skora katılmaz (TR+EN stop-word çekirdeği).
_STOPWORDS = frozenset("""
ve veya ile bir bu şu o ne mi mu mü mı de da ki çok daha en için gibi kadar
the a an and or of to in on is are was were be as at by for from with that
this it its into
""".split())

_MIN_WORD_LEN = 3       # Skorlanan minimum kelime uzunluğu
_MAX_PARAGRAPHS = 12    # Seçilecek maksimum paragraf sayısı


def _normalize(text):
    """Küçük harf + aksan sadeleştirme (Türkçe dahil)."""
    text = (text or "").lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    return text.translate(str.maketrans("çğıöşü", "cgiosu"))


def _keywords(query):
    """Sorgudan skorlanabilir anahtar kelimeleri çıkarır."""
    words = re.findall(r"[a-z0-9çğıöşüâîû]+", _normalize(query))
    return {w for w in words
            if len(w) >= _MIN_WORD_LEN and w not in _STOPWORDS}


def pick_relevant_sections(text, query, limit=4000, max_paragraphs=_MAX_PARAGRAPHS):
    """Sayfa metninden soruyla ilgili bölümleri seçer, limite kırpar.

    text:  fetch_url_as_text çıktısı (düz metin).
    query: kullanıcının sorusu / aranan konu.
    limit: döndürülecek maksimum karakter.
    Döner: ilgili paragraflar (orijinal sırayla) veya bulunamazsa
    metnin başından `limit` karakter.
    """
    text = (text or "").strip()
    if not text:
        return ""
    limit = max(500, int(limit or 4000))

    # Paragraflara böl; tek satırlık menü artıkları (<40 karakter) atlanır.
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    paras = [p for p in paras if len(p) >= 40] or paras

    keys = _keywords(query)
    if not keys:
        return text[:limit]

    scored = []
    for idx, p in enumerate(paras):
        norm = _normalize(p)
        hits = sum(1 for k in keys if k in norm)
        if hits:
            # Uzun tekrar paragraflarını cezalandır: skor / log(uzunluk)
            import math
            scored.append((hits / math.log10(100 + len(p)), idx, p))

    if not scored:
        return text[:limit]

    scored.sort(key=lambda t: (-t[0], t[1]))
    chosen = sorted(scored[:max(1, max_paragraphs)], key=lambda t: t[1])

    out, total = [], 0
    for _, _, p in chosen:
        if total + len(p) + 2 > limit:
            break
        out.append(p)
        total += len(p) + 2
    if not out:
        # Tek paragraf bile limiti aşıyorsa başından kırp
        return chosen[0][2][:limit]
    return "\n\n".join(out)
