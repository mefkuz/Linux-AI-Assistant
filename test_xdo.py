import subprocess
result = subprocess.run(['xdotool', 'getactivewindow', 'getwindowname'], capture_output=True, text=True)
print(f"[{result.returncode}] {result.stdout.strip()}")
