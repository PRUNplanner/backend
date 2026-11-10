"""The link preview card as a 1200×630 PNG, drawn with Pillow. Pure: data in, PNG bytes out."""

import io
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH, HEIGHT = 1200, 630
PAD = 64

BACKGROUND = '#030707'
LIME = '#c0e218'
TEXT = '#e8eded'
MUTED = '#8a9797'
NEGATIVE = '#f2726f'
TRACK = '#1b2424'

# material tiles as the frontend's materials.css draws them: (background, text) per category
CATEGORY_COLOURS: dict[str, tuple[str, str]] = {
    'agricultural-products': ('#003800', '#8bb37b'),
    'alloys': ('#7b4c1e', '#facb9d'),
    'chemicals': ('#b72e5b', '#ffadda'),
    'construction-materials': ('#185bd3', '#97daff'),
    'construction-parts': ('#294d6b', '#a8ccea'),
    'construction-prefabs': ('#0f1e62', '#8e9de1'),
    'consumable-bundles': ('#3e0a11', '#bd8990'),
    'consumables-basic': ('#a62c2a', '#ff989e'),
    'consumables-luxury': ('#680000', '#db9191'),
    'drones': ('#8c3412', '#ffb391'),
    'electronic-devices': ('#561493', '#d593ff'),
    'electronic-parts': ('#5b2eb7', '#daadff'),
    'electronic-pieces': ('#7752bd', '#f6d1ff'),
    'electronic-systems': ('#331a4c', '#b299cb'),
    'elements': ('#3d2e20', '#bcad9f'),
    'energy-systems': ('#153e27', '#94bda6'),
    'fuels': ('#548d22', '#cbfaa3'),
    'gases': ('#00696b', '#7fe8ea'),
    'liquids': ('#67a8da', '#f1ffff'),
    'medical-equipment': ('#55aa55', '#d4ffd4'),
    'metals': ('#363636', '#b5b5b5'),
    'minerals': ('#997149', '#fff0c8'),
    'ores': ('#525761', '#d1d6e0'),
    'plastics': ('#791f62', '#f89ee1'),
    'ship-engines': ('#992900', '#ffa87f'),
    'ship-kits': ('#995400', '#ffd37f'),
    'ship-parts': ('#996300', '#ffe27f'),
    'ship-shields': ('#bf740a', '#dbdb79'),
    'software-components': ('#88792f', '#fff8ae'),
    'software-systems': ('#3c3505', '#bbb484'),
    'software-tools': ('#816213', '#ffe192'),
    'textiles': ('#525a21', '#d1d9a0'),
    'unit-prefabs': ('#1d1b1c', '#9c9a9b'),
    'utility': ('#a19488', '#ffffff'),
}
UNKNOWN_CATEGORY = ('#191919', '#7f7f7f')

FONT_DIR = Path(__file__).resolve().parent.parent / 'assets' / 'fonts'
CURRENCY = 'ȼ'


def category_key(category_name: str) -> str:
    """'agricultural products' → 'agricultural-products', as the frontend's materialCategoryClass"""
    return category_name.replace(' ', '-').replace('(', '').replace(')', '')


@dataclass(frozen=True)
class CardOutput:
    ticker: str
    category: str
    # None on a card without a summary
    per_day: float | None = None


@dataclass(frozen=True)
class PlanCard:
    name: str
    planet: str
    username: str
    footer: str
    # summary numbers, None without a summary
    profit: float | None = None
    payback: str | None = None
    area_used: int | None = None
    area_total: int | None = None
    # shown without a summary: COGC and building count
    facts: list[str] = field(default_factory=list)
    outputs: list[CardOutput] = field(default_factory=list)


# Fonts: Roboto (bundled, assets/fonts), its latin subset plus latin-ext for other letters. Roboto has no ȼ, so the
# currency sign is drawn as a c with a stroke.


@lru_cache(maxsize=32)
def _font(size: int, bold: bool, ext: bool) -> ImageFont.FreeTypeFont:
    name = f'roboto-latin{"-ext" if ext else ""}-{700 if bold else 400}-normal.woff'
    return ImageFont.truetype(str(FONT_DIR / name), size)


@lru_cache(maxsize=1024)
def _has_glyph(ext: bool, char: str) -> bool:
    font = _font(24, False, ext)
    # a missing character renders as the font's .notdef box; the mask supports the buffer protocol
    return bytes(font.getmask(char)) != bytes(font.getmask(chr(0xFFFF)))  # ty: ignore[invalid-argument-type]


def _runs(text: str, size: int, bold: bool) -> list[tuple[str, ImageFont.FreeTypeFont]]:
    """Splits text into runs of one font each; the currency sign is a run of its own."""
    runs: list[tuple[str, ImageFont.FreeTypeFont]] = []
    for char in text:
        ext = char != CURRENCY and not _has_glyph(False, char) and _has_glyph(True, char)
        font = _font(size, bold, ext)
        if runs and runs[-1][1] is font and char != CURRENCY and runs[-1][0] != CURRENCY:
            runs[-1] = (runs[-1][0] + char, font)
        else:
            runs.append((char, font))
    return runs


