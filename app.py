import time
import threading
import queue
import ctypes
import pyperclip
from pynput import keyboard
import pystray
from PIL import Image, ImageDraw
import logging
import logging.handlers

# Local modules
from autocorrect import is_gibberish_english, is_gibberish_thai, fix_text_manual, valid_thai_words_set, add_to_ignore_list, IGNORE_FILE, simulate_thai_output, get_suggestions
import os
import subprocess
import tkinter as tk
from ctypes import wintypes

log_handler = logging.handlers.RotatingFileHandler(
    'langfix.log', maxBytes=1_000_000, backupCount=3, encoding='utf-8'
)
logging.basicConfig(
    handlers=[log_handler],
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

controller = keyboard.Controller()
key_queue = queue.Queue()

# --- Win32 API for Language Switch ---
user32 = ctypes.WinDLL('user32', use_last_error=True)
WM_INPUTLANGCHANGEREQUEST = 0x0050
HKL_THAI = 0x041E041E
HKL_ENG = 0x04090409
LANG_THAI = 0x041E
LANG_ENG = 0x0409

def get_current_keyboard_layout():
    hwnd = user32.GetForegroundWindow()
    if hwnd:
        thread_id = user32.GetWindowThreadProcessId(hwnd, 0)
        hkl = user32.GetKeyboardLayout(thread_id)
        return hkl & 0xFFFF
    return 0

def switch_to_thai():
    hwnd = user32.GetForegroundWindow()
    user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, HKL_THAI)

def switch_to_eng():
    hwnd = user32.GetForegroundWindow()
    user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, HKL_ENG)

def wait_for_layout_change(target_lang):
    for _ in range(20):
        if get_current_keyboard_layout() == target_lang:
            break
        time.sleep(0.02)

def get_active_monitor_top_center():
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    MONITOR_DEFAULTTONEAREST = 2
    hmonitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if user32.GetMonitorInfoW(hmonitor, ctypes.byref(mi)):
        work_rect = mi.rcWork
        x = work_rect.left + (work_rect.right - work_rect.left) // 2
        y = work_rect.top + 20  # Top Center
        return x, y
    return None

is_simulating = False
current_word_buffer = []
last_correction = None
ui_queue = queue.Queue()
current_suggestions = []
selected_suggestion_index = 0

def simulate_backspaces(count):
    global is_simulating
    is_simulating = True
    
    # Release common modifiers just in case
    controller.release(keyboard.Key.shift)
    controller.release(keyboard.Key.ctrl)
    controller.release(keyboard.Key.alt)
    
    # Wait for the text editor to finish rendering the last typed character
    time.sleep(0.15)
    for _ in range(count):
        controller.press(keyboard.Key.backspace)
        time.sleep(0.02)
        controller.release(keyboard.Key.backspace)
        time.sleep(0.04)
    time.sleep(0.05)

def simulate_type(text):
    global is_simulating
    is_simulating = True
    # Use pynput's type() which sends KEYEVENTF_UNICODE — bypasses layout issues entirely
    controller.type(text)
    time.sleep(0.05)


