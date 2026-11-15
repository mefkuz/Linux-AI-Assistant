import speech_recognition as sr
import logging
import contextlib
import os
import sys
from src.core.i18n import tr

logger = logging.getLogger(__name__)


class _RecordingCancelled(Exception):
    pass


class _CancellableStream:
    def __init__(self, stream, event):
        self.stream = stream
        self.event = event

    def read(self, *args, **kwargs):
        if self.event.is_set():
            raise _RecordingCancelled()
        data = self.stream.read(*args, **kwargs)
        if self.event.is_set():
            raise _RecordingCancelled()
        return data

    def __getattr__(self, name):
        return getattr(self.stream, name)

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
        self.last_error = None
        self.recognizer.operation_timeout = 15

    def listen_and_transcribe(self, cancel_event=None, status_callback=None):
        """
        Mikrofondan ses alır ve metne çevirir.
        """
        language     = "tr-TR"
        timeout      = 5
        phrase_limit = 30   # Maks. konuşma süresi (sn)
        pause_thresh = 1.5  # Sessizlik süresi — bunu artırırsanız erken kesilmez
        self.last_error = None

        def check_cancelled():
            if cancel_event is not None and cancel_event.is_set():
                raise _RecordingCancelled()

        if self.settings:
            language     = self.settings.get("language", "tr-TR")
            timeout      = self.settings.get("overlay_timeout_seconds", 5)
            phrase_limit = self.settings.get("phrase_time_limit", 30)
            pause_thresh = self.settings.get("pause_threshold", 1.5)

        # Sessizlik süresi ayarı (varsayılan 0.8s — çok kısa)
        self.recognizer.pause_threshold      = pause_thresh
        self.recognizer.non_speaking_duration = min(pause_thresh, 0.8)

        try:
            check_cancelled()
            with suppress_c_stderr():
                mic = sr.Microphone()
                
            with mic as source:
                if cancel_event is not None:
                    source.stream = _CancellableStream(source.stream, cancel_event)
                logger.info("Ortam gürültüsü kalibre ediliyor...")
                self.recognizer.adjust_for_ambient_noise(source, duration=0.4)
                logger.info(f"Dinleniyor... (dil={language}, timeout={timeout}s)")
                if status_callback:
                    status_callback("listening")

                audio = self.recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_limit)
                logger.info("Ses alındı, işleniyor...")

            # Release the microphone before the network recognition request.
            check_cancelled()
            if status_callback:
                status_callback("transcribing")
            text = self.recognizer.recognize_google(audio, language=language)
            check_cancelled()
            return text

        except _RecordingCancelled:
            return None
        except sr.WaitTimeoutError:
            self.last_error = tr("Ses algılanamadı. Tekrar deneyebilirsin.")
            logger.warning("Ses algılanmadı (Zaman aşımı).")
            return None
        except sr.UnknownValueError:
            self.last_error = tr("Ses anlaşılamadı. Tekrar deneyebilir veya yanıtını yazabilirsin.")
            logger.warning("Söylenen anlaşılamadı.")
            return None
        except sr.RequestError as e:
            self.last_error = tr("Ses tanıma servisine ulaşılamadı. Yanıtını yazarak gönderebilirsin.")
            logger.error(f"STT Servisine ulaşılamadı: {e}")
            return None
        except Exception as e:
            self.last_error = tr("Mikrofon kullanılamadı. Yanıtını yazarak gönderebilirsin.")
            logger.exception("Dinleme sırasında beklenmeyen hata.")
            return None
