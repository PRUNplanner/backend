from drf_spectacular.authentication import BasicScheme
from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from user.auth_apikey import LoggedBasicAuthentication, LoggedJWTAuthentication, UserAPIKeyAuthentication


class UserAPIKeySchema(OpenApiAuthenticationExtension):
    target_class = UserAPIKeyAuthentication
    name = ['ApiKeyAuth', 'ApiKeyQuery']

    def get_security_requirement(self, auto_schema):
        # either one, not both
        return [{name: []} for name in self.name]

    def get_security_definition(self, auto_schema):
        return [
            {
                'type': 'apiKey',
                'in': 'header',
                'name': 'Authorization',
                'description': 'Format: Api-Key <your-key-here>',
            },
            {
                'type': 'apiKey',
                'in': 'query',
                'name': 'api_key',
                'description': 'The key as ?api_key=<your-key-here>, e.g. for Google Sheets IMPORTDATA',
            },
        ]


# spectacular matches authenticators by exact class, so the logging subclasses need their own entries
class LoggedJWTScheme(SimpleJWTScheme):
    target_class = LoggedJWTAuthentication


class LoggedBasicScheme(BasicScheme):
    target_class = LoggedBasicAuthentication
