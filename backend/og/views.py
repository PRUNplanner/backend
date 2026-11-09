"""
Link previews: Open Graph HTML and a card image per shared plan, for chat apps' crawlers (Discord first). Traefik
sends crawler user agents on the app's /shared/<uuid> here; people keep getting the app.
"""

import hashlib
import re
import time
from uuid import UUID

import structlog
from api.renderers import OrjsonRenderer
from core.env import settings
from django.core.cache import cache
from django.http import Http404, HttpResponse
from django.template import loader
from gamedata.models import GameMaterial, GamePlanet
from planning.models import PlanningShared
from planning.schemas.planning_plan_summary import PlanSummary_V1
from pydantic import ValidationError
from rest_framework.negotiation import BaseContentNegotiation
from rest_framework.permissions import AllowAny
from rest_framework.renderers import BaseRenderer
from rest_framework.request import Request
from rest_framework.views import APIView

from og import images, texts

logger = structlog.get_logger(__name__)

APP_URL = (settings.frontend_url or 'https://prunplanner.org').rstrip('/')
SITE_NAME = 'PRUNplanner'
THEME_COLOUR = '#c0e218'
IMAGE_TTL = 7 * 24 * 60 * 60

# the app's own card, for unknown links
GENERIC = {
    'title': 'PRUNplanner: Base and Empire Planner for Prosperous Universe',
    'description': (
        'Free planning tool for Prosperous Universe (PrUn): plan bases and empires, find planets, compare ROI and '
        'track burn with FIO.'
    ),
    'image': f'{APP_URL}/images/og/prunplanner.jpg',
    'image_alt': 'The PRUNplanner plan editor with the claim: Plan a base before you build it.',
}

# as Traefik's router matches them; anything else is logged as other
_BOTS = re.compile(
    r'discordbot|slackbot|twitterbot|telegrambot|facebookexternalhit|whatsapp|linkedinbot|redditbot|mastodon|'
    r'bluesky|skypeuripreview|embedly',
    re.IGNORECASE,
)


def _bot(request: Request) -> str:
    match = _BOTS.search(request.META.get('HTTP_USER_AGENT', ''))
    return match.group(0).lower() if match else 'other'


class _IgnoreAccept(BaseContentNegotiation):
    """Crawlers send all kinds of Accept headers; these views have one answer each, so none is refused (406)."""

    def select_parser(self, request: Request, parsers: list[object]) -> object:
        return parsers[0] if parsers else None

    def select_renderer(
        self, request: Request, renderers: list[BaseRenderer], format_suffix: str | None = None
    ) -> tuple[BaseRenderer, str]:
        return renderers[0], renderers[0].media_type or '*/*'


class _OgView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_scope = 'og'
    # errors (404, 429) are DRF responses; the views return finished HttpResponses
    renderer_classes = [OrjsonRenderer]
    content_negotiation_class = _IgnoreAccept
    # not part of the API: left out of the OpenAPI schema
    schema = None


