"""Register a CJK font for the PDF. An embedded (subset) TrueType font renders identically in every PDF
viewer; the built-in CID font is a last resort that relies on the viewer's own Asian font pack."""

import logging
from functools import lru_cache
from pathlib import Path

from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont

log = logging.getLogger(__name__)

FONT_NAME = "CJK"
FONT_BOLD = "CJK-Bold"
# (regular, bold) candidates: Docker image (fonts-wqy-microhei), Windows dev machines.
CANDIDATES = (
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", None),
    ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", None),
    ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyhbd.ttc"),
    ("C:/Windows/Fonts/simhei.ttf", None),
)


def _load(name: str, path: str) -> bool:
    try:
        pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
        return True
    except Exception as exc:  # unreadable/unsupported font file: try the next candidate
        log.warning("Could not load PDF font %s: %s", path, exc)
        return False


@lru_cache
def register_fonts(custom_path: str = "") -> tuple[str, str]:
    """Register and return (regular, bold) font names."""
    candidates = ([(custom_path, None)] if custom_path else []) + list(CANDIDATES)
    for regular, bold in candidates:
        if regular and Path(regular).is_file() and _load(FONT_NAME, regular):
            bold_name = FONT_BOLD if bold and Path(bold).is_file() and _load(FONT_BOLD, bold) else FONT_NAME
            addMapping(FONT_NAME, 0, 0, FONT_NAME)
            addMapping(FONT_NAME, 1, 0, bold_name)
            addMapping(FONT_NAME, 0, 1, FONT_NAME)
            addMapping(FONT_NAME, 1, 1, bold_name)
            return FONT_NAME, bold_name
    log.warning("No CJK TrueType font found; falling back to the non-embedded STSong-Light CID font")
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    return "STSong-Light", "STSong-Light"
