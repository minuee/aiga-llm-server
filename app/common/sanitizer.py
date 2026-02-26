# app/common/sanitizer.py
import re
from kiwipiepy import Kiwi
from ..common.logger import logger
from ..common.sensitive_words import MEDICAL_REWRITE

# Initialize Kiwi tokenizer
try:
    kiwi = Kiwi()
except Exception as e:
    logger.error(f"Failed to initialize Kiwi: {e}")
    kiwi = None

# Words to preserve during sanitization for location context
LOCATION_KEYWORDS = {"근처", "주변", "가깝다", "인근", "부근", "근방", "옆", "가까이", "가까운데", "여기"}

def _sanitize_with_kiwi(text: str) -> str:
    """
    (Internal) Aggressively sanitizes the input text by extracting nouns, verbs, adjectives
    and key location words to simplify the query for content filters.
    """
    if not kiwi:
        logger.error("Kiwi not initialized. Returning original text.")
        return text
    
    try:
        # Analyze the text and get the list of tokens from the first sentence result
        tokens = kiwi.analyze(text)[0][0]
        
        # Preserve nouns, verbs, adjectives AND location keywords based on form or lemma
        preserved_words = []
        for t in tokens:
            if t.tag.startswith(("NN", "VV", "VA")) or t.form in LOCATION_KEYWORDS or t.lemma in LOCATION_KEYWORDS:
                preserved_words.append(t.form)

        nouns = " ".join(preserved_words)

        # If no words are preserved, return the original text to avoid empty messages
        if not nouns:
            logger.warning(f"Kiwi sanitization resulted in an empty string for '{text}'. Returning original text.")
            return text

        logger.info(f"Kiwi sanitization: '{text}' -> '{nouns}'")
        return nouns
    except Exception as e:
        logger.error(f"Kiwi sanitization failed for text '{text}': {e}")
        return text # Fallback to original text on error

def sanitize_prompt(text: str) -> str:
    """
    Applies a conditional 2-stage sanitization to bypass content filters
    by detecting symptom descriptions and sensitive keywords.
    """
    # 1. Check for symptom lemmas using Kiwi for more robust detection
    found_symptom_lemma = False
    if kiwi:
        # Common symptom-related verb/adjective stems
        SYMPTOM_STEMS = {
            "아프다", "저리다", "쑤시다", "결리다", "따갑다", "쓰리다", "가렵다", "붓다",
            "토하다", "답답하다", "어지럽다", "메스껍다", "울렁거리다", "더부룩하다"
        }
        try:
            tokens = kiwi.tokenize(text)
            for token in tokens:
                # Check if the lemma of a verb (VV) or adjective (VA) is a known symptom
                if token.tag.startswith(('VV', 'VA')) and token.lemma in SYMPTOM_STEMS:
                    found_symptom_lemma = True
                    logger.info(f"Symptom lemma '{token.lemma}' found in '{text}'")
                    break
        except Exception as e:
            logger.error(f"Kiwi tokenization failed during symptom check for '{text}': {e}")

    # 2. Fallback/secondary check for keywords in the dictionary
    found_sensitive_in_dict = any(word in text for word in MEDICAL_REWRITE.keys())

    # 3. A query is sensitive if either check passes
    is_sensitive_query = found_symptom_lemma or found_sensitive_in_dict

    # 4. Rewrite the text using the MEDICAL_REWRITE dictionary regardless
    rewritten_text = text
    for old, new in MEDICAL_REWRITE.items():
        rewritten_text = rewritten_text.replace(old, new)

    final_sanitized_text = ""
    # 5. Conditionally apply aggressive sanitization on the rewritten text
    if is_sensitive_query:
        logger.info(f"Sensitive query detected in '{text}'. Applying full Kiwi sanitization.")
        final_sanitized_text = _sanitize_with_kiwi(rewritten_text)
    else:
        # 6. Return only the rewritten text if not sensitive
        logger.info(f"No sensitive symptoms/keywords detected. Standard rewrite applied: '{text}' -> '{rewritten_text}'")
        final_sanitized_text = rewritten_text
    
    logger.info(f"Sanitization result: Original='{text}', Rewritten='{rewritten_text}', Final='{final_sanitized_text}'")
    return final_sanitized_text