class _SharedPlan:
    """A shared plan as the card shows it: one query for share, plan and owner, one for the planet's name."""

    def __init__(self, share: PlanningShared) -> None:
        plan = share.plan
        self.uuid = str(share.uuid)
        self.plan = plan
        self.username: str = plan.user.username
        planet_name = (
            GamePlanet.objects.filter(planet_natural_id=plan.planet_natural_id)
            .values_list('planet_name', flat=True)
            .first()
        )
        self.planet = texts.planet_label(planet_name or '', plan.planet_natural_id)
        try:
            self.summary = PlanSummary_V1.model_validate(plan.plan_summary) if plan.plan_summary else None
        except ValidationError:
            # stored by an older schema: the card without numbers
            self.summary = None
        self.summary_at = plan.plan_summary_at if self.summary else None
        self.cogc = plan.plan_cogc
        self.building_count, self.outputs = texts.plan_data_facts(plan.plan_data)

    @classmethod
    def find(cls, uuid: UUID) -> '_SharedPlan | None':
        share = PlanningShared.objects.select_related('plan__user').filter(uuid=uuid).first()
        return cls(share) if share else None

    @property
    def version(self) -> str:
        """Changes with everything the card shows, so a new ?v= makes Discord load the new image."""
        parts = (self.plan.modified_at, self.summary_at, self.plan.plan_name, self.planet, self.username)
        # a cache-busting key, not a security check
        return hashlib.sha1('|'.join(map(str, parts)).encode(), usedforsecurity=False).hexdigest()[:12]

    @property
    def app_url(self) -> str:
        return f'{APP_URL}/shared/{self.uuid}'

    @property
    def title(self) -> str:
        return texts.plan_title(self.plan.plan_name, self.planet)

    @property
    def description(self) -> str:
        return texts.plan_description(
            username=self.username,
            summary=self.summary,
            summary_at=self.summary_at,
            cogc=self.cogc,
            planet=self.planet,
            building_count=self.building_count,
            outputs=self.outputs,
        )

    def card(self) -> images.PlanCard:
        tickers = [o.ticker for o in self.summary.outputs] if self.summary else self.outputs[:4]
        categories = dict(GameMaterial.objects.filter(ticker__in=tickers).values_list('ticker', 'category_name'))
        footer = ['prunplanner.org']
        if self.summary:
            basis = texts.price_basis_label(self.summary.price_basis)
            if basis:
                footer.append(basis)
            if self.summary_at:
                footer.append(f'prices as of {texts.date_label(self.summary_at)}')

        name, planet, username, footer_text = self.plan.plan_name, self.planet, self.username, ' · '.join(footer)
        if self.summary is None:
            buildings = f'{self.building_count} building' + ('' if self.building_count == 1 else 's')
            facts = [texts.cogc_label(self.cogc), buildings if self.building_count else None]
            return images.PlanCard(
                name=name,
                planet=planet,
                username=username,
                footer=footer_text,
                facts=[' · '.join(fact for fact in facts if fact)] if any(facts) else [],
                outputs=[images.CardOutput(ticker, categories.get(ticker, '')) for ticker in tickers],
            )
        summary = self.summary
        return images.PlanCard(
            name=name,
            planet=planet,
            username=username,
            footer=footer_text,
            profit=summary.profit,
            payback=f'pays back in {texts.days(summary.payback_days)}' if summary.payback_days is not None else None,
            area_used=summary.area_used,
            area_total=summary.area_total,
            outputs=[images.CardOutput(o.ticker, categories.get(o.ticker, ''), o.per_day) for o in summary.outputs],
        )


class SharedPlanPreviewView(_OgView):
    def get(self, request: Request, uuid: UUID) -> HttpResponse:
        shared = _SharedPlan.find(uuid)
        if shared is None:
            context = {**GENERIC, 'url': APP_URL + '/', 'noindex': True}
        else:
            image_path = f'/og/shared/{shared.uuid}.png?v={shared.version}'
            context = {
                'title': shared.title,
                'description': shared.description,
                'image': request.build_absolute_uri(image_path),
                'image_alt': f'Card of the plan {shared.title}',
                'url': shared.app_url,
                'noindex': False,
            }
        logger.info(
            'og_preview_served', kind='plan', format='html', uuid=str(uuid), bot=_bot(request), found=shared is not None
        )

        html = loader.render_to_string(
            'og/card.html', {**context, 'site_name': SITE_NAME, 'theme_colour': THEME_COLOUR}
        )
        response = HttpResponse(html, content_type='text/html; charset=utf-8')
        response['Cache-Control'] = 'public, max-age=300'
        # served at the app's own URL to crawlers only: a shared cache must not hand it to browsers
        response['Vary'] = 'User-Agent'
        return response


class SharedPlanImageView(_OgView):
    def get(self, request: Request, uuid: UUID) -> HttpResponse:
        shared = _SharedPlan.find(uuid)
        if shared is None:
            raise Http404

        key = f'og:plan:{shared.uuid}:{shared.version}'
        png: bytes | None = cache.get(key)
        cache_hit = png is not None
        if png is None:
            start = time.perf_counter()
            png = images.render_plan_card(shared.card())
            logger.info('og_image_rendered', kind='plan', duration_ms=round((time.perf_counter() - start) * 1000, 1))
            cache.set(key, png, IMAGE_TTL)
        logger.info(
            'og_preview_served', kind='plan', format='png', uuid=shared.uuid, bot=_bot(request), cache_hit=cache_hit
        )

        response = HttpResponse(png, content_type='image/png')
        response['Cache-Control'] = f'public, max-age={IMAGE_TTL}'
        return response
