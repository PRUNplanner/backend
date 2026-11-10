import io
import time

import pytest
from og import images
from PIL import Image

CARD = images.PlanCard(
    name='Steel mill ' + 'with a very long name ' * 10,
    planet='Montem (OT-580b)',
    username='Łukasz_' + 'x' * 140,
    footer='prunplanner.org · CI1 30-day average · prices as of 8 Nov 2026',
    profit=27740.4,
    payback='pays back in 45 days',
    area_used=412,
    area_total=500,
    outputs=[
        images.CardOutput('FE', 'metals', 123.4),
        images.CardOutput('LST', 'construction materials', 12.34),
        images.CardOutput('FLX', 'chemicals', 1.2),
        images.CardOutput('ZZZ', 'unknown', 0),
    ],
)


def _open(png: bytes) -> Image.Image:
    return Image.open(io.BytesIO(png))


def test_plan_card_is_a_1200x630_png() -> None:
    image = _open(images.render_plan_card(CARD))

    assert image.format == 'PNG'
    assert image.size == (1200, 630)
    assert image.mode == 'RGB'
    # the lime colour bar
    assert image.getpixel((4, 300)) == (0xC0, 0xE2, 0x18)


@pytest.mark.parametrize(
    'card',
    [
        images.PlanCard(name='Old plan', planet='OT-580b', username='bob', footer='prunplanner.org'),
        images.PlanCard(
            name='Old plan',
            planet='OT-580b',
            username='bob',
            footer='prunplanner.org',
            facts=['Metallurgy COGC · 12 buildings'],
            outputs=[images.CardOutput('FE', 'metals')],
        ),
        images.PlanCard(name='Loss', planet='X', username='y', footer='z', profit=-10, area_used=0, area_total=500),
    ],
)
def test_cards_without_numbers_or_with_a_loss(card: images.PlanCard) -> None:
    assert _open(images.render_plan_card(card)).size == (1200, 630)


def test_render_time_budget() -> None:
    images.render_plan_card(CARD)  # fonts load once per process
    timings = []
    for _ in range(3):
        start = time.perf_counter()
        images.render_plan_card(CARD)
        timings.append(time.perf_counter() - start)
    # the best of three, so one slow moment on a CI runner doesn't fail it
    assert min(timings) < 0.15


def test_long_text_is_cut_with_an_ellipsis() -> None:
    cut = images._fit(CARD.name, 60, True, 1072)
    assert cut.endswith('…')
    assert images._width(cut, 60, True) <= 1072
    assert images._fit('short', 60, True, 1072) == 'short'


def test_category_key_matches_the_frontend() -> None:
    assert images.category_key('agricultural products') == 'agricultural-products'
    assert images.category_key('consumables (basic)') == 'consumables-basic'