def process_buffer(buffer, trigger="TIMEOUT"):
    """Detect gibberish and correct it. Called from worker_thread only."""
    global is_simulating, last_correction
    text = "".join(buffer)
    if len(text) < 2:
        return

    layout = get_current_keyboard_layout()

    if layout == LANG_THAI:
        # Simulate what actually got rendered on screen (Thai has dead keys)
        actual_rendered_text = simulate_thai_output(text)
        backspace_count = len(actual_rendered_text)
    else:
        backspace_count = len(text)

    # SPACE / ENTER: key was already printed on screen before we process → need +1
    # GIB_SPACE / GIB_ENTER: hook blocks the key but Windows may release it anyway
    #   due to hook timeout — so we always add +1 to be safe.
    if trigger in ("SPACE", "ENTER", "GIB_SPACE", "GIB_ENTER"):
        backspace_count += 1

    if layout == LANG_ENG:
        is_gib, thai_text = is_gibberish_english(text)
        if is_gib:
            from pythainlp import word_tokenize
            from pythainlp.spell import correct
            from pythainlp.util import isthai

            tokens = word_tokenize(thai_text, engine="newmm")
            fixed = [correct(t) if isthai(t) and t not in valid_thai_words_set else t for t in tokens]
            final_thai_text = "".join(fixed)

            last_correction = {
                'original_text': text,
                'corrected_text': final_thai_text,
                'original_layout': layout,
                'new_layout': LANG_THAI,
                'trigger': trigger
            }
            simulate_backspaces(backspace_count)
            switch_to_thai()
            wait_for_layout_change(LANG_THAI)
            simulate_type(final_thai_text)

            if trigger in ("SPACE", "GIB_SPACE"):
                simulate_type(" ")
            elif trigger in ("ENTER", "GIB_ENTER"):
                controller.press(keyboard.Key.enter)
                controller.release(keyboard.Key.enter)

            time.sleep(0.1)
            with key_queue.mutex:
                key_queue.queue.clear()
            is_simulating = False

    elif layout == LANG_THAI:
        is_gib, eng_text = is_gibberish_thai(text)
        if is_gib:
            # Store the *rendered* text (not raw keystrokes) so Ctrl+Shift+Z
            # can retype it correctly via Unicode injection.
            last_correction = {
                'original_text': actual_rendered_text,
                'corrected_text': eng_text,
                'original_layout': layout,
                'new_layout': LANG_ENG,
                'trigger': trigger
            }
            simulate_backspaces(backspace_count)
            switch_to_eng()
            wait_for_layout_change(LANG_ENG)
            simulate_type(eng_text)

            if trigger in ("SPACE", "GIB_SPACE"):
                simulate_type(" ")
            elif trigger in ("ENTER", "GIB_ENTER"):
                controller.press(keyboard.Key.enter)
                controller.release(keyboard.Key.enter)

            time.sleep(0.1)
            with key_queue.mutex:
                key_queue.queue.clear()
            is_simulating = False


def undo_last_correction(add_to_ignore=False):
    global last_correction, is_simulating
    if not last_correction:
        return

    print("Undoing last correction...")
    is_simulating = True

    orig = last_correction['original_text']
    corr = last_correction['corrected_text']
    orig_layout = last_correction['original_layout']
    trigger = last_correction['trigger']

    if add_to_ignore:
        add_to_ignore_list(orig)
        print(f"Added '{orig}' to ignore list.")

    backspace_count = len(corr)
    # Space/Enter was re-typed by us after correction, so delete it too
    if trigger in ("SPACE", "ENTER", "GIB_SPACE", "GIB_ENTER"):
        backspace_count += 1

    simulate_backspaces(backspace_count)

    # Use clipboard paste to restore original text.
    # This is completely layout-independent — avoids controller.type() sending
    # wrong chars when the current keyboard layout does not match the text (e.g.
    # typing ASCII 'scroll' while on Thai layout gives Thai chars instead).
    pyperclip.copy(orig)
    time.sleep(0.1)
    v_key = keyboard.KeyCode(vk=0x56)   # Raw VK_V — layout-independent paste
    controller.press(keyboard.Key.ctrl)
    controller.press(v_key)
    controller.release(v_key)
    controller.release(keyboard.Key.ctrl)
    time.sleep(0.1)

    # Restore the separator key that the user originally pressed
    if trigger in ("SPACE", "GIB_SPACE"):
        controller.type(" ")
    elif trigger in ("ENTER", "GIB_ENTER"):
        controller.press(keyboard.Key.enter)
        controller.release(keyboard.Key.enter)

    # Switch layout back to what the user was on originally,
    # so their subsequent typing uses the correct layout
    if orig_layout == LANG_ENG:
        switch_to_eng()
    else:
        switch_to_thai()

    time.sleep(0.1)
    with key_queue.mutex:
        key_queue.queue.clear()
    is_simulating = False
    last_correction = None


def _update_suggestions_for_buffer():
    """Helper: recompute suggestions from current_word_buffer and push to ui_queue."""
    global current_suggestions, selected_suggestion_index
    if current_word_buffer:
        suggs, prefix = get_suggestions("".join(current_word_buffer))
        if suggs or prefix:
            if suggs is None:
                suggs = []
            filtered_suggs = [s for s in suggs if s != prefix]
            current_suggestions = [prefix] + filtered_suggs
            selected_suggestion_index = 0
            ui_queue.put((current_suggestions, selected_suggestion_index))
        else:
            current_suggestions = []
            selected_suggestion_index = 0
            ui_queue.put(None)
    else:
        current_suggestions = []
        selected_suggestion_index = 0
        ui_queue.put(None)


