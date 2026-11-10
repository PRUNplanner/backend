import io
import re
from datetime import UTC, datetime

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from model_bakery import baker
from PIL import Image
from planning.models import PlanningPlan, PlanningShared
from rest_framework.test import APIClient
from structlog.testing import capture_logs
from tests.fixtures.planning.fxt_plan_summary import plan_summary
from tests.fixtures.planning.fxt_plan_vallis import plan_data_vallis

pytestmark = pytest.mark.django_db

DISCORD = 'Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)'
AT = datetime(2026, 11, 8, 12, tzinfo=UTC)


@pytest.fixture
def share(user_factory) -> PlanningShared:
    user = user_factory(username='scorpio')
    plan = baker.make(
        'planning.PlanningPlan',
        user=user,
        plan_name='Steel mill',
        planet_natural_id='OT-580b',
        plan_cogc='METALLURGY',
        plan_data=plan_data_vallis,
        plan_summary=plan_summary,
        plan_summary_at=AT,
    )
    baker.make('gamedata.GamePlanet', planet_natural_id='OT-580b', planet_name='Montem')
    baker.make('gamedata.GameMaterial', ticker='FE', category_name='metals')
    return baker.make('planning.PlanningShared', user=user, plan=plan)


def _html(share_uuid: object, **headers: str):
    return APIClient().get(reverse('og:shared-plan', kwargs={'uuid': share_uuid}), HTTP_USER_AGENT=DISCORD, **headers)


def _meta(html: str, prop: str) -> str:
    match = re.search(rf'<meta (?:property|name)="{re.escape(prop)}" content="([^"]*)"', html)
    assert match, prop
    return match.group(1)


def _image_url(html: str) -> str:
    return _meta(html, 'og:image').replace('&amp;', '&').removeprefix('http://testserver')


class TestPreviewHtml:
    def test_plan_with_summary(self, share: PlanningShared) -> None:
        with capture_logs() as logs:
            response = _html(share.uuid)

        html = response.content.decode()
        assert response.status_code == 200
        assert response['Content-Type'].startswith('text/html')
        assert response['Cache-Control'] == 'public, max-age=300'
        assert 'User-Agent' in response['Vary']
        assert _meta(html, 'og:title') == 'Montem (OT-580b) · Steel mill'
        assert _meta(html, 'og:description').startswith('Shared by scorpio · 27,740 ȼ/day · pays back in 45 days')
        assert _meta(html, 'og:url') == f'https://prunplanner.org/shared/{share.uuid}'
        assert re.fullmatch(rf'/og/shared/{share.uuid}\.png\?v=[0-9a-f]{{12}}', _image_url(html))
        assert _meta(html, 'twitter:card') == 'summary_large_image'
        assert _meta(html, 'theme-color') == '#c0e218'
        assert 'noindex' not in html
        assert f'url=https://prunplanner.org/shared/{share.uuid}' in html
        assert {
            'event': 'og_preview_served',
            'kind': 'plan',
            'format': 'html',
            'uuid': str(share.uuid),
            'bot': 'discordbot',
            'found': True,
            'log_level': 'info',
        } in logs

    def test_plan_without_summary(self, share: PlanningShared) -> None:
        PlanningPlan.objects.filter(pk=share.plan.pk).update(plan_summary=None, plan_summary_at=None)

        html = _html(share.uuid).content.decode()

        description = _meta(html, 'og:description')
        assert description.startswith('Shared by scorpio · Montem (OT-580b) · Metallurgy COGC · ')
        assert 'building' in description
        assert 'ȼ' not in description

    def test_summary_of_an_unknown_schema_counts_as_none(self, share: PlanningShared) -> None:
        PlanningPlan.objects.filter(pk=share.plan.pk).update(plan_summary={'v': 99})

        assert 'ȼ' not in _meta(_html(share.uuid).content.decode(), 'og:description')

    def test_unknown_uuid_gets_the_generic_card(self) -> None:
        response = _html('356da85a-494a-45a9-b20e-16d0f128c5b8')

        html = response.content.decode()
        assert response.status_code == 200
        assert '<meta name="robots" content="noindex">' in html
        assert _meta(html, 'og:image') == 'https://prunplanner.org/images/og/prunplanner.jpg'

    def test_escapes_plan_names(self, share: PlanningShared) -> None:
        PlanningPlan.objects.filter(pk=share.plan.pk).update(plan_name='"><script>alert(1)</script>')

        html = _html(share.uuid).content.decode()

        assert '<script>' not in html

    def test_any_accept_header(self, share: PlanningShared) -> None:
        assert _html(share.uuid, HTTP_ACCEPT='application/xhtml+xml').status_code == 200

    def test_two_queries(self, share: PlanningShared) -> None:
        with CaptureQueriesContext(connection) as queries:
            _html(share.uuid)

        assert len(queries) <= 2

    def test_username_change_moves_the_image_version(self, share: PlanningShared) -> None:
        before = _image_url(_html(share.uuid).content.decode())
        share.plan.user.username = 'renamed'
        share.plan.user.save()

        after = _image_url(_html(share.uuid).content.decode())

        assert before != after

    def test_save_moves_the_image_version(self, share: PlanningShared) -> None:
        before = _image_url(_html(share.uuid).content.decode())
        PlanningPlan.objects.filter(pk=share.plan.pk).update(plan_summary_at=datetime(2026, 11, 9, tzinfo=UTC))

        assert _image_url(_html(share.uuid).content.decode()) != before


@pytest.mark.usefixtures('locmem_cache')
class TestPreviewImage:
    def test_png_rendered_once_then_cached(self, share: PlanningShared) -> None:
        url = _image_url(_html(share.uuid).content.decode())

        with capture_logs() as logs:
            first = APIClient().get(url, HTTP_USER_AGENT=DISCORD)
            second = APIClient().get(url, HTTP_USER_AGENT=DISCORD)

        assert first.status_code == second.status_code == 200
        assert first['Content-Type'] == 'image/png'
        assert first['Cache-Control'] == 'public, max-age=604800'
        assert first.content == second.content
        assert Image.open(io.BytesIO(first.content)).size == (1200, 630)
        events = [(log['event'], log.get('cache_hit')) for log in logs if log['event'].startswith('og_')]
        assert events == [('og_image_rendered', None), ('og_preview_served', False), ('og_preview_served', True)]

    def test_plan_without_summary(self, share: PlanningShared) -> None:
        PlanningPlan.objects.filter(pk=share.plan.pk).update(plan_summary=None, plan_summary_at=None)

        response = APIClient().get(reverse('og:shared-plan-image', kwargs={'uuid': share.uuid}))

        assert response.status_code == 200

    def test_unknown_uuid_is_404(self) -> None:
        url = reverse('og:shared-plan-image', kwargs={'uuid': '356da85a-494a-45a9-b20e-16d0f128c5b8'})
        assert APIClient().get(url).status_code == 404

    def test_throttled_per_ip(self, share: PlanningShared) -> None:
        client = APIClient(REMOTE_ADDR='10.0.0.1')
        url = reverse('og:shared-plan', kwargs={'uuid': share.uuid})
        for _ in range(60):
            assert client.get(url).status_code == 200

        assert client.get(url).status_code == 429
        assert APIClient(REMOTE_ADDR='10.0.0.2').get(url).status_code == 200
