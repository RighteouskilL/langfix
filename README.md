# LangFix — Auto-Correct Keyboard Layout (Thai ⇄ English)

LangFix is a lightweight Windows background utility that automatically detects and corrects text typed on the wrong keyboard layout. Ever typed `l;ylfu` instead of `สวัสดี` because you forgot to switch layouts? LangFix silently watches your keystrokes, detects the mistake, and fixes it in real time — no interruptions, no popups you didn't ask for.

---

## ✨ Features

- **Auto-Correct on Space / Enter / Idle:** Detects gibberish (English chars on Thai layout or vice versa) and corrects it automatically when you press `Space`, `Enter`, or after a short idle pause (~1.2s).
- **Word Suggestion Popup:** While typing, a compact suggestion bar appears at the top of the screen showing Thai word completions. Navigate with Vim-style keys and accept with `Ctrl+Space`.
- **Smart Undo:** Press `Ctrl+Shift+Z` immediately after a correction to revert the change and restore exactly what you had before.
- **Ignore & Undo:** Press `Ctrl+Shift+I` to undo the last correction *and* add that word to the ignore list so LangFix never touches it again.
- **Manual Fix:** Highlight any wrongly typed text and press `Ctrl+Shift+Space` to force-translate it between layouts.
- **Thai Dead-Key Aware:** Uses a WTT (Windows Thai Traditional) simulator to accurately count rendered characters (accounting for combined vowels and tone marks), so backspacing and replacing is always pixel-perfect.
- **Mouse & Cursor Aware:** Clicking the mouse or pressing arrow keys / Home / End / Delete automatically resets the buffer so stale context never causes wrong suggestions.
- **Rotating Log:** Diagnostic log (`langfix.log`) rotates automatically at 1 MB, keeping up to 3 backups. The log never grows unbounded.
- **System Tray:** Runs silently with an **LF** icon in the system tray.

---

## ⌨️ Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl + Space` | **Accept** the highlighted suggestion from the popup |
| `Ctrl + H` / `Ctrl + P` / `Ctrl + K` | Navigate suggestion popup **← Prev** |
| `Ctrl + L` / `Ctrl + N` / `Ctrl + J` | Navigate suggestion popup **→ Next** |
| `Esc` | Dismiss the suggestion popup |
| `Ctrl + Shift + Z` | **Undo** the last auto-correction |
| `Ctrl + Shift + I` | **Undo + Ignore** — reverts correction and whitelists the word |
| `Ctrl + Shift + Space` | **Manual Fix** — translate highlighted/selected text |

---

## 🚀 Installation & Usage

### Prerequisites
- Python 3.9+
- Windows 10 / 11
- Thai Kedmanee keyboard layout installed

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Run Directly
```bash
python app.py
```
An **LF** icon will appear in the system tray. LangFix is now active.

### 3. Build a Standalone Executable (Optional)
Package everything into a single `.exe` with PyInstaller:
```bash
python -m PyInstaller --noconsole --onefile --collect-data pythainlp --collect-data spellchecker -n LangFix -y app.py
```
The output will be in `dist/LangFix.exe`.

### 4. Auto-Start with Windows
To launch LangFix automatically on login, copy `LangFix.exe` (or a shortcut to `app.py`) into the Windows Startup folder:

1. Press `Win + R`
2. Type `shell:startup` and press Enter
3. Paste `LangFix.exe` into that folder

---

## 🛠️ Configuration — Ignore List

Words you never want LangFix to touch (e.g. brand names, technical terms, slang) can be added to the ignore list:

- **Via tray:** Right-click the **LF** tray icon → **Edit Ignore List** (opens in Notepad)
- **Via keyboard:** Press `Ctrl+Shift+I` after any unwanted correction
- **Manually:** Edit `ignore_list.txt` — one word per line, lines starting with `#` are comments

---

## 📂 Project Structure

| File | Description |
|---|---|
| `app.py` | Main application — keyboard hooks, correction engine, UI thread, tray |
| `autocorrect.py` | Gibberish detection, layout mapping, Thai spell-check, suggestion logic |
| `ignore_list.txt` | User-defined whitelist (auto-created on first run) |
| `langfix.log` | Rotating diagnostic log (max 1 MB × 3 backups) |
| `requirements.txt` | Python dependencies |

---

## 📄 Dependencies

| Package | Purpose |
|---|---|
| `pynput` | Global keyboard hook and key injection |
| `pyperclip` | Clipboard read/write for manual fix and undo |
| `pystray` + `Pillow` | System tray icon |
| `pythainlp` | Thai dictionary, tokenization, and spell-check |
| `pyspellchecker` | English dictionary validation |

---

## License
MIT License