def worker_thread():
    global current_word_buffer, current_suggestions, selected_suggestion_index, is_simulating
    while True:
        try:
            # Longer timeout when suggestions are showing (user is navigating)
            timeout_val = 10.0 if current_suggestions else 1.2
            item = key_queue.get(timeout=timeout_val)

            if item == "SPACE":
                # Space already went to the OS; process buffer and correct if gibberish.
                # process_buffer trigger="SPACE" will add +1 to backspace count for the space.
                ui_queue.put(None)
                current_suggestions = []
                if current_word_buffer:
                    buf_copy = list(current_word_buffer)
                    current_word_buffer.clear()
                    process_buffer(buf_copy, trigger="SPACE")
                else:
                    current_word_buffer.clear()

            elif item == "ENTER":
                ui_queue.put(None)
                current_suggestions = []
                if current_word_buffer:
                    buf_copy = list(current_word_buffer)
                    current_word_buffer.clear()
                    process_buffer(buf_copy, trigger="ENTER")
                else:
                    current_word_buffer.clear()

            elif item == "BACKSPACE":
                if current_word_buffer:
                    current_word_buffer.pop()
                _update_suggestions_for_buffer()

            elif item == "CLEAR":
                ui_queue.put(None)
                current_word_buffer.clear()
                current_suggestions = []
                selected_suggestion_index = 0

            elif item.startswith("ACCEPT_"):
                idx = int(item.split("_")[1])
                if current_suggestions and idx < len(current_suggestions):
                    accept_suggestion(idx)

            elif item == "UNDO_Z":
                undo_last_correction(add_to_ignore=False)

            elif item == "UNDO_I":
                undo_last_correction(add_to_ignore=True)

            elif item == "MANUAL_FIX":
                on_manual_fix()

            else:
                # Regular character
                current_word_buffer.append(item)
                _update_suggestions_for_buffer()

        except queue.Empty:
            # Idle timeout — auto-correct if there's something in the buffer
            if current_word_buffer:
                ui_queue.put(None)
                buf_copy = list(current_word_buffer)
                current_word_buffer.clear()
                current_suggestions = []
                process_buffer(buf_copy, trigger="TIMEOUT")
        except Exception as e:
            logging.error(f"Error in worker_thread: {e}")
            import traceback
            traceback.print_exc()


def accept_suggestion(idx, append_char=None):
    global is_simulating, last_correction, current_word_buffer, current_suggestions, selected_suggestion_index
    if not current_suggestions or idx >= len(current_suggestions):
        return

    suggested_word = current_suggestions[idx]
    raw_prefix = "".join(current_word_buffer)

    layout = get_current_keyboard_layout()
    is_gib, _ = is_gibberish_english(raw_prefix)
    target_layout = LANG_THAI if is_gib else layout

    if layout == LANG_THAI:
        # Use rendered text for backspace count AND for storing original (for undo)
        actual_rendered = simulate_thai_output(raw_prefix)
        backspace_count = len(actual_rendered)
        original_text_for_undo = actual_rendered
    else:
        backspace_count = len(raw_prefix)
        original_text_for_undo = raw_prefix

    last_correction = {
        'original_text': original_text_for_undo,
        'corrected_text': suggested_word,
        'original_layout': layout,
        'new_layout': target_layout,
        'trigger': 'TAB'
    }

    ui_queue.put(None)

    if is_gib:
        switch_to_thai()
        wait_for_layout_change(LANG_THAI)

    simulate_backspaces(backspace_count)
    simulate_type(suggested_word)

    if append_char == " ":
        simulate_type(" ")
    elif append_char == "ENTER":
        controller.press(keyboard.Key.enter)
        controller.release(keyboard.Key.enter)

    ui_queue.put(None)
    current_word_buffer.clear()
    current_suggestions = []
    selected_suggestion_index = 0
    with key_queue.mutex:
        key_queue.queue.clear()
    is_simulating = False


