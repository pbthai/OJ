from django.urls import path

from hcmus import views  # noqa: I202  app riêng, không phải thư viện bên thứ ba

urlpatterns = [
    path('resolver/', views.resolver_index, name='hcmus_resolver_index'),
    path('resolver/<str:contest_key>/', views.resolver, name='hcmus_resolver'),
    path('resolver/<str:contest_key>/data.json', views.resolver_data, name='hcmus_resolver_data'),
    path('de-bai/', views.statement_index, name='hcmus_statement_index'),
    path('de-bai/<str:contest_key>/tai-ve.pdf', views.statement_download,
         name='hcmus_statement_download'),
    path('xep-hang/', views.ranking_list, name='hcmus_ranking_list'),
    path('xep-hang/<slug:slug>/', views.ranking_detail, name='hcmus_ranking_detail'),
]
