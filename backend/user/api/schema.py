from drf_spectacular.extensions import OpenApiAuthenticationExtension
from user.auth_apikey import UserAPIKeyAuthentication


class UserAPIKeySchema(OpenApiAuthenticationExtension):
    target_class = UserAPIKeyAuthentication
    name = 'ApiKeyAuth'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'apiKey',
            'in': 'header',
            'name': 'Authorization',
            'description': 'Format: Api-Key <your-key-here>',
        }
