from django.urls import reverse_lazy
from django.utils.functional import Promise

from core.env import settings


def _changelist(model: str) -> Promise:
    return reverse_lazy(f'admin:{model}_changelist')


def _item(title: str, icon: str, model: str) -> dict[str, object]:
    return {'title': title, 'icon': icon, 'link': _changelist(model)}


_EXTERNAL = (
    [{'title': 'External', 'items': [{'title': 'Axiom logs', 'icon': 'open_in_new', 'link': settings.admin_axiom_url}]}]
    if settings.admin_axiom_url
    else []
)

# Unfold
UNFOLD = {
    'SITE_TITLE': 'PRUNplanner',
    'SITE_HEADER': 'PRUNplanner',
    'SITE_SUBHEADER': 'Admin',
    'SITE_SYMBOL': 'public',
    'BORDER_RADIUS': '6px',
    'THEME': 'dark',
    'SHOW_HISTORY': True,
    'DASHBOARD_CALLBACK': 'analytics.dashboard.dashboard_index',
    'ENVIRONMENT': 'core.admin.environment_callback',
    'ENVIRONMENT_TITLE_PREFIX': 'core.admin.environment_title_prefix_callback',
    # full ramps from the frontend palette: 700/800/900 are its border/card/page, primary 400 the brand lime
    'COLORS': {
        'base': {
            '50': '#f7f7f6',
            '100': '#ededeb',
            '200': '#d6d6d3',
            '300': '#b4b4b0',
            '400': '#8a8a86',
            '500': '#6b6b68',
            '600': '#4d4d4b',
            '700': '#2d2d30',
            '800': '#1e1e1e',
            '900': '#151515',
            '950': '#0d0d0d',
        },
        'primary': {
            '50': '#f8fce6',
            '100': '#eff8c6',
            '200': '#e0f194',
            '300': '#d0ea5c',
            '400': '#c0e219',
            '500': '#a8c615',
            '600': '#86a011',
            '700': '#657a0f',
            '800': '#4f5f10',
            '900': '#3a4610',
            '950': '#1f2606',
        },
    },
    'COMMAND': {'search_models': True, 'show_history': True},
    'SIDEBAR': {
        'show_search': True,
        'show_all_applications': False,
        'navigation': [
            {
                'title': 'Overview',
                'items': [
                    {'title': 'Dashboard', 'icon': 'dashboard', 'link': reverse_lazy('admin:index')},
                    {'title': 'Task health', 'icon': 'monitor_heart', 'link': reverse_lazy('admin:task_health')},
                ],
            },
            {
                'title': 'Users',
                'items': [
                    _item('Users', 'person', 'user_user'),
                    _item('API keys', 'key', 'user_userapikey'),
                    _item('Verification codes', 'pin', 'user_verificationcode'),
                    _item('Preferences', 'tune', 'user_userpreference'),
                ],
            },
            {
                'title': 'Planning',
                'items': [
                    _item('Plans', 'description', 'planning_planningplan'),
                    _item('Empires', 'hub', 'planning_planningempire'),
                    _item('CX preferences', 'storefront', 'planning_planningcx'),
                    _item('Shared plans', 'share', 'planning_planningshared'),
                ],
            },
            {
                'title': 'Game data',
                'items': [
                    {
                        'title': 'Planets',
                        'icon': 'public',
                        'link': _changelist('gamedata_gameplanet'),
                        'badge': 'core.admin.badge_stuck_planets',
                    },
                    _item('Materials', 'category', 'gamedata_gamematerial'),
                    _item('Buildings', 'factory', 'gamedata_gamebuilding'),
                    _item('Recipes', 'science', 'gamedata_gamerecipe'),
                    _item('Exchanges', 'currency_exchange', 'gamedata_gameexchange'),
                ],
            },
            {
                'title': 'Market data',
                'items': [
                    _item('CXPC', 'candlestick_chart', 'gamedata_gameexchangecxpc'),
                    _item('Exchange analytics', 'monitoring', 'gamedata_gameexchangeanalytics'),
                ],
            },
            {
                'title': 'Automation',
                'items': [
                    _item('FIO player data', 'sync', 'gamedata_gamefioplayerdata'),
                    _item('Periodic tasks', 'schedule', 'django_celery_beat_periodictask'),
                    _item('Intervals', 'timer', 'django_celery_beat_intervalschedule'),
                    _item('Crontabs', 'event_repeat', 'django_celery_beat_crontabschedule'),
                    _item('Webhooks', 'webhook', 'user_globalconfigwebhook'),
                ],
            },
            {
                'title': 'Analytics',
                'items': [
                    _item('App statistics', 'query_stats', 'analytics_appstatistic'),
                    _item('Plan aggregates', 'insights', 'analytics_analyticsplanaggregate'),
                    _item('Empire snapshots', 'inventory', 'analytics_analyticsempirematerialsnapshot'),
                ],
            },
            {'title': 'System', 'items': [_item('Log entries', 'history', 'admin_logentry')]},
            *_EXTERNAL,
        ],
    },
}
