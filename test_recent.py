import subprocess
workspace = "/home/mefkuz/Belgeler/projeler/Linux-AI-Assistant"
res = subprocess.run(['find', workspace, '-type', 'f', 
                      '-not', '-path', '*/.git/*', 
                      '-not', '-path', '*/node_modules/*', 
                      '-not', '-path', '*/venv/*', 
                      '-not', '-path', '*/__pycache__/*',
                      '-printf', '%T@ %p\n'], capture_output=True, text=True)
if res.stdout.strip():
    lines = res.stdout.strip().split('\n')
    lines.sort(reverse=True)
    print("Most recent 3 files:")
    for l in lines[:3]:
        print(l.split(' ', 1)[1])
