from django.urls import path

from hcmus import views

urlpatterns = [
    path('resolver/', views.resolver_index, name='hcmus_resolver_index'),
    path('resolver/<str:contest_key>/', views.resolver, name='hcmus_resolver'),
    path('resolver/<str:contest_key>/data.json', views.resolver_data, name='hcmus_resolver_data'),
]