def on_manual_fix():
    print("Manual fix triggered")
    c_key = keyboard.KeyCode(vk=0x43)
    controller.press(keyboard.Key.ctrl)
    controller.press(c_key)
    controller.release(c_key)
    controller.release(keyboard.Key.ctrl)

    time.sleep(0.1)
    text = pyperclip.paste()
    fixed_text, target_lang = fix_text_manual(text)

    if fixed_text != text:
        pyperclip.copy(fixed_text)
        time.sleep(0.1)
        v_key = keyboard.KeyCode(vk=0x56)
        controller.press(keyboard.Key.ctrl)
        controller.press(v_key)
        controller.release(v_key)
        controller.release(keyboard.Key.ctrl)

        if target_lang == "thai":
            switch_to_thai()
        else:
            switch_to_eng()


def on_press(key):
    global is_simulating, current_suggestions
    if is_simulating:
        return
    try:
        # IMPORTANT: check special keys FIRST, before the hasattr(key,'char') check.
        # On some Thai keyboard layouts, the space bar reports key.char==' ' which
        # would be treated as a regular character if we checked char first.
        if key == keyboard.Key.space or getattr(key, 'char', None) == ' ':
            key_queue.put("SPACE")
        elif key == keyboard.Key.enter or getattr(key, 'char', None) in ('\n', '\r'):
            key_queue.put("ENTER")
        elif key == keyboard.Key.backspace:
            key_queue.put("BACKSPACE")
        elif key == keyboard.Key.esc:
            key_queue.put("CLEAR")
        elif key in (keyboard.Key.left, keyboard.Key.right, keyboard.Key.up, keyboard.Key.down,
                     keyboard.Key.home, keyboard.Key.end, keyboard.Key.page_up, keyboard.Key.page_down,
                     keyboard.Key.delete):
            key_queue.put("CLEAR")
        elif hasattr(key, 'char') and key.char is not None:
            key_queue.put(key.char)
    except Exception as e:
        logging.error(f"Error in on_press: {e}")


def win32_event_filter(msg, data):
    global is_simulating, current_suggestions, selected_suggestion_index, current_word_buffer

    if is_simulating:
        # Block physical keys while we are simulating. Allow only our injected events.
        if not (data.flags & 0x10):  # LLKHF_INJECTED
            return False

    # Only process key-down events for our logic
    if msg in (256, 257, 260, 261):  # WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP

        # --- Suggestion navigation (only when popup is visible) ---
        if current_suggestions:
            ctrl_pressed = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000

            if ctrl_pressed:
                # Navigation: H, K, P → Previous
                if data.vkCode in (0x48, 0x4B, 0x50):
                    if msg in (256, 260):
                        selected_suggestion_index = (selected_suggestion_index - 1) % len(current_suggestions)
                        ui_queue.put((current_suggestions, selected_suggestion_index))
                    return False
                # Navigation: L, J, N → Next
                elif data.vkCode in (0x4C, 0x4A, 0x4E):
                    if msg in (256, 260):
                        selected_suggestion_index = (selected_suggestion_index + 1) % len(current_suggestions)
                        ui_queue.put((current_suggestions, selected_suggestion_index))
                    return False
                # Accept: Ctrl+Space
                elif data.vkCode == 0x20:
                    if msg in (256, 260):
                        idx = 1 if selected_suggestion_index == 0 and len(current_suggestions) > 1 else selected_suggestion_index
                        key_queue.put(f"ACCEPT_{idx}")
                    return False

            # Esc → dismiss suggestions
            if data.vkCode == 0x1B:
                if msg in (256, 260):
                    key_queue.put("CLEAR")
                return False

        # (Space/Enter are no longer blocked here — they pass through to the OS
        #  and on_press sends SPACE/ENTER to worker_thread to trigger process_buffer.)

        # --- Global hotkeys (layout-independent via vkCode) ---
        ctrl_pressed = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
        shift_pressed = ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000

        if ctrl_pressed and shift_pressed:
            if data.vkCode == 0x5A:   # Z → Undo
                if msg in (256, 260):
                    key_queue.put("UNDO_Z")
                return False
            elif data.vkCode == 0x49:  # I → Undo + ignore
                if msg in (256, 260):
                    key_queue.put("UNDO_I")
                return False
            elif data.vkCode == 0x20:  # Space → Manual fix
                if msg in (256, 260):
                    key_queue.put("MANUAL_FIX")
                return False

    return True


