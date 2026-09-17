import os
import logging
import sys

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # python-dotenv kurulu değilse .env yükleme atlanır

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

_HERE = os.path.dirname(os.path.abspath(__file__))
_VENV_PY = os.path.join(_HERE, "venv", "bin", "python")

# app.py ile aynı kural: venv varsa ona geç (sistem python'uyla kırık çalışmayı önler).
if os.path.isfile(_VENV_PY) and os.path.realpath(sys.executable) != os.path.realpath(_VENV_PY):
    os.execv(_VENV_PY, [_VENV_PY, os.path.abspath(__file__)] + sys.argv[1:])

try:
    from src.llm.router import Router
    from src.core.i18n import tr, set_language
except ModuleNotFoundError as e:
    missing = getattr(e, "name", "") or str(e)
    sys.stderr.write(
        f"\nHATA / ERROR: Gerekli Python paketi eksik / missing package: '{missing}'\n"
        "Çözüm / Fix: ./scripts/install.sh  (veya: ./venv/bin/pip install -r requirements.txt)\n\n"
    )
    sys.exit(2)

# Loglama ayarları (terminalde ve dosyada tutulur)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_app.log")),
        logging.StreamHandler()
    ]
)

# Konsol çıktılarını temizlemek için kök logger seviyesini uyarı düzeyine çekiyoruz
logging.getLogger().setLevel(logging.WARNING)

def main():
    print("=====================================================")
    print("=      Linux AI Assistant ve Yapay Zeka Entegratörü     =")
    print("=====================================================")
    print(tr("Mod: Bağlam Duyarlı CLI / LLM Arabirimi"))
    print(tr("Çıkmak için 'exit' veya 'quit' yazın.") + "\n")
    
    # Ortam değişkenlerini (.env) yükle
    if load_dotenv:
        load_dotenv()
    
    # Router başlat (Otomatik olarak LLM ve CLI Client'ları ayağa kaldırır)
    router = Router()
    try:
        set_language(router.settings.get("app_language", "tr") if router.settings else "tr")
    except Exception:
        pass
    
    while True:
        try:
            # Girdi Bekleme
            user_input = input("\n" + tr("[Kullanıcı] >> ")).strip()
            
            if user_input.lower() in ['exit', 'quit']:
                print(tr("Oturum sonlandırılıyor..."))
                break
                
            if not user_input:
                continue
                
            # Yönlendirme ve Analiz İşlemi
            response = router.parse_and_route(user_input)
            
            # Yanıtı Bastırma
            print(f"\n[Linux AI Assistant] >> {response}")
            
        except KeyboardInterrupt:
            print("\n" + tr("Oturum sonlandırılıyor...") + " (Ctrl+C)")
            break
        except Exception as e:
            logging.exception("Ana döngüde beklenmeyen bir hata oluştu.")
            print(f"\n{tr("[Kritik Hata]:")} {str(e)}\n")

if __name__ == "__main__":
    main()
