import re
import unicodedata
from typing import Optional

def normalize_phone_number(raw_phone: str) -> Optional[str]:
    """
    Bulletproof Iranian phone number normalization algorithm:
    - Step 1: Convert all Persian/Arabic digits (۰-۹ / ٠-٩) to English digits (0-9).
    - Step 2: Strip all non-numeric characters (spaces, dashes, parens, '+', etc.).
    - Step 3: Standardize Iranian mobile numbers:
      * 00989... -> 989...
      * 09...    -> 989...
      * 9... (10 digits) -> 989...
    - Step 4: Validate against Regex ^989\d{9}$ (must be exactly 12 digits). Return None if invalid.
    """
    if not raw_phone:
        return None
    
    # Step 1: Normalize unicode (NFKC) and convert Persian/Arabic digits to English
    normalized_unicode = unicodedata.normalize('NFKC', str(raw_phone))
    
    persian_arabic_digits = {
        '۰': '0', '۱': '1', '۲': '2', '۳': '3', '۴': '4',
        '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
        '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
    }
    
    converted_digits = "".join(persian_arabic_digits.get(ch, ch) for ch in normalized_unicode)
    
    # Step 2: Strip all non-numeric characters
    numeric_only = "".join([c for c in converted_digits if c.isdigit()])
    
    if not numeric_only:
        return None
    
    # Step 3: Standardize Iranian mobile numbers
    # 00989XXXXXXXXX (14 digits) or 989XXXXXXXXX (12 digits) or 09XXXXXXXXX (11 digits) or 9XXXXXXXXX (10 digits)
    if numeric_only.startswith("0098"):
        numeric_only = numeric_only[2:] # 0989...
    
    if numeric_only.startswith("98"):
        # already 989... if 12 digits
        pass
    elif numeric_only.startswith("09"):
        numeric_only = "98" + numeric_only[1:] # 989...
    elif len(numeric_only) == 10 and numeric_only.startswith("9"):
        numeric_only = "98" + numeric_only # 989...
        
    # Step 4: Validate against Regex ^989\d{9}$ (must be exactly 12 digits starting with 989)
    if re.fullmatch(r"^989\d{9}$", numeric_only):
        return numeric_only
        
    return None
