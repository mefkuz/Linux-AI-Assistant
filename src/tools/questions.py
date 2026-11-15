"""Shared clarification contract for HTTP, Codex, GUI and terminal clients."""
import json
import re
import unicodedata
from src.core.i18n import tr


class UserQuestionCancelled(Exception):
    """Stop this request when the user declines to supply required information."""


def clarification_question(text):
    """Recognize a direct request for missing information, not any '?' in an answer.

    Models should call ask_user themselves. This conservative fallback covers
    short plain-text clarification replies when they ignore that instruction.
    Explanations, quoted questions, code and optional follow-ups stay responses.
    """
    clean = re.sub(r"\[\s*EKRANDA[_ ]G[OÖ]STER\s*\]", "", text or "", flags=re.I).strip()
    clean = re.sub(r"^\s*<think>.*?</think>", "", clean, flags=re.S).strip()
    if not clean or len(clean) > 2000 or "?" not in clean or "```" in clean or clean.startswith(('>', '"', '“')):
        return None
    folded = ''.join(c for c in unicodedata.normalize('NFKD', clean.casefold())
                     if not unicodedata.combining(c)).replace('ı', 'i')
    folded = re.sub(r"^(?:elbette|tabii|sure|certainly)[,.!\s]+", "", folded)
    if folded.startswith(("what else", "anything else", "would you like more", "would you like me to explain")):
        return None
    starts_as_question = re.match(
        r"(?:neyi\b|ne\b|hangi\w*\b|nereye\b|kime\b|tam olarak\b|"
        r"bunu yapabilmem|bu islemi yapabilmem|anlayabilmem|netlestir|"
        r"what\b|which\b|where\b|when\b|could you\b|can you clarify\b|"
        r"do you mean\b|would you prefer\b|i need\b)", folded)
    missing_information = re.search(
        r"kastet|kasted|belirt|netlestir|bahsed|tercih|istiyors|isters|secm|secer|"
        r"clarif|specif|would you like|do you mean|should i|prefer|need.+(?:information|detail)", folded)
    return clean if starts_as_question and missing_information else None


def validate_question(arguments):
    question = arguments.get("question")
    options = arguments.get("options", [])
    if not isinstance(question, str) or not question.strip() or len(question) > 2000:
        raise ValueError("question must be a non-empty string of at most 2000 characters")
    if not isinstance(options, list) or len(options) > 6:
        raise ValueError("options must be a list of at most 6 strings")
    if any(not isinstance(option, str) or not option.strip() or len(option) > 200 for option in options):
        raise ValueError("each option must be a non-empty string of at most 200 characters")
    return {"question": question.strip(), "options": list(dict.fromkeys(o.strip() for o in options))}


def ask_in_terminal(request):
    print("\n" + request["question"], flush=True)
    for index, option in enumerate(request["options"], 1):
        print(f"  {index}. {option}", flush=True)
    try:
        while True:
            answer = input(tr("Yanıt (seçenek numarası veya metin; /cancel = iptal): ")).strip()
            if answer.lower() == "/cancel":
                return None
            if answer:
                if answer.isdecimal() and 1 <= int(answer) <= len(request["options"]):
                    return request["options"][int(answer) - 1]
                return answer
    except (EOFError, KeyboardInterrupt):
        return None


def question_was_cancelled(name, result):
    if name != "ask_user":
        return False
    try:
        return json.loads(result).get("status") == "cancelled"
    except (ValueError, TypeError, AttributeError):
        return False
