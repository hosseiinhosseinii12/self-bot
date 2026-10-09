"""Font maps for base names and clock digits, with custom font support."""
from .config import CONFIG

NAME_FONTS = {
    "normal":    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "italic":    "𝘈𝘉𝘊𝘋𝘌𝘍𝘎𝘏𝘐𝘑𝘒𝘓𝘔𝘕𝘖𝘗𝘘𝘙𝘚𝘛𝘜𝘝𝘞𝘟𝘠𝘡",
    "bold":      "𝐀𝐁𝐂𝐃𝐄𝐅𝐆𝐇𝐈𝐉𝐊𝐋𝐌𝐍𝐎𝐏𝐐𝐑𝐒𝐓𝐔𝐕𝐖𝐗𝐘𝐙",
    "script":    "𝒜ℬ𝒞𝒟ℰℱ𝒢ℋℐ𝒥𝒦ℒℳ𝒩𝒪𝒫𝒬ℛ𝒮𝒯𝒰𝒱𝒲𝒳𝒴𝒵",
    "fraktur":   "𝔄𝔅ℭ𝔇𝔈𝔉𝔊ℌℑ𝔍𝔎𝔏𝔐𝔑𝔒𝔓𝔔ℜ𝔖𝔗𝔘𝔙𝔚𝔛𝔜ℨ",
    "double":    "𝔸𝔹ℂ𝔻𝔼𝔽𝔾ℍ𝕀𝕁𝕂𝕃𝕄ℕ𝕆ℙℚℝ𝕊𝕋𝕌𝕍𝕎𝕏𝕐ℤ",
    "sans":      "𝖠𝖡𝖢𝖣𝖤𝖥𝖦𝖧𝖨𝖩𝖪𝖫𝖬𝖭𝖮𝖯𝖰𝖱𝖲𝖳𝖴𝖵𝖶𝖷𝖸𝖹",
    "sans-bold": "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭",
}

DIGIT_FONTS = {
    "double":      "𝟘𝟙𝟚𝟛𝟜𝟝𝟞𝟟𝟠𝟡",
    "bold":        "𝟎𝟏𝟐𝟑𝟒𝟓𝟔𝟕𝟖𝟗",
    "sans":        "𝟢𝟣𝟤𝟥𝟦𝟧𝟨𝟩𝟪𝟫",
    "sans-bold":   "𝟬𝟭𝟮𝟯𝟰𝟱𝟲𝟳𝟴𝟵",
    "mono":        "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿",
    "superscript": "⁰¹²³⁴⁵⁶⁷⁸⁹",
    "subscript":   "₀₁₂₃₄₅₆₇₈₉",
    "circled":     "⓪①②③④⑤⑥⑦⑧⑨",
    "circled-neg": "⓿❶❷❸❹❺❻❼❽❾",
    "paren":       "0⒈⒉⒊⒋⒌⒍⒎⒏⒐",
    "fullwidth":   "０１２３４５６７８９",
    "arabic":      "٠١٢٣٤٥٦٧٨٩",
    "arabic-ext":  "۰۱۲۳۴۵۶۷۸۹",
    "devanagari":  "०१२३४५६७८९",
    "bengali":     "০১২৩৪৫৬৭৮৯",
    "thai":        "๐๑๒๓๔๕๖๗๘๙",
}


def apply_name_font(text: str, font_name: str, custom: str = "") -> str:
    """Apply a name font. `custom` is a string of 26 characters (A–Z).

    Priority:
      1. explicit `custom` argument (if 26+ chars)
      2. CONFIG['custom_name_font'] (if 26+ chars)
      3. built-in mapping from NAME_FONTS
    """
    custom = custom or CONFIG.get("custom_name_font", "") or ""
    if len(custom) >= 26:
        out = []
        for ch in text:
            if ch.isalpha():
                idx = ord(ch.upper()) - 65
                out.append(custom[idx] if 0 <= idx < 26 else ch)
            else:
                out.append(ch)
        return "".join(out)
    mapping = NAME_FONTS.get(font_name, NAME_FONTS["normal"])
    out = []
    for ch in text:
        if ch.isalpha():
            idx = ord(ch.upper()) - 65
            out.append(mapping[idx] if 0 <= idx < len(mapping) else ch)
        else:
            out.append(ch)
    return "".join(out)


def apply_clock_font(text: str, font_name: str, custom: str = "") -> str:
    """Apply a clock digit font. `custom` is a string of 10 characters (0–9).

    Priority:
      1. explicit `custom` argument (if 10+ chars)
      2. CONFIG['custom_clock_font'] (if 10+ chars)
      3. built-in mapping from DIGIT_FONTS
    """
    custom = custom or CONFIG.get("custom_clock_font", "") or ""
    if len(custom) >= 10:
        return "".join(custom[int(c)] if c.isdigit() else c for c in text)
    mapping = DIGIT_FONTS.get(font_name, DIGIT_FONTS["double"])
    return "".join(mapping[int(c)] if c.isdigit() else c for c in text)