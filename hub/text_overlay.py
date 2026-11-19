"""Guaranteed text: render literal strings onto the generated image with a real font.

When ``text_overlay`` is enabled for a job, the model is asked for an image with NO
text (clean reserved space); the Hub then draws the exact requested strings using the
bundled OFL Vazirmatn font and Arabic shaping. This is the only path that can *guarantee*
character-perfect text — model-generated glyphs can never be guaranteed.
"""
import io
import pathlib
import re

from PIL import Image, ImageDraw, ImageFont

import arabic_reshaper
from bidi.algorithm import get_display

FONT_PATH = pathlib.Path(__file__).resolve().parent.parent / "assets" / "fonts" / "Vazirmatn-Variable.ttf"
FONT_SIZE = 48
MAX_LITERALS = 8
MAX_LITERAL_CHARS = 200

_QUOTED = re.compile(r"[«\"'“]([^«»\"'“\”\n]{2,200})[»\"'”]")
_LABEL = re.compile(r"(?:^|\n)\s*(headline|badge|footer|title|text|label|heading|کد|عنوان|متن|پانویس|برچسب)\s*[:：]\s*([^\n]{2,200})", re.IGNORECASE)
_STRIP = "«»\"'“”„"


def shaped(text: str) -> str:
    """Shape Persian/Arabic + apply bidi so RTL renders correctly in Pillow."""
    return get_display(arabic_reshaper.reshape(text))


def extract_literals(prompt: str) -> list[str]:
    """Pull explicit text the operator asked for out of the prompt.

    Quoted spans («...», "..." or '...') and simple ``Label: value`` lines are treated as
    literal strings that must appear verbatim. Text the model invents is not covered.
    """
    found: list[str] = []
    for match in _QUOTED.finditer(prompt or ""):
        value = match.group(1).strip().strip(_STRIP)
        if value and value not in found and len(value) <= MAX_LITERAL_CHARS:
            found.append(value)
    for match in _LABEL.finditer(prompt or ""):
        value = match.group(2).strip().strip(_STRIP)
        if value and value not in found and len(value) <= MAX_LITERAL_CHARS:
            found.append(value)
    return found[:MAX_LITERALS]


def no_text_guidance(prompt: str, literals: list[str]) -> str:
    """Append an instruction that stops ChatGPT from drawing the text itself."""
    if not literals:
        return prompt
    listing = "\n".join(f"- {item}" for item in literals)
    return (
        (prompt or "").strip()
        + "\n\nCRITICAL LAYOUT CONSTRAINT: Generate the artwork with absolutely NO text, "
        "letters, numbers, logos or watermarks anywhere. Leave clean, simple empty "
        "areas (plain dark background) where text will be placed afterwards by the "
        "design system. The following strings will be typeset separately:\n"
        + listing
        + "\nDo NOT attempt to render them. Do not add any substitute characters."
    )


def _auto_boxes(count: int, width: int, height: int) -> list[tuple[int, int, int, int]]:
    """Simple, predictable layout bands, top to bottom."""
    margin = int(width * 0.06)
    band = int(height / max(1, count))
    return [
        (margin, int(i * band + band * 0.15), width - margin, int((i + 1) * band - band * 0.10))
        for i in range(count)
    ]


def render(image_bytes: bytes, texts: list[str], *, max_width_ratio: float = 0.88) -> bytes:
    """Draw ``texts`` onto the image and return the composed PNG bytes."""
    texts = [t for t in (texts or []) if t][:MAX_LITERALS]
    if not texts:
        return image_bytes
    image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
    width, height = image.size
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    boxes = _auto_boxes(len(texts), width, height)
    # Adapt font size to each band; serif-free Vazirmatn stays legible.
    for text, box in zip(texts, boxes):
        left, top, right, bottom = box
        band_height = max(8, bottom - top)
        size = max(14, int(band_height * 0.62))
        font = ImageFont.truetype(str(FONT_PATH), size)
        label = shaped(text)
        bbox = draw.textbbox((0, 0), label, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        max_w = int((right - left) * max_width_ratio)
        if tw > max_w:
            size = max(12, int(size * max_w / tw))
            font = ImageFont.truetype(str(FONT_PATH), size)
            bbox = draw.textbbox((0, 0), label, font=font)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = left + max(0, (right - left - tw) // 2)
        y = top + max(0, (band_height - th) // 2) - bbox[1]
        draw.text((x, y), label, font=font, fill=(255, 255, 255, 255))
    composed = Image.alpha_composite(image, layer).convert("RGB")
    out = io.BytesIO()
    composed.save(out, format="PNG")
    return out.getvalue()
