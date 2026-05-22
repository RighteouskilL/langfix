import time
import threading
import queue
import ctypes
import pyperclip
from pynput import keyboard
import pystray
from PIL import Image, ImageDraw
import logging

# Local modules
from autocorrect import is_gibberish_english, is_gibberish_thai, fix_text_manual, valid_thai_words_set, add_to_ignore_list, IGNORE_FILE, simulate_thai_output, get_suggestions
import os
import subprocess
import tkinter as tk
from ctypes import wintypes

logging.basicConfig(filename='langfix.log', level=logging.DEBUG, 
                    format='%(asctime)s - %(levelname)s - %(message)s')

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

def get_active_monitor_bottom_center():
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
        y = work_rect.top + 20 # Top Center
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
    
    # Release common modifiers just in case the user is still holding them (e.g. Shift for typing 'ั' or Tab)
    controller.release(keyboard.Key.shift)
    controller.release(keyboard.Key.ctrl)
    controller.release(keyboard.Key.alt)
    
    time.sleep(0.15) # Wait longer (150ms) to ensure the text editor has finished rendering the last typed character
    for _ in range(count):
        controller.press(keyboard.Key.backspace)
        time.sleep(0.02) # Hold the backspace key for 20ms so the OS registers it properly
        controller.release(keyboard.Key.backspace)
        time.sleep(0.04) # Wait 40ms before the next backspace
    time.sleep(0.05)

def simulate_type(text):
    global is_simulating
    is_simulating = True
    pyperclip.copy(text)
    time.sleep(0.05) 
    
    # Paste using virtual key code to avoid layout issues
    v_key = keyboard.KeyCode(vk=0x56)
    controller.press(keyboard.Key.ctrl)
    controller.press(v_key)
    controller.release(v_key)
    controller.release(keyboard.Key.ctrl)
    time.sleep(0.05)


def process_buffer(buffer, trigger="TIMEOUT"):
    global is_simulating, last_correction
    text = "".join(buffer)
    if len(text) < 2: return 
    
    layout = get_current_keyboard_layout()
    
    if layout == LANG_THAI:
        # If typing on Thai layout, simulate what actually got rendered on screen
        actual_rendered_text = simulate_thai_output(text)
        backspace_count = len(actual_rendered_text)
    else:
        # English layout has no dead keys
        backspace_count = len(text)
        
    if trigger in ["SPACE", "ENTER"]:
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
            
            switch_to_thai()
            wait_for_layout_change(LANG_THAI)
            simulate_backspaces(backspace_count)
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
            last_correction = {
                'original_text': text,
                'corrected_text': eng_text,
                'original_layout': layout,
                'new_layout': LANG_ENG,
                'trigger': trigger
            }
            switch_to_eng()
            wait_for_layout_change(LANG_ENG)
            simulate_backspaces(backspace_count)
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
    curr_layout = last_correction['new_layout']
    trigger = last_correction['trigger']
    
    if add_to_ignore:
        add_to_ignore_list(orig)
        print(f"Added '{orig}' to ignore list.")
    
    # Switch back to the language it is currently in to delete
    # Wait, it is already in curr_layout.
    
    backspace_count = len(corr)
    if trigger in ["SPACE", "ENTER"]:
        backspace_count += 1
        
    simulate_backspaces(backspace_count)
    
    if orig_layout == LANG_ENG:
        switch_to_eng()
        wait_for_layout_change(LANG_ENG)
    else:
        switch_to_thai()
        wait_for_layout_change(LANG_THAI)
        
    simulate_type(orig)
    if trigger == "SPACE":
        simulate_type(" ")
    elif trigger == "ENTER":
        controller.press(keyboard.Key.enter)
        controller.release(keyboard.Key.enter)
        
    time.sleep(0.1)
    with key_queue.mutex:
        key_queue.queue.clear()
    is_simulating = False
    last_correction = None

