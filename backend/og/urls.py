from django.urls import path

from og import views

app_name = 'og'

urlpatterns = [
    path('shared/<uuid:uuid>/', views.SharedPlanPreviewView.as_view(), name='shared-plan'),
    path('shared/<uuid:uuid>.png', views.SharedPlanImageView.as_view(), name='shared-plan-image'),
]
