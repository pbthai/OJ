from django.urls import path

from hcmus import views  # noqa: I202  app riêng, không phải thư viện bên thứ ba

urlpatterns = [
    path('resolver/', views.resolver_index, name='hcmus_resolver_index'),
    path('resolver/<str:contest_key>/', views.resolver, name='hcmus_resolver'),
    path('resolver/<str:contest_key>/data.json', views.resolver_data, name='hcmus_resolver_data'),
    path('de-bai/', views.statement_index, name='hcmus_statement_index'),
    path('de-bai/<str:contest_key>/tai-ve.pdf', views.statement_download,
         name='hcmus_statement_download'),
    path('suc-khoe/', views.health_page, name='hcmus_health'),
    path('suc-khoe/data.json', views.health_data, name='hcmus_health_data'),
    path('suc-khoe/may-cham/<str:name>/', views.health_judge_toggle, name='hcmus_judge_toggle'),
    path('xep-hang/', views.ranking_list, name='hcmus_ranking_list'),
    path('xep-hang/<slug:slug>/', views.ranking_detail, name='hcmus_ranking_detail'),
    path('lich/', views.calendar_page, name='hcmus_calendar'),
    path('lich/cong-khai.ics', views.calendar_ical, name='hcmus_calendar_ical'),
]