def worker_thread():
    global current_word_buffer, current_suggestions, selected_suggestion_index
    while True:
        try:
            timeout_val = 10.0 if current_suggestions else 1.2
            item = key_queue.get(timeout=timeout_val)
            if item == "SPACE":
                ui_queue.put(None) # Hide UI
                process_buffer(current_word_buffer, trigger="SPACE")
                current_word_buffer.clear()
            elif item == "ENTER":
                ui_queue.put(None)
                process_buffer(current_word_buffer, trigger="ENTER")
                current_word_buffer.clear()
            elif item == "BACKSPACE":
                if current_word_buffer:
                    current_word_buffer.pop()
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
                            ui_queue.put(None)
            elif item == "CLEAR":
                ui_queue.put(None)
                current_word_buffer.clear()
                current_suggestions = []
                selected_suggestion_index = 0
            elif item.startswith("ACCEPT_"):
                idx = int(item.split("_")[1])
                if current_suggestions and idx < len(current_suggestions):
                    accept_suggestion(idx)
            elif item == "GIB_SPACE":
                buf_copy = list(current_word_buffer)
                current_word_buffer.clear()
                current_suggestions = []
                ui_queue.put(None)
                process_buffer(buf_copy, trigger="GIB_SPACE")
            elif item == "GIB_ENTER":
                buf_copy = list(current_word_buffer)
                current_word_buffer.clear()
                current_suggestions = []
                ui_queue.put(None)
                process_buffer(buf_copy, trigger="GIB_ENTER")
            elif item == "UNDO_Z":
                undo_last_correction(add_to_ignore=False)
            elif item == "UNDO_I":
                undo_last_correction(add_to_ignore=True)
            elif item == "MANUAL_FIX":
                on_manual_fix()
            else:
                current_word_buffer.append(item)
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
        except queue.Empty:
            if current_word_buffer:
                ui_queue.put(None)
                process_buffer(current_word_buffer, trigger="TIMEOUT")
                current_word_buffer.clear()
                current_suggestions = []

def accept_suggestion(idx, append_char=None):
    global is_simulating, last_correction, current_word_buffer, current_suggestions, selected_suggestion_index
    if not current_suggestions or idx >= len(current_suggestions): return
    
    suggested_word = current_suggestions[idx]
    raw_prefix = "".join(current_word_buffer)
    
    is_gib, translated = is_gibberish_english(raw_prefix)
    
    ui_queue.put(None)
    
    layout = get_current_keyboard_layout()
    target_layout = LANG_THAI if is_gib else layout # Assuming suggestions are Thai
    
    if layout == LANG_THAI:
        actual_rendered_text = simulate_thai_output(raw_prefix)
        backspace_count = len(actual_rendered_text)
    else:
        backspace_count = len(raw_prefix)
        
    last_correction = {
        'original_text': raw_prefix,
        'corrected_text': suggested_word,
        'original_layout': layout,
        'new_layout': target_layout,
        'trigger': 'TAB'
    }
    
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
    if is_simulating: return
    try:
        if key == keyboard.Key.tab and current_suggestions:
            logging.debug("Tab intercepted in on_press (fallback)")
            key_queue.put("TAB")
            return
        elif hasattr(key, 'char') and key.char is not None:
            key_queue.put(key.char)
        elif key == keyboard.Key.space:
            key_queue.put("SPACE")
        elif key == keyboard.Key.enter:
            key_queue.put("ENTER")
        elif key == keyboard.Key.backspace:
            key_queue.put("BACKSPACE")
        elif key == keyboard.Key.esc:
            key_queue.put("CLEAR")
    except Exception as e:
        logging.error(f"Error in on_press: {e}")

