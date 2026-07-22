"""Tính năng tuỳ biến của FIT-HCMUS (xem docs/07-lo-trinh-tinh-nang.md §4).

ICPC Resolver: diễn hoạt "mở băng" bảng xếp hạng sau contest.
Dữ liệu lấy thẳng từ kết quả vnoj đã tính (frozen_* và final) nên thứ hạng
luôn khớp bảng chính thức — không tự tính lại như các resolver bên ngoài.

QUAN TRỌNG: khi contest được mở băng (frozen_last_minutes=0) rồi recompute,
vnoj GHI ĐÈ frozen_points/is_frozen -> mất trạng thái đóng băng. Vì vậy view
này lưu snapshot JSON lần đầu chạy và ưu tiên dùng snapshot về sau.
"""
import json
import os

from django.conf import settings
from django.contrib.auth.decorators import user_passes_test
from django.core.exceptions import PermissionDenied
from django.http import (FileResponse, Http404, HttpResponse,
                         HttpResponseBadRequest, JsonResponse)
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from hcmus import statement_pdf
from hcmus.health import snapshot as health_snapshot
from hcmus.models import JudgeSwitch, Ranking
from hcmus.ranking import compute as compute_ranking
from hcmus.tasks import build_contest_statement
from judge.models import Contest, ContestParticipation
from judge.utils.celery import redirect_to_task_status

BASE = os.path.dirname(os.path.abspath(__file__))
SNAPSHOT_DIR = getattr(settings, 'HCMUS_SNAPSHOT_DIR', os.path.join(BASE, 'snapshots'))

staff_only = user_passes_test(lambda u: u.is_authenticated and u.is_staff)


def _label(contest, index):
    try:
        return contest.get_label_for_problem(index)
    except Exception:
        return chr(65 + index) if index < 26 else str(index + 1)


def _penalty_per_wrong(contest):
    try:
        return int(contest.format.config.get('penalty', 20))
    except Exception:
        return 20