def start_listening():
    from pynput import mouse

    def on_click(x, y, button, pressed):
        if pressed:
            key_queue.put("CLEAR")

    try:
        mouse_listener = mouse.Listener(on_click=on_click)
        mouse_listener.start()

        with keyboard.Listener(on_press=on_press, win32_event_filter=win32_event_filter) as listener:
            listener.join()
    except KeyboardInterrupt:
        pass


def edit_ignore_list(icon, item):
    if not os.path.exists(IGNORE_FILE):
        with open(IGNORE_FILE, 'w', encoding='utf-8') as f:
            f.write("# Add words to ignore (one per line)\n")
    subprocess.Popen(['notepad.exe', IGNORE_FILE])


def setup_tray():
    image = Image.new('RGB', (64, 64), color=(0, 120, 215))
    d = ImageDraw.Draw(image)
    d.text((20, 24), "LF", fill=(255, 255, 255))

    menu = pystray.Menu(
        pystray.MenuItem('Edit Ignore List', edit_ignore_list),
        pystray.MenuItem('Quit LangFix', lambda icon, item: [icon.stop(), __import__('os')._exit(0)])
    )
    icon = pystray.Icon("LangFix", image, "LangFix (Auto-Correct)", menu)
    icon.run()


def ui_thread():
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-disabled", True)
    root.configure(bg='#1e1e1e')
    root.attributes("-alpha", 0.0)

    main_frame = tk.Frame(root, bg='#1e1e1e', padx=8, pady=4,
                          highlightbackground="#3e3e42", highlightthickness=1)
    main_frame.pack()

    sugg_frame = tk.Frame(main_frame, bg='#1e1e1e')
    sugg_frame.pack(side=tk.LEFT)

    tip_label = tk.Label(main_frame,
                         text=" Ctrl+H/J/K/L/N/P: เลื่อน  |  Ctrl+Space: ยืนยัน  |  Esc: ปิด",
                         font=("Segoe UI", 8), bg='#1e1e1e', fg='#666666')
    tip_label.pack(side=tk.RIGHT, padx=(10, 0))

    sugg_labels = []

    def check_queue():
        try:
            data = ui_queue.get_nowait()
            if data:
                suggs, sel_idx = data

                for lbl in sugg_labels:
                    lbl.destroy()
                sugg_labels.clear()

                for i, s in enumerate(suggs):
                    bg_color = '#094771' if i == sel_idx else '#1e1e1e'
                    fg_color = '#ffffff' if i == sel_idx else '#cccccc'
                    lbl = tk.Label(sugg_frame, text=s,
                                   font=("Segoe UI", 12, "bold" if i == sel_idx else "normal"),
                                   bg=bg_color, fg=fg_color, padx=8, pady=2)
                    lbl.pack(side=tk.LEFT)

                    if i < len(suggs) - 1:
                        sep = tk.Label(sugg_frame, text="|",
                                       font=("Segoe UI", 11), bg='#1e1e1e', fg='#555555')
                        sep.pack(side=tk.LEFT)
                        sugg_labels.append(sep)

                    sugg_labels.append(lbl)

                pos = get_active_monitor_top_center()
                if pos:
                    x, y = pos
                    root.update_idletasks()
                    w = root.winfo_reqwidth()
                    root.geometry(f"+{x - w//2}+{y}")
                root.attributes("-alpha", 0.95)
            else:
                root.attributes("-alpha", 0.0)
        except queue.Empty:
            pass
        root.after(50, check_queue)

    root.after(50, check_queue)
    root.mainloop()


if __name__ == "__main__":
    t = threading.Thread(target=worker_thread, daemon=True)
    t.start()

    ui_t = threading.Thread(target=ui_thread, daemon=True)
    ui_t.start()

    listener_thread = threading.Thread(target=start_listening, daemon=True)
    listener_thread.start()

    print("LangFix Started! Look for the icon in the System Tray (bottom right).")
    try:
        setup_tray()
    except KeyboardInterrupt:
        pass
