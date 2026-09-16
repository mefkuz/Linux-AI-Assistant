# Linux AI Assistant

A highly configurable, context-aware voice assistant and system bridge designed exclusively for Linux desktop environments.

While there are many AI assistants available for Windows and macOS, Linux power users often lack a deeply integrated, native tool that respects the Linux ecosystem. Linux AI Assistant bridges this gap by offering a seamless overlay interface, robust context awareness (screen OCR and clipboard reading), and flexible API support, all while remaining completely unobtrusive to your workflow.

## Key Features

- **Unobtrusive Overlay UI:** Operates in the background with a minimal, non-blocking overlay that stays out of your way.
- **Tool Calling (NEW!):** In Remote/Local API modes the AI can use 9 function tools on its own — run shell commands, read/write/append files, read your screen (OCR) and clipboard, check the active window, and control the browser. Dangerous commands and sensitive reads ask for confirmation first.
- **Conversation Memory (NEW!):** The AI remembers the last N dialogue turns (configurable, default 6). Follow-ups like "add that to the file you just opened" work naturally. Clear it anytime from the tray menu.
- **Browser Extension Integration:** A two-way communication bridge that lets the AI read your Gmail, PDFs, and YouTube videos, and lets you voice-control the browser (scroll, close tabs, auto-fill forms).
- **Context Awareness:** Can instantly read your clipboard and perform OCR on your screen (using native Linux tools like Grim, Spectacle, or Gnome-Screenshot) to provide context to the AI.
- **Universal Linux Support:** The installer handles dependencies seamlessly across Arch, Debian/Ubuntu, Fedora, and openSUSE.
- **Flexible LLM Integration:** Connect to local models (e.g., LM Studio, Ollama), remote APIs (e.g., Groq, OpenAI, OpenRouter), or CLI-based AI tools.
- **Bilingual Interface:** Supports both English and Turkish application languages out of the box.

## Tool Calling

When **Tool Calling** is enabled (Settings → Security, active in Remote/Local API modes), the model receives 9 function tools and calls them as needed — no keywords required:

| Tool | What it does |
|---|---|
| `run_shell_command` | Runs a Linux terminal command (stdout + stderr + exit code) |
| `read_file` / `write_file` / `append_file` | Read, overwrite, or append to text files (workspace-restricted) |
| `list_directory` | Lists folder contents |
| `read_screen_text` | Screenshots the screen and reads it via OCR |
| `get_clipboard_text` | Reads the clipboard |
| `get_active_window_context` | Lists the active window / media player context |
| `browser_action` | Controls the browser via the extension (close tab, scroll, fill form, new tab) |

**Safety model (3 layers):**
1. Sensitive reads (screen/clipboard) ask for confirmation every time unless you allow them in settings.
2. File writes and command execution ask only if you enable "ask before running tools".
3. Dangerous shell commands (`rm`, `sudo`, …) always ask for confirmation, even with confirmations off — and file tools can never escape the workspace directory.

Every tool call is logged to `Loglar/araclar-YYYY-MM-DD.md`, and models/servers that don't support the `tools` parameter automatically fall back to plain chat.

## Conversation Memory

The assistant keeps the last N question-answer turns (Settings → Security → "Conversation turns to remember", 0 = off) and sends them with each request, in all three LLM modes. Internal calls (CLI output analysis, dictation cleanup) never pollute the history. Use **Tray menu → Clear Memory** to start fresh.

## Browser Extension Integration

The **Manifest V3 Browser Extension** (compatible with Chrome, Brave, and Edge) takes the AI's context awareness to the next level. It works in both directions:

- **Browser → AI (context):** Click "Send This Tab" (or right-click selected text → "Ask Linux AI Assistant") to inject the page into the AI's context. Gmail threads, PDFs, and articles are extracted as clean text; YouTube videos automatically resolve to their full transcript.
- **AI → Browser (control):** Voice commands like "Close this tab", "Scroll down", or "Reply to this email with a polite rejection" are executed through the `browser_action` tool — the AI drafts the reply and injects it directly into your Gmail compose box or any active web form.

