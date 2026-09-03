#!/bin/bash
set -e

echo "======================================"
echo "    Linux-AI-Assistant Kurulum Sihirbazı"
echo "======================================"

# Scriptin bulunduğu dizini al
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "$APP_DIR"

echo "[1/4] Gerekli sistem paketleri kontrol ediliyor..."

if command -v pacman &> /dev/null; then
    echo "Arch Linux tabanlı sistem algılandı. Gerekli paketler yükleniyor..."
    sudo pacman -S --needed --noconfirm python python-pip python-virtualenv portaudio tesseract tesseract-data-tur tesseract-data-eng grim spectacle gnome-screenshot
elif command -v apt-get &> /dev/null; then
    echo "Debian/Ubuntu tabanlı sistem algılandı. Gerekli paketler yükleniyor..."
    sudo apt-get update
    sudo apt-get install -y build-essential python3-venv python3-pip portaudio19-dev python3-dev python3-pyaudio xcb libxcb-cursor0 libxcb-xinerama0 libxkbcommon-x11-0 libgl1-mesa-glx libegl1-mesa tesseract-ocr tesseract-ocr-tur tesseract-ocr-eng grim gnome-screenshot
elif command -v dnf &> /dev/null; then
    echo "Fedora/RHEL tabanlı sistem algılandı. Gerekli paketler yükleniyor..."
    sudo dnf install -y python3 python3-pip portaudio-devel python3-devel tesseract tesseract-langpack-tur tesseract-langpack-eng grim gnome-screenshot
elif command -v zypper &> /dev/null; then
    echo "openSUSE tabanlı sistem algılandı. Gerekli paketler yükleniyor..."
    sudo zypper install -y -n python3 python3-pip portaudio-devel python3-devel tesseract-ocr tesseract-ocr-traineddata-turkish tesseract-ocr-traineddata-english grim gnome-screenshot
else
    echo "Uyarı: 'pacman', 'apt', 'dnf' veya 'zypper' bulunamadı. Lütfen 'portaudio', 'tesseract' ve OCR dil paketlerinin sisteminizde kurulu olduğundan emin olun."
fi

echo "[2/4] Python Sanal Ortamı (venv) kuruluyor..."
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

echo "[3/4] Python kütüphaneleri yükleniyor..."
pip install --upgrade pip
pip install PyQt6 pyaudio SpeechRecognition pynput requests gTTS pygame

echo "[4/4] Masaüstü ve Başlangıç (Autostart) kısayolları oluşturuluyor..."

# Masaüstü ve Autostart dizinleri
APPS_DIR="$HOME/.local/share/applications"
AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$APPS_DIR"
mkdir -p "$AUTOSTART_DIR"

DESKTOP_FILE_CONTENT="[Desktop Entry]
Name=Linux-AI-Assistant
Comment=Akıllı Linux AI Assistant ve Sesli Asistan
Exec=$APP_DIR/venv/bin/python $APP_DIR/gui_main.py
Path=$APP_DIR
Icon=audio-input-microphone
Terminal=false
Type=Application
Categories=Utility;Audio;
StartupNotify=true"

# Uygulama menüsü için kısayol
echo "$DESKTOP_FILE_CONTENT" > "$APPS_DIR/linux-ai-assistant.desktop"
chmod +x "$APPS_DIR/linux-ai-assistant.desktop"

# Bilgisayar açılışında otomatik başlama için kısayol
echo "$DESKTOP_FILE_CONTENT" > "$AUTOSTART_DIR/linux-ai-assistant.desktop"
chmod +x "$AUTOSTART_DIR/linux-ai-assistant.desktop"

echo "======================================"
echo "Kurulum Tamamlandı!"
echo "Uygulamanız menüye eklendi ve bilgisayar her açıldığında otomatik başlayacak."
echo "Linux-AI-Assistant uygulamasını şimdi menüden aratarak veya aşağıdaki komutla başlatabilirsiniz:"
echo "$APP_DIR/venv/bin/python $APP_DIR/gui_main.py"
echo "======================================"
