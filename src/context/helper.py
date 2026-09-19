import subprocess
import re
from src.core.i18n import tr

def get_active_contexts():
    """
    Scans the system for active media players (MPRIS) to provide
    contextual information for the AI.
    Returns a list of dicts with 'type', 'title', 'detail', 'icon'.
    Only media contexts (YouTube video / playing song) are returned.
    """
    contexts = []
    
    # --- MPRIS Media Players ---
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
                    
                    if is_youtube:
                        contexts.append({"type": "media", "icon": "▶", "label": tr("YouTube Videosunu"), "title": title or "YouTube Video", "detail": url})
                    elif is_spotify:
                        disp = f"{artist} - {title}" if artist and title else (title or tr("Spotify Şarkısı"))
                        contexts.append({"type": "media", "icon": "♪", "label": tr("Çalan Şarkıyı"), "title": disp, "detail": url})
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
