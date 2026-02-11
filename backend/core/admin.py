from django.contrib import admin
from django.contrib.auth.models import Group
from django_celery_beat.models import ClockedSchedule, SolarSchedule

# Hide admin elements
try:
    admin.site.unregister(Group)
    admin.site.unregister(SolarSchedule)
    admin.site.unregister(ClockedSchedule)
except Exception:
    pass
