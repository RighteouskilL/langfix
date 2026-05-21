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
from autocorrect import is_gibberish_english, is_gibberish_thai, fix_text_manual, valid_thai_words_set, add_to_ignore_list, IGNORE_FILE, simulate_thai_output
import os
import subprocess

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

is_simulating = False
current_word_buffer = []
last_correction = None

def simulate_backspaces(count):
    global is_simulating
    is_simulating = True
    for _ in range(count):
        controller.press(keyboard.Key.backspace)
        controller.release(keyboard.Key.backspace)
        time.sleep(0.01)
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
            
            if trigger == "SPACE":
                simulate_type(" ")
            elif trigger == "ENTER":
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
            
            if trigger == "SPACE":
                simulate_type(" ")
            elif trigger == "ENTER":
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
    global current_word_buffer
    while True:
        try:
            item = key_queue.get(timeout=1.2) # Reverted back to 1.2s since no autocomplete UI
            if item == "SPACE":
                process_buffer(current_word_buffer, trigger="SPACE")
                current_word_buffer.clear()
            elif item == "ENTER":
                process_buffer(current_word_buffer, trigger="ENTER")
                current_word_buffer.clear()
            elif item == "BACKSPACE":
                if current_word_buffer:
                    current_word_buffer.pop()
            elif item == "CLEAR":
                current_word_buffer.clear()
            else:
                current_word_buffer.append(item)
        except queue.Empty:
            if current_word_buffer:
                process_buffer(current_word_buffer, trigger="TIMEOUT")
                current_word_buffer.clear()

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
    global is_simulating
    if is_simulating: return
    try:
        if hasattr(key, 'char') and key.char is not None:
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
    global is_simulating
    if is_simulating:
        # data.flags & 0x10 (LLKHF_INJECTED) check if event is injected by us.
        # If it's a physical key (not injected) while we are simulating, block it!
        # This prevents accidental shortcuts like Ctrl+N if user types 'n' while we simulate Ctrl+V.
        if not (data.flags & 0x10):
            return False 
    return True

def start_listening():
    h = keyboard.GlobalHotKeys({
        '<ctrl>+<shift>+<space>': on_manual_fix,
        '<ctrl>+<shift>+z': lambda: undo_last_correction(add_to_ignore=False),
        '<ctrl>+<shift>+i': lambda: undo_last_correction(add_to_ignore=True)
    })
    h.start()
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

if __name__ == "__main__":
    t = threading.Thread(target=worker_thread, daemon=True)
    t.start()
    
    listener_thread = threading.Thread(target=start_listening, daemon=True)
    listener_thread.start()
    
    print("LangFix Started! Look for the icon in the System Tray (bottom right).")
    try:
        setup_tray()
    except KeyboardInterrupt:
        pass
