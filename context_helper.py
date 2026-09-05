import subprocess
import re
import os

def get_active_contexts():
    """
    Scans the system for active media, windows, or processes 
    to provide contextual information for the AI.
    Returns a list of dicts with 'type', 'title', 'detail', 'icon'.
    """
    contexts = []
    
    # --- 1. MPRIS Media Players ---
    try:
        result = subprocess.run(['dbus-send', '--session', '--dest=org.freedesktop.DBus',
                                 '--type=method_call', '--print-reply',
                                 '/org/freedesktop/DBus', 'org.freedesktop.DBus.ListNames'],
                                capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            players = []
            for line in result.stdout.split('\n'):
                match = re.search(r'"(org\.mpris\.MediaPlayer2\.[^"]+)"', line)
                if match:
                    players.append(match.group(1))
                    
            for player in players:
                meta_result = subprocess.run(['dbus-send', '--session', f'--dest={player}',
                                              '--type=method_call', '--print-reply',
                                              '/org/mpris/MediaPlayer2',
                                              'org.freedesktop.DBus.Properties.Get',
                                              'string:org.mpris.MediaPlayer2.Player',
                                              'string:Metadata'],
                                             capture_output=True, text=True, timeout=1)
                if meta_result.returncode == 0:
                    title_match = re.search(r'string "xesam:title"[\s\n]+variant\s+string "(.*?)"', meta_result.stdout, re.DOTALL)
                    url_match = re.search(r'string "xesam:url"[\s\n]+variant\s+string "(.*?)"', meta_result.stdout, re.DOTALL)
                    artist_match = re.search(r'string "xesam:artist"[\s\n]+variant\s+array \[\s+string "(.*?)"', meta_result.stdout, re.DOTALL)
                    
                    title = title_match.group(1) if title_match else None
                    url = url_match.group(1) if url_match else None
                    artist = artist_match.group(1) if artist_match else None
                    
                    if not title and not url:
                        continue
                        
                    is_youtube = (title and "YouTube" in title) or (url and "youtube.com/watch" in url)
                    is_spotify = "spotify" in player.lower() or (url and "spotify.com" in url)
                    is_vlc = "vlc" in player.lower()
                    
                    if is_youtube:
                        contexts.append({"type": "media", "icon": "▶", "label": "YouTube Videosunu", "title": title or "YouTube Video", "detail": url})
                    elif is_spotify:
                        disp = f"{artist} - {title}" if artist and title else (title or "Spotify Şarkısı")
                        contexts.append({"type": "media", "icon": "🎵", "label": "Çalan Şarkıyı", "title": disp, "detail": url})
                    else:
                        disp = f"{artist} - {title}" if artist and title else (title or "Medya")
                        contexts.append({"type": "media", "icon": "▶", "label": "Aktif Medyayı", "title": disp, "detail": url})
    except Exception:
        pass

    # --- 2. Active Window (X11 / Fallback) ---
    w_title = None
    try:
        # Try xdotool
        result = subprocess.run(['xdotool', 'getactivewindow', 'getwindowname'], capture_output=True, text=True, timeout=1)
        if result.returncode == 0 and result.stdout.strip():
            w_title = result.stdout.strip()
    except Exception:
        pass

    if not w_title:
        try:
            # Try xprop (more commonly installed than xdotool)
            id_result = subprocess.run(['xprop', '-root', '_NET_ACTIVE_WINDOW'], capture_output=True, text=True, timeout=1)
            if id_result.returncode == 0:
                match = re.search(r'window id # (0x[a-fA-F0-9]+)', id_result.stdout)
                if match:
                    win_id = match.group(1)
                    name_result = subprocess.run(['xprop', '-id', win_id, '_NET_WM_NAME'], capture_output=True, text=True, timeout=1)
                    if name_result.returncode == 0:
                        name_match = re.search(r'_NET_WM_NAME\(\w+\) = "(.*)"', name_result.stdout)
                        if name_match:
                            w_title = name_match.group(1).strip()
        except Exception:
            pass

    if w_title:
        try:
            # Terminal
            if "Terminal" in w_title or "Konsole" in w_title or "Alacritty" in w_title or "Kitty" in w_title or "@" in w_title:
                contexts.append({"type": "window", "icon": "🖥️", "label": "Terminali", "title": w_title, "detail": None})
            # Dosya Yöneticisi
            elif "Nautilus" in w_title or "Dolphin" in w_title or "Thunar" in w_title or "Nemo" in w_title:
                contexts.append({"type": "window", "icon": "📁", "label": "Açık Klasörü", "title": w_title, "detail": None})
            # Ofis
            elif "LibreOffice" in w_title or "Word" in w_title or "Excel" in w_title:
                contexts.append({"type": "window", "icon": "📄", "label": "Üzerinde Çalışılan Belgeyi", "title": w_title, "detail": None})
            # Tarayıcı (Eğer medya algılanmadıysa veya sekmeyi de eklemek isterse)
            elif "Firefox" in w_title or "Chrome" in w_title or "Brave" in w_title or "Edge" in w_title:
                contexts.append({"type": "window", "icon": "🌐", "label": "Açık Sekmeyi", "title": w_title, "detail": None})
            # Bilinmeyen / Klasör (Örneğin sadece "Belgeler" yazıyorsa)
            elif w_title:
                # Kendi arayüzümüzü yoksayalım
                if "Linux-AI-Assistant" not in w_title:
                    contexts.append({"type": "window", "icon": "🗔", "label": "Açık Pencereyi", "title": w_title, "detail": None})
        except Exception:
            pass

    # Kwin/KDE Wayland fallback for Active Window
    try:
        if "KDE_FULL_SESSION" in os.environ:
            result = subprocess.run(['qdbus', 'org.kde.KWin', '/KWin', 'org.kde.KWin.activeWindow'], capture_output=True, text=True, timeout=1)
            # Hard to parse easily without complex scripting, we will rely on XWayland/xdotool where possible or leave it.
    except Exception:
        pass

    # Remove duplicates or prefer ones with URL
    final_contexts = {}
    for c in contexts:
        # Simple clustering by first 20 chars of title to merge brave/plasma duplicates
        key = c['title'][:20] if c['title'] else str(c)
        if key in final_contexts:
            if c['detail'] and not final_contexts[key]['detail']:
                final_contexts[key] = c
        else:
            final_contexts[key] = c

    return list(final_contexts.values())

if __name__ == "__main__":
    print(get_active_contexts())
