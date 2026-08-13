from django.urls import path
from django.views.generic import RedirectView

from hcmus import views  # noqa: I202  app riêng, không phải thư viện bên thứ ba

urlpatterns = [
    path('resolver/', views.resolver_index, name='hcmus_resolver_index'),
    path('resolver/<str:contest_key>/', views.resolver, name='hcmus_resolver'),
    path('resolver/<str:contest_key>/data.json', views.resolver_data, name='hcmus_resolver_data'),
    path('de-bai/', views.statement_index, name='hcmus_statement_index'),
    path('de-bai/<str:contest_key>/tai-ve.pdf', views.statement_download,
         name='hcmus_statement_download'),
    path('nap-de-tex/', views.problem_tex, name='hcmus_problem_tex'),
    path('suc-khoe/', views.health_page, name='hcmus_health'),
    path('suc-khoe/data.json', views.health_data, name='hcmus_health_data'),
    path('suc-khoe/may-cham/<str:name>/', views.health_judge_toggle, name='hcmus_judge_toggle'),
    path('bang-vang/', views.ranking_list, name='hcmus_ranking_list'),
    path('bang-vang/<slug:slug>/', views.ranking_detail, name='hcmus_ranking_detail'),
    # Giữ link cũ /xep-hang/ sống: chuyển hướng vĩnh viễn sang /bang-vang/.
    path('xep-hang/', RedirectView.as_view(pattern_name='hcmus_ranking_list', permanent=True)),
    path('xep-hang/<slug:slug>/',
         RedirectView.as_view(pattern_name='hcmus_ranking_detail', permanent=True)),
    path('lich/', views.calendar_page, name='hcmus_calendar'),
    path('lich/cong-khai.ics', views.calendar_ical, name='hcmus_calendar_ical'),
    path('tai-khoan/', views.accounts_page, name='hcmus_accounts'),
    # Bảng xếp hạng công khai qua link bí mật (kỳ thi vẫn riêng tư).
    path('bang-diem/<str:token>/', views.public_scoreboard, name='hcmus_public_scoreboard'),
    path('tim-teammate/', views.teammate_board, name='hcmus_teammate'),
    path('tim-teammate/trang-thai/', views.teammate_status, name='hcmus_teammate_status'),
    path('tim-teammate/xoa/<int:pk>/', views.teammate_delete, name='hcmus_teammate_delete'),
    path('in-bai/<int:submission>/', views.print_submission, name='hcmus_print_submission'),
    # ĐẶT CUỐI CÙNG: bắt mọi đường dẫn một cấp còn lại (vd /pretest/). Phải nằm sau
    # tất cả URL khác, nếu không nó nuốt mất các trang bên trên.
    path('<slug:slug>/', views.landing_page, name='hcmus_landing'),
]
