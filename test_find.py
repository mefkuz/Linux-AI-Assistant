import subprocess
import os

workspace = "/home/mefkuz/Belgeler/projeler/Linux-AI-Assistant"
w_title = "• gui_main.py - Linux-AI-Assistant - Visual Studio Code"

candidates = set([p.strip() for p in w_title.replace("•", "").split(" - ")] + w_title.replace("•", "").split())

for cand in candidates:
    if not cand or len(cand) < 3: continue
    print(f"Checking: {cand}")
    res = subprocess.run(['find', workspace, '-type', 'f', '-name', cand], capture_output=True, text=True)
    if res.stdout.strip():
        filepath = res.stdout.strip().split('\n')[0]
        print(f"FOUND FILE! {filepath}")
