import os
import logging
from dotenv import load_dotenv
from router import Router

# Loglama ayarları (terminalde ve dosyada tutulur)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler("bridge_app.log"), 
        logging.StreamHandler()
    ]
)

# Konsol çıktılarını temizlemek için kök logger seviyesini uyarı düzeyine çekiyoruz
logging.getLogger().setLevel(logging.WARNING)

def main():
    print("=====================================================")
    print("=      Sistem Köprüsü ve Yapay Zeka Entegratörü     =")
    print("=====================================================")
    print("Mod: Bağlam Duyarlı CLI / LLM Arabirimi")
    print("Çıkmak için 'exit' veya 'quit' yazın.\n")
    
    # Ortam değişkenlerini (.env) yükle
    load_dotenv()
    
    # Router başlat (Otomatik olarak LLM ve CLI Client'ları ayağa kaldırır)
    router = Router()
    
    while True:
        try:
            # Girdi Bekleme
            user_input = input("\n[Kullanıcı] >> ").strip()
            
            if user_input.lower() in ['exit', 'quit']:
                print("Oturum sonlandırılıyor...")
                break
                
            if not user_input:
                continue
                
            # Yönlendirme ve Analiz İşlemi
            response = router.parse_and_route(user_input)
            
            # Yanıtı Bastırma
            print(f"\n[Sistem Köprüsü] >> {response}")
            
        except KeyboardInterrupt:
            print("\nOturum sonlandırılıyor (Ctrl+C)...")
            break
        except Exception as e:
            logging.exception("Ana döngüde beklenmeyen bir hata oluştu.")
            print(f"\n[Kritik Hata]: {str(e)}\n")

if __name__ == "__main__":
    main()
