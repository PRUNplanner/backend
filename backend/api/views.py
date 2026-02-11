from django.http import HttpRequest, HttpResponse
from django.template import loader


def index(request: HttpRequest) -> HttpResponse:
    template = loader.get_template('api/index.html')

    return HttpResponse(template.render(None, request))
