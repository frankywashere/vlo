"""CPU wordmark artwork shared by every video delivery path."""
import math

from PIL import Image, ImageDraw, ImageFilter, ImageFont


def create_watermark(width: int, height: int) -> tuple[Image.Image, int]:
    """Return the branded badge and its inset from the bottom/right video edges."""
    scale = 4
    short_edge = min(width, height)
    size = max(10, min(54, round(short_edge / 32)))
    # Pillow bundles this font: identical artwork in local and container exports,
    # including workers without system fonts or FFmpeg's drawtext filter.
    stroke = max(1, round(size * scale * .028))
    tracking = -size * scale * .04
    glyphs = []
    cursor = 0
    for word, ratio, color in (("recast", 1, (23, 23, 25, 255)),
                               ("fx", 1, (204, 63, 55, 255)),
                               (".com", .64, (23, 23, 25, 255))):
        font = ImageFont.load_default(size=round(size * scale * ratio))
        word_stroke = max(1, round(stroke * ratio))
        for character in word:
            bounds = font.getbbox(character, anchor="ls", stroke_width=word_stroke)
            glyphs.append((character, cursor, bounds, color, font, word_stroke))
            cursor += font.getlength(character) + tracking * ratio
    left = min(x + bounds[0] for _, x, bounds, *_ in glyphs)
    right = max(x + bounds[2] for _, x, bounds, *_ in glyphs)
    top = min(bounds[1] for _, _, bounds, *_ in glyphs)
    bottom = max(bounds[3] for _, _, bounds, *_ in glyphs)
    padding_x, padding_y = round(size * scale * .55), round(size * scale * .38)
    blur = max(scale, round(size * scale * .055))
    surround = math.ceil(blur * 3)
    panel_width = math.ceil(right - left) + padding_x * 2
    panel_height = bottom - top + padding_y * 2
    dimensions = (panel_width + surround * 2, panel_height + surround * 2)
    radius = min(5 * scale, round(size * scale * .22))

    # A nearly opaque white plate keeps both brand colors stable on light, dark,
    # and busy footage. The fine edge and shadow define it against white scenes.
    shadow = Image.new("RGBA", dimensions)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (surround, surround + scale, surround + panel_width - 1, surround + panel_height + scale - 1),
        radius=radius, fill=(0, 0, 0, 42))
    badge = shadow.filter(ImageFilter.GaussianBlur(blur))
    artwork = Image.new("RGBA", dimensions)
    draw = ImageDraw.Draw(artwork)
    draw.rounded_rectangle((surround, surround, surround + panel_width - 1, surround + panel_height - 1),
                           radius=radius, fill=(255, 255, 255, 246), outline=(35, 32, 36, 32), width=scale)
    for character, x, _, color, font, word_stroke in glyphs:
        draw.text((surround + padding_x + x - left, surround + padding_y - top), character,
                  font=font, anchor="ls", fill=color, stroke_width=word_stroke, stroke_fill=color)
    badge = Image.alpha_composite(badge, artwork)
    badge = badge.resize((math.ceil(dimensions[0] / scale), math.ceil(dimensions[1] / scale)), Image.Resampling.LANCZOS)
    margin = min(max(4, round(short_edge / 40)), max(0, (short_edge - 1) // 4))
    fit = min(1, max(1, width - margin * 2) / badge.width, max(1, height - margin * 2) / badge.height)
    if fit < 1:
        badge = badge.resize((max(1, int(badge.width * fit)), max(1, int(badge.height * fit))), Image.Resampling.LANCZOS)
    return badge, margin
