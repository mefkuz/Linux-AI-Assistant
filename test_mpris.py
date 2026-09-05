import subprocess
import re

def get_youtube_title():
    try:
        # Get list of D-Bus services
        result = subprocess.run(['dbus-send', '--session', '--dest=org.freedesktop.DBus',
                                 '--type=method_call', '--print-reply',
                                 '/org/freedesktop/DBus', 'org.freedesktop.DBus.ListNames'],
                                capture_output=True, text=True)
        if result.returncode != 0:
            return None
        
        # Find all MPRIS players
        players = []
        for line in result.stdout.split('\n'):
            match = re.search(r'"(org\.mpris\.MediaPlayer2\.[^"]+)"', line)
            if match:
                players.append(match.group(1))
                
        for player in players:
            # Query metadata
            meta_result = subprocess.run(['dbus-send', '--session', f'--dest={player}',
                                          '--type=method_call', '--print-reply',
                                          '/org/mpris/MediaPlayer2',
                                          'org.freedesktop.DBus.Properties.Get',
                                          'string:org.mpris.MediaPlayer2.Player',
                                          'string:Metadata'],
                                         capture_output=True, text=True)
            if meta_result.returncode == 0:
                # Poor man's parsing of dbus-send output for xesam:title and xesam:url
                title_match = re.search(r'string "xesam:title"\n\s+variant\s+string "(.*?)"', meta_result.stdout, re.DOTALL)
                url_match = re.search(r'string "xesam:url"\n\s+variant\s+string "(.*?)"', meta_result.stdout, re.DOTALL)
                
                title = title_match.group(1) if title_match else None
                url = url_match.group(1) if url_match else None
                
                print(f"Player: {player}, Title: {title}, URL: {url}")
    except Exception as e:
        print(f"Error: {e}")

get_youtube_title()