def win32_event_filter(msg, data):
    global is_simulating, current_suggestions, selected_suggestion_index
    if is_simulating:
        # data.flags & 0x10 (LLKHF_INJECTED) check if event is injected by us.
        # If it's a physical key (not injected) while we are simulating, block it!
        # This prevents accidental shortcuts like Ctrl+N if user types 'n' while we simulate Ctrl+V.
        if not (data.flags & 0x10):
            return False 
            
    # Block physical keys if suggestions are showing
    if msg in (256, 257, 260, 261): # WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP
        if current_suggestions:
            # Vim-style Ctrl navigation and Ctrl+Space to ACCEPT
            ctrl_pressed = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
            
            if ctrl_pressed:
                # Navigation: H, K, P (Prev)
                if data.vkCode in (0x48, 0x4B, 0x50): 
                    if msg in (256, 260):
                        selected_suggestion_index = (selected_suggestion_index - 1) % len(current_suggestions)
                        ui_queue.put((current_suggestions, selected_suggestion_index))
                    return False
                # Navigation: L, J, N (Next)
                elif data.vkCode in (0x4C, 0x4A, 0x4E):
                    if msg in (256, 260):
                        selected_suggestion_index = (selected_suggestion_index + 1) % len(current_suggestions)
                        ui_queue.put((current_suggestions, selected_suggestion_index))
                    return False
                # Accept: Ctrl + Space
                elif data.vkCode == 0x20:
                    if msg in (256, 260):
                        idx = 1 if selected_suggestion_index == 0 and len(current_suggestions) > 1 else selected_suggestion_index
                        key_queue.put(f"ACCEPT_{idx}")
                    return False
                    
            if data.vkCode == 0x1B: # VK_ESCAPE (Clear suggestion)
                if msg in (256, 260):
                    key_queue.put("CLEAR")
                return False
                
        # Gibberish interception for Space and Enter to prevent race conditions and misfiring chat sends
        if current_word_buffer and data.vkCode in (0x20, 0x0D):
            raw_prefix = "".join(current_word_buffer)
            layout = get_current_keyboard_layout()
            is_gib = False
            if layout == LANG_ENG:
                is_gib, _ = is_gibberish_english(raw_prefix)
            else:
                is_gib, _ = is_gibberish_thai(raw_prefix)
                
            if is_gib:
                if data.vkCode == 0x20:
                    if msg in (256, 260): key_queue.put("GIB_SPACE")
                    return False
                elif data.vkCode == 0x0D:
                    if msg in (256, 260): key_queue.put("GIB_ENTER")
                    return False
                    
        # Global hotkeys (Layout independent)
        ctrl_pressed = ctypes.windll.user32.GetAsyncKeyState(0x11) & 0x8000
        shift_pressed = ctypes.windll.user32.GetAsyncKeyState(0x10) & 0x8000
        
        if ctrl_pressed and shift_pressed:
            if data.vkCode == 0x5A: # Z
                if msg in (256, 260): key_queue.put("UNDO_Z")
                return False
            elif data.vkCode == 0x49: # I
                if msg in (256, 260): key_queue.put("UNDO_I")
                return False
            elif data.vkCode == 0x20: # Space
                if msg in (256, 260): key_queue.put("MANUAL_FIX")
                return False
            
    return True

def start_listening():
    try:
        with keyboard.Listener(on_press=on_press, win32_event_filter=win32_event_filter) as listener:
            listener.join()
    except KeyboardInterrupt:
        pass

def edit_ignore_list(icon, item):
    if not os.path.exists(IGNORE_FILE):
        with open(IGNORE_FILE, 'w', encoding='utf-8') as f:
            f.write("# Add words to ignore (one per line)\n")
    # Open in notepad
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
    
    # Try to make it transparent/invisible when empty
    root.attributes("-alpha", 0.0)
    
    main_frame = tk.Frame(root, bg='#1e1e1e', padx=8, pady=4, highlightbackground="#3e3e42", highlightthickness=1)
    main_frame.pack()
    
    sugg_frame = tk.Frame(main_frame, bg='#1e1e1e')
    sugg_frame.pack(side=tk.LEFT)
    
    tip_label = tk.Label(main_frame, text=" Ctrl+H/J/K/L/N/P: เลื่อน  |  Ctrl+Space: ยืนยัน  |  Esc: ปิด", font=("Segoe UI", 8), bg='#1e1e1e', fg='#666666')
    tip_label.pack(side=tk.RIGHT, padx=(10, 0))
    
    sugg_labels = []
    
    def check_queue():
        try:
            data = ui_queue.get_nowait()
            if data:
                suggs, sel_idx = data
                
                # Clear old labels
                for lbl in sugg_labels:
                    lbl.destroy()
                sugg_labels.clear()
                
                # Create new labels horizontally
                for i, s in enumerate(suggs):
                    bg_color = '#094771' if i == sel_idx else '#1e1e1e'
                    fg_color = '#ffffff' if i == sel_idx else '#cccccc'
                    
                    lbl = tk.Label(sugg_frame, text=s, font=("Segoe UI", 12, "bold" if i==sel_idx else "normal"), bg=bg_color, fg=fg_color, padx=8, pady=2)
                    lbl.pack(side=tk.LEFT)
                    
                    if i < len(suggs) - 1:
                        sep = tk.Label(sugg_frame, text="|", font=("Segoe UI", 11), bg='#1e1e1e', fg='#555555')
                        sep.pack(side=tk.LEFT)
                        sugg_labels.append(sep)
                        
                    sugg_labels.append(lbl)
                
                # Position on active monitor
                pos = get_active_monitor_bottom_center()
                if pos:
                    x, y = pos
                    root.update_idletasks() # Get req width
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