The extension talks to the app over `http://127.0.0.1:8765` (local only, never leaves your machine).

**Installation (Chrome / Brave / Edge):**
1. Navigate to `chrome://extensions/` (or `edge://extensions/`) in your browser.
2. Enable **Developer mode** in the top right.
3. Click **Load unpacked** in the top left.
4. Select the `browser_extension` folder located inside this repository.
5. Pin the microphone icon to your toolbar!

**Installation (Firefox):**
🚧 Work in progress — a separate Firefox build lives in `browser_extension_firefox/`, but it is not yet stable. Tracked for a future release.

## Screenshots

### Main Interface

When triggered via your custom global hotkey, the minimal listening overlay appears. It provides visual waveform feedback for your voice and auto-closes when the interaction is finished.

![Listening Overlay](screenshots/listining.png)

![Waiting for AI](screenshots/waiting%20for%20AI%20Response.png)

### Configuration & Settings

Linux AI Assistant is highly customizable, putting the control entirely in your hands.

**General Settings**
Configure your global hotkey and choose the application language.
![General Settings](screenshots/general%20settings.png)

**Listening & Overlay Settings**
Fine-tune microphone sensitivity, pause detection thresholds, and the physical position of the overlay.
![Listening Settings](screenshots/Listening%20settings.png)

**AI & API Configuration**
Easily switch between local AI instances, remote APIs, and command-line LLM tools.
![AI Settings](screenshots/AI%20Settings.png)

**Security & Advanced Settings**
Control the permissions of the AI: automatic popup responses, dictation optimization, screen/clipboard reading permissions, Tool Calling toggles, and conversation memory size.
![Security Settings](screenshots/security%20setting.png)

## Installation

The project includes an intelligent installer script that automatically detects your Linux distribution and installs the required system packages, sets up a secure Python virtual environment (venv), and creates desktop/autostart shortcuts.

1. Clone the repository:
   ```bash
   git clone https://github.com/mefkuz/Linux-AI-Assistant.git
   cd Linux-AI-Assistant
   ```

2. Make the installer executable and run it:
   ```bash
   chmod +x install.sh
   ./install.sh
   ```

3. Launch the application:
   You can find **Linux-AI-Assistant** in your application launcher, or start it directly from the terminal:
   ```bash
   ./venv/bin/python gui_main.py
   ```

### 🎯 Wayland & Global Hotkeys
If you are using a modern Wayland compositor (like GNOME Wayland or Hyprland), traditional global hotkeys (via pynput) might be blocked by the OS for security reasons.
You can easily bypass this by mapping a custom keyboard shortcut in your OS settings to the following command:
```bash
/path/to/Linux-AI-Assistant/venv/bin/python /path/to/Linux-AI-Assistant/gui_main.py --trigger
```
This safely signals the background process to instantly wake up and start listening, making it 100% compatible with any Linux environment!

## Requirements & Dependencies

The `install.sh` script installs these automatically depending on your distribution (pacman, apt, dnf, or zypper):
- Python 3.10+
- `portaudio` (for PyAudio)
- `tesseract` & `tesseract-ocr` language packs (for screen context reading)
- A screenshot utility (`grim` for Wayland, `spectacle` for KDE, or `gnome-screenshot` for GNOME/GTK)
- XCB libraries (for PyQt6 compatibility)

## Security & Privacy

- **API Keys are local:** All settings and API keys are stored locally in a `settings.json` file. This file is intentionally ignored in `.gitignore` to prevent accidental uploads.
- **You are in control:** The system prompt is fully exposed in the settings, allowing you to explicitly define how the AI behaves and what rules it follows.
- **Permission checks:** Screen/clipboard reading asks for confirmation every time unless explicitly allowed; dangerous shell commands always require confirmation; file tools are jailed to the workspace directory.
- **Local-only browser bridge:** The extension communicates over `127.0.0.1` only — page contents never leave your machine except to your chosen LLM API.

## Contributing

Contributions, issues, and feature requests are welcome. Feel free to check the issues page.

## License

Distributed under the MIT License.