def _width(text: str, size: int, bold: bool) -> float:
    return sum(font.getlength('c' if run == CURRENCY else run) for run, font in _runs(text, size, bold))


def _fit(text: str, size: int, bold: bool, max_width: float) -> str:
    """The text, cut with an ellipsis to fit max_width"""
    if _width(text, size, bold) <= max_width:
        return text
    while text and _width(text + '…', size, bold) > max_width:
        text = text[:-1]
    return text.rstrip() + '…'


def _text(
    draw: ImageDraw.ImageDraw, xy: tuple[float, float], text: str, size: int, fill: str, bold: bool = False
) -> float:
    """Draws text from its top-left at xy, returns its width"""
    x, y = xy
    for run, font in _runs(text, size, bold):
        if run == CURRENCY:
            draw.text((x, y), 'c', font=font, fill=fill, anchor='ls')
            left, top, right, bottom = draw.textbbox((x, y), 'c', font=font, anchor='ls')
            stroke = max(2, size // 14)
            draw.line((left + 1, bottom + size * 0.08, right - 1, top - size * 0.08), fill=fill, width=stroke)
            x += font.getlength('c')
        else:
            draw.text((x, y), run, font=font, fill=fill, anchor='ls')
            x += font.getlength(run)
    return x - xy[0]


def _per_day(value: float) -> str:
    if value >= 100:
        return f'{value:,.0f}/day'
    if value >= 10:
        return f'{value:.1f}/day'
    return f'{value:.2f}/day'


def render_plan_card(card: PlanCard) -> bytes:
    image = Image.new('RGB', (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    inner = WIDTH - 2 * PAD

    # lime edge, as Discord's colour bar
    draw.rectangle((0, 0, 11, HEIGHT), fill=LIME)

    # baselines (anchor 'ls'): planet first, as large as it gets, then the plan's name and its owner
    _text(draw, (PAD, 108), _fit(card.planet, 60, True, inner), 60, TEXT, bold=True)
    owner = f'  · shared by {card.username}'
    owner_width = min(_width(owner, 30, False), inner / 3)
    name_width = _text(draw, (PAD, 168), _fit(card.name, 44, True, inner - owner_width), 44, LIME, bold=True)
    _text(draw, (PAD + name_width, 168), _fit(owner, 30, False, inner - name_width), 30, MUTED)

    y = 220
    if card.profit is not None:
        _text(draw, (PAD, y + 30), 'PROFIT PER DAY', 22, MUTED, bold=True)
        colour = LIME if card.profit >= 0 else NEGATIVE
        width = _text(draw, (PAD, y + 120), f'{round(card.profit):,} {CURRENCY}', 96, colour, bold=True)
        if card.payback:
            _text(draw, (PAD + width + 40, y + 120), card.payback, 36, TEXT)
        if card.area_used is not None and card.area_total:
            meter_y = y + 158
            share = min(card.area_used / card.area_total, 1)
            label = f'Area {card.area_used:,} / {card.area_total:,}'
            label_width = _text(draw, (PAD, meter_y + 22), label, 24, MUTED)
            left = PAD + label_width + 24
            draw.rounded_rectangle((left, meter_y + 4, left + 360, meter_y + 20), radius=8, fill=TRACK)
            if share > 0:
                draw.rounded_rectangle(
                    (left, meter_y + 4, left + max(16, 360 * share), meter_y + 20), radius=8, fill=LIME
                )
    else:
        for index, fact in enumerate(card.facts[:2]):
            _text(draw, (PAD, y + 50 + index * 50), _fit(fact, 36, False, inner), 36, TEXT)

    # output tiles
    tile_y, tile_w, tile_h, gap = 440, 250, 108, 24
    for index, output in enumerate(card.outputs[:4]):
        left = PAD + index * (tile_w + gap)
        background, colour = CATEGORY_COLOURS.get(category_key(output.category), UNKNOWN_CATEGORY)
        draw.rounded_rectangle((left, tile_y, left + tile_w, tile_y + tile_h), radius=10, fill=background)
        if output.per_day is None:
            _text(draw, (left + 24, tile_y + 72), output.ticker, 44, colour, bold=True)
        else:
            _text(draw, (left + 24, tile_y + 54), output.ticker, 40, colour, bold=True)
            _text(draw, (left + 24, tile_y + 90), _per_day(output.per_day), 24, colour)

    _text(draw, (PAD, HEIGHT - 40), _fit(card.footer, 24, False, inner), 24, MUTED)

    buffer = io.BytesIO()
    image.save(buffer, format='PNG', optimize=False)
    return buffer.getvalue()
