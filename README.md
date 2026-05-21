# LangFix (Auto-Correct Keyboard Layout)

LangFix is a lightweight Windows utility that runs in the background and automatically fixes sentences typed in the wrong keyboard layout (Thai ⇄ English). Have you ever forgotten to switch your keyboard layout and typed `l;ylfu` instead of `สวัสดี`? LangFix automatically detects this gibberish, switches the layout for you, and corrects the text on the fly!

## ✨ Features

- **Automatic Correction (Auto-Detect):** Automatically detects if you are typing English gibberish on a Thai layout (or vice versa) and corrects it when you press `Space` or `Enter`.
- **Manual Correction:** Highlight any incorrectly typed text and press `Ctrl + Shift + Space` to immediately translate and fix it.
- **Smart Undo:** Did the program correct a word it shouldn't have? Press `Ctrl + Shift + Z` immediately after to undo the correction and revert to your original text.
- **Ignore List (Whitelist):** Press `Ctrl + Shift + I` after a correction to undo it *and* add that word to the Ignore List so LangFix will never try to correct it again.
- **WTT Simulator Integration:** Accurately calculates dead keys and overlapping vowels in the Windows Thai layout to ensure backspacing and replacing text is 100% precise.
- **System Tray:** Runs silently in the system tray.

## ⌨️ Keyboard Shortcuts

| Shortcut | Description |
|---|---|
| `Ctrl + Shift + Space` | **Manual Fix**: Corrects the currently selected/highlighted text. |
| `Ctrl + Shift + Z` | **Undo**: Reverts the last auto-correction. |
| `Ctrl + Shift + I` | **Ignore**: Reverts the last auto-correction and adds the word to the ignore list. |

## 🚀 Installation & Build

Since LangFix interacts directly with Windows APIs and keyboard inputs, it is designed to be compiled into a standalone `.exe` for easy usage.

### Prerequisites
- Python 3.x
- Windows OS

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Build the Executable
Use PyInstaller to package the script and its dictionaries into a single `.exe` file:
```bash
python -m PyInstaller --noconsole --onefile --collect-data pythainlp --collect-data spellchecker -n LangFix -y app.py
```

### 3. Usage
Once built, you will find `LangFix.exe` in the `dist/` folder. 
- Double click it to run. You will see an **LF** icon in your system tray.
- To make it run automatically when you turn on your PC, simply copy the `.exe` file into your Windows Startup folder:
  `Win + R` -> type `shell:startup` -> Paste `LangFix.exe` here.

## 🛠️ Configuration (Ignore List)
You can manually edit the ignore list by right-clicking the **LF** icon in the system tray and selecting **"Edit Ignore List"**. Add any specific names, slang, or technical terms you don't want the program to touch (one word per line).

## 📄 Dependencies
- `pynput` (for global keyboard hooks)
- `pyperclip` (for clipboard management)
- `pystray` & `Pillow` (for system tray icon)
- `pythainlp` (for Thai dictionary and tokenization)
- `pyspellchecker` (for English dictionary validation)

## License
MIT License
