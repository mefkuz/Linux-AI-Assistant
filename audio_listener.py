import speech_recognition as sr
import logging
import contextlib
import os
import sys

logger = logging.getLogger(__name__)

@contextlib.contextmanager
def suppress_c_stderr():
    try:
        devnull = os.open(os.devnull, os.O_WRONLY)
        old_stderr = os.dup(2)
        sys.stderr.flush()
        os.dup2(devnull, 2)
        os.close(devnull)
    except Exception:
        yield
        return
    try:
        yield
    finally:
        sys.stderr.flush()
        os.dup2(old_stderr, 2)
        os.close(old_stderr)

class AudioListener:
    def __init__(self, settings=None):
        self.recognizer = sr.Recognizer()
        self.settings = settings

    def listen_and_transcribe(self):
        """
        Mikrofondan ses alır ve metne çevirir.
        """
        language     = "tr-TR"
        timeout      = 5
        phrase_limit = 30   # Maks. konuşma süresi (sn)
        pause_thresh = 1.5  # Sessizlik süresi — bunu artırırsanız erken kesilmez

        if self.settings:
            language     = self.settings.get("language", "tr-TR")
            timeout      = self.settings.get("overlay_timeout_seconds", 5)
            phrase_limit = self.settings.get("phrase_time_limit", 30)
            pause_thresh = self.settings.get("pause_threshold", 1.5)

        # Sessizlik süresi ayarı (varsayılan 0.8s — çok kısa)
        self.recognizer.pause_threshold      = pause_thresh
        self.recognizer.non_speaking_duration = min(pause_thresh, 0.8)

        try:
            with suppress_c_stderr():
                mic = sr.Microphone()
                
            with mic as source:
                logger.info("Ortam gürültüsü kalibre ediliyor...")
                self.recognizer.adjust_for_ambient_noise(source, duration=0.4)
                logger.info(f"Dinleniyor... (dil={language}, timeout={timeout}s)")

                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
                logger.info("Ses alındı, işleniyor...")

                text = self.recognizer.recognize_google(audio, language=language)
                return text

        except sr.WaitTimeoutError:
            logger.warning("Ses algılanmadı (Zaman aşımı).")
            return None
        except sr.UnknownValueError:
            logger.warning("Söylenen anlaşılamadı.")
            return None
        except sr.RequestError as e:
            logger.error(f"STT Servisine ulaşılamadı: {e}")
            return None
        except Exception as e:
            logger.exception("Dinleme sırasında beklenmeyen hata.")
            return None
