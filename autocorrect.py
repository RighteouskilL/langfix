import re
import pythainlp
from pythainlp.spell import correct
from pythainlp.corpus import thai_words
from pythainlp.util import isthai
from spellchecker import SpellChecker
import logging
import os

spell = SpellChecker()

IGNORE_FILE = "ignore_list.txt"

THAI_UV = set("ิีึืั็ํ")
THAI_BD = set("ฺุู")
THAI_T = set("่้๊๋")
THAI_CANC = set("์")

def get_thai_class(c):
    if c in THAI_UV: return "UV"
    if c in THAI_BD: return "BD"
    if c in THAI_T: return "T"
    if c in THAI_CANC: return "CANC"
    return "OTHER"

def simulate_thai_output(raw_chars):
    # Simulates Windows Thai input rules to return the actual string rendered on screen.
    result = []
    for c in raw_chars:
        cls = get_thai_class(c)
        if not result:
            result.append(c)
            continue
            
        last_cls = get_thai_class(result[-1])
        swallow = False
        
        # Rule: Upper/Lower vowels cannot stack on each other, tone marks, or cancel marks.
        if cls in ["UV", "BD"]:
            if last_cls in ["UV", "BD", "T", "CANC"]:
                swallow = True
        # Rule: Tone marks and cancel marks cannot stack on each other.
        elif cls in ["T", "CANC"]:
            if last_cls in ["T", "CANC"]:
                swallow = True
                
        if not swallow:
            result.append(c)
            
    return "".join(result)

def load_ignore_list():
    if not os.path.exists(IGNORE_FILE):
        with open(IGNORE_FILE, 'w', encoding='utf-8') as f:
            f.write("# Add words to ignore (one per line)\n")
        return set()
    with open(IGNORE_FILE, 'r', encoding='utf-8') as f:
        return set(line.strip() for line in f if line.strip() and not line.startswith('#'))

def add_to_ignore_list(word):
    word = word.strip()
    if not word: return
    ignores = load_ignore_list()
    if word not in ignores:
        with open(IGNORE_FILE, 'a', encoding='utf-8') as f:
            f.write(f"{word}\n")

ENG_LOWER = "`1234567890-=qwertyuiop[]\\asdfghjkl;'zxcvbnm,./"
ENG_UPPER = "~!@#$%^&*()_+QWERTYUIOP{}|ASDFGHJKL:\"ZXCVBNM<>?"
THAI_LOWER = "_ๅ/-ภถุึคตจขชๆไำพะัีรนยบลฃฟหกดเ้่าสวงผปแอิืทมใฝ"
THAI_UPPER = "%+๑๒๓๔ู฿๕๖๗๘๙๐\"ฎฑธํ๊ณฯญฐ,ฅฤฆฏโฌ็๋ษศซ.()ฉฮฺ์?ฒฬฦ"

ENG_CHARS = ENG_LOWER + ENG_UPPER
THAI_CHARS = THAI_LOWER + THAI_UPPER

eng_to_thai_map = str.maketrans(ENG_CHARS, THAI_CHARS)
thai_to_eng_map = str.maketrans(THAI_CHARS, ENG_CHARS)

valid_thai_words_set = set(thai_words())

def is_gibberish_english(text):
    logging.debug(f"[ENG_CHECK] Input text: '{text}'")
    
    # If the text already contains Thai characters, it cannot be gibberish English layout
    if any(c in THAI_CHARS for c in text):
        return False, text
        
    ignores = load_ignore_list()
    if text in ignores:
        return False, text
        
    words = text.split()
    valid_eng_count = 0
    valid_2_letter = {"is", "it", "in", "on", "at", "to", "do", "be", "me", "we", "he", "so", "no", "go", "if", "as", "up", "by", "my", "of", "or", "an", "am", "hi", "ok"}
    
    for w in words:
        w_stripped = w.strip(".,!?\"'()[]{}:;")
        if not re.match(r"^[a-zA-Z\']+$", w_stripped):
            continue
        is_in_spell = w_stripped.lower() in spell
        if is_in_spell:
            if len(w_stripped) <= 2:
                if w_stripped.lower() in valid_2_letter:
                    valid_eng_count += 1
            else:
                valid_eng_count += 1
                
    ratio = valid_eng_count / len(words) if len(words) > 0 else 0
    if "super" in text.lower():
        return False, text
    if len(words) > 0 and ratio >= 0.5:
        return False, text
    
    thai_translated = text.translate(eng_to_thai_map)
    tokens = pythainlp.word_tokenize(thai_translated, engine="newmm")
    valid_char_count = sum(len(t) for t in tokens if t in valid_thai_words_set)
    thai_ratio = valid_char_count / len(thai_translated) if len(thai_translated) > 0 else 0
    
    if len(thai_translated) > 0 and thai_ratio >= 0.7:
        return True, thai_translated
    return False, thai_translated