def build_payload(contest):
    cps = list(contest.contest_problems.select_related('problem').order_by('order'))
    problems = [{'id': str(cp.id), 'label': _label(contest, i), 'code': cp.problem.code}
                for i, cp in enumerate(cps)]

    teams = []
    parts = (contest.users.filter(virtual=ContestParticipation.LIVE, is_disqualified=False)
             .select_related('user__user'))
    for part in parts:
        fd = part.format_data or {}
        cells = []
        for p in problems:
            d = fd.get(p['id']) or {}
            final_solved = float(d.get('points') or 0) > 0
            cells.append({
                'label': p['label'],
                'pending': bool(d.get('is_frozen')),
                'frozen': {
                    'solved': float(d.get('frozen_points') or 0) > 0,
                    'tries': int(d.get('frozen_tries') or 0),
                },
                'final': {
                    'solved': final_solved,
                    'tries': int(d.get('tries') or 0),
                    'minutes': int(float(d.get('time') or 0) // 60) if final_solved else None,
                },
            })
        u = part.user
        teams.append({
            'username': u.user.username,
            'name': (u.username_display_override or u.user.first_name or u.user.username),
            'org': (u.organizations.first().short_name if u.organizations.exists() else ''),
            'frozen': {'solved': int(part.frozen_score or 0), 'penalty': int(part.frozen_cumtime or 0)},
            'final': {'solved': int(part.score or 0), 'penalty': int(part.cumtime or 0)},
            'cells': cells,
        })

    return {
        'contest': {
            'key': contest.key,
            'name': contest.name,
            'frozen_minutes': contest.frozen_last_minutes,
            'penalty_per_wrong': _penalty_per_wrong(contest),
            'problems': problems,
        },
        'teams': teams,
    }


def _snapshot_path(key):
    return os.path.join(SNAPSHOT_DIR, f'{key}.json')


def get_payload(contest, refresh=False):
    """Ưu tiên snapshot (giữ được trạng thái băng kể cả sau khi đã mở băng)."""
    path = _snapshot_path(contest.key)
    if not refresh and os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    payload = build_payload(contest)
    # chỉ lưu snapshot khi còn dữ liệu băng (có ô pending) để không đè bằng bản đã mở
    has_pending = any(c['pending'] for t in payload['teams'] for c in t['cells'])
    if has_pending or refresh:
        os.makedirs(SNAPSHOT_DIR, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
    return payload


@staff_only
def resolver_index(request):
    rows = []
    for c in Contest.objects.order_by('-end_time')[:30]:
        rows.append(
            f'<li><a href="/resolver/{c.key}/">{c.name}</a> '
            f'<span style="color:#888">({c.key}, freeze {c.frozen_last_minutes}′'
            f'{", có snapshot" if os.path.exists(_snapshot_path(c.key)) else ""})</span></li>')
    html = ('<meta charset="utf-8"><title>ICPC Resolver</title>'
            '<body style="font:16px system-ui;max-width:760px;margin:40px auto">'
            '<h1>ICPC Resolver</h1><p>Chọn kỳ thi để diễn hoạt mở băng bảng xếp hạng:</p>'
            f'<ul style="line-height:1.9">{"".join(rows)}</ul>'
            '<p style="color:#888">Mẹo: diễn resolver TRƯỚC khi mở băng contest. '
            'Lần mở đầu tiên hệ thống tự lưu snapshot nên vẫn diễn lại được về sau.</p></body>')
    return HttpResponse(html)


@staff_only
def resolver_data(request, contest_key):
    try:
        contest = Contest.objects.get(key=contest_key)
    except Contest.DoesNotExist:
        raise Http404()
    return JsonResponse(get_payload(contest, refresh=request.GET.get('refresh') == '1'))


@staff_only
def resolver(request, contest_key):
    try:
        contest = Contest.objects.get(key=contest_key)
    except Contest.DoesNotExist:
        raise Http404()
    payload = get_payload(contest, refresh=request.GET.get('refresh') == '1')
    with open(os.path.join(BASE, 'resolver.html'), encoding='utf-8') as f:
        html = f.read()
    return HttpResponse(html.replace('__RESOLVER_DATA__', json.dumps(payload, ensure_ascii=False)))


# ==========================================================================
# Tải PDF đề bài trọn bộ của một contest
# ==========================================================================

@staff_only
def statement_index(request):
    """Form chọn contest + style. GET hiện form, POST đẩy việc sang celery."""
    if request.method == 'POST':
        return _statement_build(request)

    rows = []
    for c in Contest.objects.order_by('-start_time')[:40]:
        n = c.contest_problems.count()
        rows.append({
            'key': c.key,
            'name': c.name,
            'date': timezone.localtime(c.start_time).strftime('%d/%m/%Y'),
            'count': n,
            'cached': os.path.exists(os.path.join(_pdf_cache(), f'{c.key}.pdf')),
        })

    payload = {
        'contests': rows,
        'styles': [{'name': k, 'desc': v} for k, v in statement_pdf.BUNDLED_STY.items()],
        'default_sty': statement_pdf.DEFAULT_STY,
    }
    with open(os.path.join(BASE, 'statement.html'), encoding='utf-8') as f:
        html = f.read()
    # Trang này không đi qua template engine (xem ghi chú resolver.html) nên phải
    # tự tiêm CSRF token, không có {% csrf_token %} để dùng.
    html = html.replace('__CSRF_TOKEN__', get_token(request))
    return HttpResponse(html.replace('__STATEMENT_DATA__',
                                     json.dumps(payload, ensure_ascii=False)))


def _pdf_cache():
    from hcmus.tasks import cache_dir
    return cache_dir()


def _statement_build(request):
    contest_key = (request.POST.get('contest') or '').strip()
    if not Contest.objects.filter(key=contest_key).exists():
        return HttpResponseBadRequest('contest không tồn tại')

    sty_name = request.POST.get('sty') or statement_pdf.DEFAULT_STY
    sty_path = None

    upload = request.FILES.get('sty_file')
    if upload:
        content = upload.read()
        try:
            statement_pdf.validate_sty(upload.name, content)
        except statement_pdf.StatementError as e:
            return HttpResponseBadRequest(str(e))
        # Ghi ra đĩa vì celery worker là tiến trình khác, không thấy file tạm của request.
        # basename() chặn ../ trong tên file do client đặt.
        updir = os.path.join(_pdf_cache(), 'sty')
        os.makedirs(updir, exist_ok=True)
        sty_path = os.path.join(updir, os.path.basename(upload.name))
        with open(sty_path, 'wb') as f:
            f.write(content)
        sty_name = os.path.basename(upload.name)

    result = build_contest_statement.delay(contest_key, sty_name, sty_path)
    return redirect_to_task_status(
        result,
        message=f'Đang biên dịch đề bài "{contest_key}"...',
        redirect=reverse('hcmus_statement_download', args=[contest_key]))


@staff_only
def statement_download(request, contest_key):
    if not Contest.objects.filter(key=contest_key).exists():
        raise Http404()
    # basename() để key kỳ quái không leo ra khỏi thư mục cache
    path = os.path.join(_pdf_cache(), os.path.basename(f'{contest_key}.pdf'))
    if not os.path.exists(path):
        raise Http404('chưa có PDF cho contest này, hãy bấm tạo lại')
    resp = FileResponse(open(path, 'rb'), content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{contest_key}.pdf"'
    return resp


# ==========================================================================
# Bảng xếp hạng team tổng hợp nhiều contest
# ==========================================================================

def ranking_list(request):
    rankings = (Ranking.visible_to(request.user)
                .select_related('creator__user')
                .prefetch_related('contests__contest'))
    return render(request, 'hcmus/ranking-list.html', {
        'title': _('Team rankings'),
        'rankings': rankings,
        'can_create': request.user.is_authenticated and request.user.is_staff,
    })


def ranking_detail(request, slug):
    obj = get_object_or_404(Ranking, slug=slug)
    if not obj.is_accessible_by(request.user):
        # 404 chứ không 403: bảng riêng tư thì sự TỒN TẠI của nó cũng không nên lộ
        raise Http404()
    rows, rcs = compute_ranking(obj)

    # Lọc org, phân quyền theo NGƯỜI XEM. Trang này render từng request (khác
    # scoreboard contest dùng cache chung), nên hiện được đúng org mỗi người thấy:
    # org công khai HOẶC org họ là thành viên. Superuser / người sửa bảng xem hết.
    # Giấu tên org private khỏi người không thấy được, cả ở dòng lẫn ở dropdown.
    from judge.models import Organization
    user = request.user
    if user.is_superuser or obj.is_editable_by(user):
        visible = None
    else:
        visible = set(Organization.objects.filter(is_unlisted=False)
                      .values_list('short_name', flat=True))
        if user.is_authenticated:
            visible |= set(user.profile.organizations.values_list('short_name', flat=True))
    if visible is not None:
        for r in rows:
            if r['org'] and r['org'] not in visible:
                r['org'] = ''

    return render(request, 'hcmus/ranking-detail.html', {
        'title': obj.name,
        'ranking': obj,
        'rows': rows,
        'ranking_contests': rcs,
        'can_edit': obj.is_editable_by(request.user),
        'mixed_units': obj.mixed_penalty_units,
        # Org có mặt để dựng nút lọc, đã lọc theo quyền người xem ở trên.
        'filter_orgs': sorted({r['org'] for r in rows if r.get('org')}),
    })


# ==========================================================================
# Sức khoẻ hệ thống
# ==========================================================================

@staff_only
def health_page(request):
    return render(request, 'hcmus/health.html', {
        'title': _('System health'),
        'data': health_snapshot(),
        'can_toggle': request.user.has_perm('hcmus.control_judges'),
    })


@staff_only
def health_data(request):
    """Endpoint cho trang tự làm mới. Trả JSON, không render lại cả trang."""
    return JsonResponse(health_snapshot())


@staff_only
@require_POST
def health_judge_toggle(request, name):
    """Bật/tắt một máy chấm. Web chỉ ghi ý muốn; tiến trình root thi hành."""
    if not request.user.has_perm('hcmus.control_judges'):
        raise PermissionDenied()
    sw = get_object_or_404(JudgeSwitch, name=name)
    sw.enabled = request.POST.get('on') == '1'
    sw.changed_by = request.profile if hasattr(request, 'profile') else None
    sw.save()   # signal tự ghi spool
    return JsonResponse({'name': sw.name, 'enabled': sw.enabled})


# ==========================================================================
# Lịch: trang xem (lọc theo user) + feed .ics công khai
# ==========================================================================

def calendar_ical(request):
    """Feed .ics công khai. KHÔNG đăng nhập, chỉ phần công khai: công cụ lịch bên
    thứ ba fetch feed không mang phiên đăng nhập nên không phân quyền theo người
    ở tầng này được. Lịch riêng chỉ hiện trên web (calendar_page)."""
    from hcmus import calendar as cal
    resp = HttpResponse(cal.public_ical(request), content_type='text/calendar; charset=utf-8')
    resp['Content-Disposition'] = 'inline; filename="fit-hcmus-oj.ics"'
    return resp


def calendar_page(request):
    """Lịch dạng lưới, lọc theo đúng người đang xem. Contest tự vào theo quyền của
    họ, cộng các sự kiện tay họ được xem. Ẩn danh chỉ thấy phần công khai.

    Hiện 12 tháng liên tiếp từ tháng hiện tại, xếp dọc để cuộn — không bấm next."""
    from hcmus import calendar as cal
    return render(request, 'hcmus/calendar.html', {
        'title': _('Calendar'),
        'months': cal.months_grids(request.user, 12),
        'ical_url': request.build_absolute_uri(reverse('hcmus_calendar_ical')),
    })
