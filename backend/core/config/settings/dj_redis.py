from core.env import settings

DJ_REDIS_PANEL_SETTINGS = {
    'ALLOW_KEY_DELETE': True,
    'INSTANCES': {
        'default': {
            'description': 'Default Redis Instance',
            'url': settings.cache.default_location,
        }
    },
}