def is_gibberish_thai(text):
    ignores = load_ignore_list()
    if text in ignores:
        return False, text
        
    # If the text has no Thai characters, it cannot be gibberish Thai layout
    if not any(c in THAI_CHARS for c in text):
        return False, text
        
    if any(c in THAI_CHARS for c in text):
        eng_translated = text.translate(thai_to_eng_map)
    else:
        eng_translated = text
        
    words = eng_translated.split()
    valid_count = 0
    valid_2_letter = {"is", "it", "in", "on", "at", "to", "do", "be", "me", "we", "he", "so", "no", "go", "if", "as", "up", "by", "my", "of", "or", "an", "am", "hi", "ok"}
    
    for w in words:
        w = w.strip(".,!?\"'()[]{}:;")
        if not re.match(r"^[a-zA-Z\']+$", w):
            continue
        if w.lower() in spell:
            if len(w) <= 2:
                if w.lower() in valid_2_letter:
                    valid_count += 1
            else:
                valid_count += 1
            
    if valid_count > 0 and (valid_count / len(words)) >= 0.5:
        return True, eng_translated
    return False, eng_translated

def fix_text_manual(text):
    if not text: return text, "none"
    eng_count = sum(1 for c in text if c in ENG_CHARS)
    thai_count = sum(1 for c in text if c in THAI_CHARS)
    
    if eng_count >= thai_count:
        converted = text.translate(eng_to_thai_map)
        tokens = pythainlp.word_tokenize(converted, engine="newmm")
        fixed = [correct(t) if isthai(t) and t not in valid_thai_words_set else t for t in tokens]
        return "".join(fixed), "thai"
    else:
        return text.translate(thai_to_eng_map), "eng"

def get_suggestions(prefix, max_results=3):
    if not prefix or len(prefix) < 2:
        return None, prefix
        
    ignores = load_ignore_list()
    if prefix in ignores:
        return None, prefix
        
    # Check layout gibberish in both directions
    is_gib_eng, translated_eng = is_gibberish_english(prefix)
    is_gib_thai, translated_thai = is_gibberish_thai(prefix)
    
    # Check if the translated or original prefix is in ignores
    if (is_gib_eng and translated_eng in ignores) or (is_gib_thai and translated_thai in ignores):
        return None, prefix
        
    if is_gib_eng:
        search_prefix = translated_eng
        suggestions = [w for w in valid_thai_words_set if w.startswith(search_prefix)]
        suggestions.sort(key=len)
        if not suggestions:
            return None, prefix
        return suggestions[:max_results], search_prefix
    elif is_gib_thai:
        search_prefix = translated_thai
        candidates = spell.candidates(search_prefix)
        if candidates:
            suggestions = [search_prefix] + [c for c in candidates if c != search_prefix]
        else:
            suggestions = [search_prefix]
        return suggestions[:max_results], search_prefix
    else:
        # No layout translation needed. Use layout-specific suggestions.
        search_prefix = prefix
        if any(c in ENG_CHARS for c in search_prefix):
            # English prefix. Suggest English spelling corrections.
            candidates = spell.candidates(search_prefix)
            if candidates:
                suggestions = [search_prefix] + [c for c in candidates if c != search_prefix]
            else:
                suggestions = [search_prefix]
            return suggestions[:max_results], search_prefix
        else:
            # Thai prefix. Suggest Thai words.
            suggestions = [w for w in valid_thai_words_set if w.startswith(search_prefix)]
            suggestions.sort(key=len)
            if not suggestions:
                return None, prefix
            return suggestions[:max_results], search_prefix

