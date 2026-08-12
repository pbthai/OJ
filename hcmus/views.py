"""Tính năng tuỳ biến của FIT-HCMUS (xem docs/07-lo-trinh-tinh-nang.md §4).

ICPC Resolver: diễn hoạt "mở băng" bảng xếp hạng sau contest.
Phần FINAL luôn đọc sống từ format_data của vnoj nên khớp bảng chính thức, kể cả
sau khi rejudge. Không tự tính lại thứ hạng như các resolver bên ngoài.

Trạng thái ĐÓNG BĂNG (pending + frozen_*) bị vnoj ghi đè khi contest mở băng
(frozen_last_minutes=0) rồi recompute -> mất. Nên view lưu snapshot phần băng khi
contest CÒN băng, và về sau chỉ phủ phần băng đó lên bảng sống (final vẫn sống).
Nhờ vậy Resolver không bao giờ đông cứng phần final theo một lần rejudge nào.
"""
import json
import os

from django import forms
from django.conf import settings
from django.core.cache import cache
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import (FileResponse, Http404, HttpResponse,
                         HttpResponseBadRequest, HttpResponseRedirect, JsonResponse)
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST

from hcmus import statement_pdf
from hcmus.health import snapshot as health_snapshot
from hcmus.models import (JudgeSwitch, LandingPage, PublicScoreboard, Ranking,
                          TeammatePost)
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
                    # Giây để so ai giải trước: nhiều đội cùng giải trong một phút
                    # thì so theo phút sẽ ra vài "giải đầu tiên" cùng lúc.
                    'seconds': float(d.get('time') or 0) if final_solved else None,
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


def _save_snapshot(path, payload):
    """Ghi snapshot nguyên tử (tránh hỏng file nếu bị ngắt giữa chừng)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


def _overlay_frozen(live, snap):
    """Nền là bảng SỐNG (final khớp bảng chính thức, phản ánh mọi rejudge); phủ lại
    trạng thái băng (pending + frozen) từ snapshot đã lưu hồi contest còn đóng băng,
    khớp theo (username, nhãn bài) nên không phụ thuộc thứ tự.
    Đội bị loại (DQ) sau đó không có trong bảng sống -> tự động biến mất khỏi resolver."""
    snap_team = {t['username']: t for t in snap['teams']}
    snap_cell = {(t['username'], c['label']): c
                 for t in snap['teams'] for c in t['cells']}
    for t in live['teams']:
        for c in t['cells']:
            sc = snap_cell.get((t['username'], c['label']))
            if sc is not None:
                c['pending'] = sc['pending']
                c['frozen'] = sc['frozen']
        st = snap_team.get(t['username'])
        if st is not None:
            t['frozen'] = st['frozen']
    return live


def get_payload(contest, refresh=False):
    """FINAL luôn lấy từ bảng sống để khớp bảng chính thức (kể cả sau rejudge);
    snapshot chỉ dùng phục hồi trạng thái ĐÓNG BĂNG khi contest đã mở băng."""
    live = build_payload(contest)
    live_has_pending = any(c['pending'] for t in live['teams'] for c in t['cells'])
    path = _snapshot_path(contest.key)

    if live_has_pending:
        # Contest còn đóng băng: format_data sống đang giữ cả frozen lẫn final đúng.
        # Cập nhật snapshot để trạng thái mở-băng sống sót khi sau này mở băng thật.
        _save_snapshot(path, live)
        return live

    # Không còn ô băng nào: hoặc chưa từng băng, hoặc đã mở băng (frozen bị xoá).
    # Phủ frozen từ snapshot nhưng GIỮ final sống -> khớp bảng chính thức.
    if not refresh and os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            snap = json.load(f)
        return _overlay_frozen(live, snap)

    return live


# =========================================================================
# PHÂN QUYỀN DÙNG CHUNG cho các trang quản trị của hcmus.
#
# LUẬT (chép lại ở docs/10-phan-quyen-hcmus.md, sửa thì sửa cả hai chỗ):
#
#   R1. `is_staff` chỉ mở CỬA VÀO trang, KHÔNG quyết định người đó thấy kỳ thi
#       nào. Mọi danh sách kỳ thi phải đi qua _contests_for(user, ...).
#   R2. Trang chi tiết / tải file phải lấy kỳ thi TỪ CHÍNH queryset đã lọc
#       (_contest_or_404), không phải Contest.objects.get rồi mới kiểm.
#   R3. Không có quyền thì trả 404 chứ không 403: kỳ thi chưa công bố thì ngay
#       cả việc nó TỒN TẠI cũng không nên lộ.
#   R4. Nới quyền thì phải hỏi "người này đã đọc được nội dung đó trên web
#       chưa?". Nếu chưa thì không được mở qua cửa sau (PDF, JSON, resolver).
#
# Ai được xem gì:
#   - superuser / judge.edit_all_contest : mọi kỳ thi.
#   - author, curator của kỳ thi         : kỳ thi đó.
#   - tester của kỳ thi                  : kỳ thi đó, CHỈ với đề bài (họ được
#       mời đọc đề để thử), KHÔNG với resolver.
#   - còn lại: chỉ kỳ thi đã kết thúc + công khai hoàn toàn + không ẩn đề —
#       lúc đó đề đã đọc được trên web nên xuất PDF không lộ thêm gì.


def _is_contest_admin(user):
    return user.is_superuser or user.has_perm('judge.edit_all_contest')


def _contests_for(user, *, include_testers=False, public_ended_ok=False):
    """Queryset kỳ thi mà `user` được phép thao tác trong các trang hcmus."""
    if _is_contest_admin(user):
        return Contest.objects.all()

    profile = user.profile
    cond = Q(authors=profile) | Q(curators=profile)
    if include_testers:
        cond |= Q(testers=profile)
    if public_ended_ok:
        cond |= Q(end_time__lt=timezone.now(), is_visible=True, is_private=False,
                  is_organization_private=False, hide_problem_statements=False)
    return Contest.objects.filter(cond).distinct()


def _contest_or_404(user, key, **kwargs):
    """Lấy kỳ thi theo key NHƯNG chỉ trong phạm vi user được phép (xem R2, R3)."""
    return get_object_or_404(_contests_for(user, **kwargs), key=key)


@staff_only
def resolver_index(request):
    rows = []
    for c in _contests_for(request.user).order_by('-end_time')[:30]:
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
    contest = _contest_or_404(request.user, contest_key)
    return JsonResponse(get_payload(contest, refresh=request.GET.get('refresh') == '1'))


@staff_only
def resolver(request, contest_key):
    contest = _contest_or_404(request.user, contest_key)
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
    for c in _statement_contests(request.user).order_by('-start_time')[:40]:
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


def _statement_contests(user):
    """Kỳ thi mà `user` được xuất/tải đề bài (xem luật ở đầu mục phân quyền)."""
    return _contests_for(user, include_testers=True, public_ended_ok=True)


def _pdf_cache():
    from hcmus.tasks import cache_dir
    return cache_dir()


def _statement_build(request):
    contest_key = (request.POST.get('contest') or '').strip()
    # Kiểm ngay ở đây chứ không chỉ ở lúc tải về: build ghi PDF vào cache dùng
    # chung, để người không có quyền kích hoạt được là đã sai.
    if not _statement_contests(request.user).filter(key=contest_key).exists():
        return HttpResponseBadRequest('contest không tồn tại hoặc bạn không có quyền')

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
    # Lấy qua queryset đã lọc (R2): file PDF nằm trong cache dùng chung nên nếu
    # chỉ kiểm "contest có tồn tại không" thì ai cũng tải được đề của người khác.
    _contest_or_404(request.user, contest_key, include_testers=True, public_ended_ok=True)
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


# ---------------------------------------------------------------------------
# Cấp / đổi mật khẩu tài khoản hàng loạt qua web. Gói lõi ở hcmus/accounts.py
# (tạo/đổi) và hcmus/slips.py (PDF). Xem docs/06 §2.7.
# ---------------------------------------------------------------------------

def _may_manage_accounts(user):
    """Được VÀO trang cấp tài khoản: mọi nhân viên. Việc tạo tài khoản mới, in
    phiếu và cấp quyền vào kỳ thi là việc chung của ban tổ chức."""
    return user.is_active and user.is_staff


def _may_edit_accounts(user):
    """Được SỬA tài khoản đã tồn tại: đổi tên đăng nhập, ghi đè email, đổi mật
    khẩu, đổi họ tên/tổ chức/quyền của người khác — và cấp cờ 'nhân viên'.

    Nhân viên thường chỉ tạo mới được. Sửa thông tin tài khoản người khác là việc
    của người có quyền quản trị tài khoản, vì sửa được thì cũng chiếm được: đổi
    email của một tài khoản sang email mình rồi bấm quên mật khẩu là vào được.
    """
    return user.is_active and (user.is_superuser or
                               user.has_perm('hcmus.manage_accounts') or
                               user.has_perm('auth.change_user'))


def _grantable_groups(user):
    """Các nhóm quyền mà `user` được phép gán cho tài khoản mới.

    Luật: KHÔNG ai cấp được thứ mình không có. Superuser thấy hết; người khác chỉ
    thấy đúng những nhóm chính họ đang thuộc. Nếu không giới hạn, một giảng viên
    có quyền tạo tài khoản sẽ tự tạo được một tài khoản khác rồi cấp cho nó nhóm
    cao hơn mình — đường vòng để leo quyền.
    """
    from django.contrib.auth.models import Group
    if user.is_superuser:
        return list(Group.objects.order_by('name'))
    return list(user.groups.order_by('name'))


def _eligible_contests(user):
    """Contest để tích chọn cấp quyền vào: sắp mở / đang chạy / vừa xong, và người
    dùng có quyền sửa. Trả về list dict cho template."""
    import datetime

    from judge.models import Contest
    now = timezone.now()
    qs = (Contest.objects.filter(end_time__gte=now - datetime.timedelta(days=14),
                                 start_time__lte=now + datetime.timedelta(days=30))
          .order_by('start_time'))
    out = []
    for c in qs:
        if not (user.is_superuser or c.is_editable_by(user)):
            continue
        status = 'sắp mở' if now < c.start_time else ('đang chạy' if now <= c.end_time else 'vừa xong')
        out.append({'key': c.key, 'name': c.name, 'start': c.start_time,
                    'is_private': c.is_private, 'status': status})
    return out


def accounts_page(request):
    from hcmus import accounts as acc_mod
    """Trang quản trị: dán CSV/text hoặc tải file, chọn tạo-mới / đổi-mật-khẩu,
    bấm một nút -> chạy trên server -> trả về file ZIP gồm CSV mật khẩu + PDF phiếu.

    Gác quyền: _may_manage_accounts (tạo mới, in phiếu) / _may_edit_accounts (đổi
    mật khẩu, sửa tài khoản đã có). Lõi ở accounts.run_batch còn chặn cứng không
    đụng tài khoản quản trị.

    Hai cờ can_create/can_reset phải bám đúng hai hàm đó. Có thời chúng bám vào
    auth.add_user/auth.change_user, mà hai quyền Django này không cấp cho ai (xem
    docs/10) — thành ra nhân viên vào trang chỉ thấy mỗi mục in phiếu."""
    if not _may_manage_accounts(request.user):
        raise PermissionDenied()

    ctx = {
        'title': _('Bulk accounts'),
        'can_create': True,     # tới được đây nghĩa là đã qua _may_manage_accounts
        'can_reset': _may_edit_accounts(request.user),
        'default_url': request.build_absolute_uri('/').rstrip('/'),
        'contests': _eligible_contests(request.user),
        'grantable_groups': _grantable_groups(request.user),
        'may_edit_accounts': _may_edit_accounts(request.user),
        'mail_subject_default': acc_mod.DEFAULT_MAIL_SUBJECT,
        'mail_body_default': acc_mod.DEFAULT_MAIL_BODY,
    }

    if request.method != 'POST':
        return render(request, 'hcmus/accounts.html', ctx)

    import io
    import zipfile

    from hcmus import accounts as acc
    from hcmus import badges, slips

    mode = request.POST.get('mode', 'create')
    if mode not in ('create', 'reset', 'slips'):
        mode = 'create'
    # slips = chỉ in phiếu, KHÔNG đụng DB (dùng khi tài khoản nằm ở contest/trang
    # khác). Chỉ cần quyền mở trang; create/reset mới cần quyền ghi tài khoản.
    if mode in ('create', 'reset'):
        # 'reset' là đổi mật khẩu người khác -> phải có quyền quản trị tài khoản.
        if mode == 'reset' and not _may_edit_accounts(request.user):
            raise PermissionDenied()

    # Nguồn dữ liệu: ưu tiên file tải lên, không thì ô dán text.
    text = ''
    upload = request.FILES.get('file')
    if upload is not None:
        if upload.size > 512 * 1024:
            ctx['error'] = _('Tệp lớn hơn 512 KB.')
            return render(request, 'hcmus/accounts.html', ctx)
        raw = upload.read()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('latin-1')
    else:
        text = request.POST.get('text', '')

    if not text.strip():
        ctx['error'] = _('Chưa có dữ liệu: dán danh sách vào ô hoặc tải file lên.')
        return render(request, 'hcmus/accounts.html', ctx)

    if mode == 'slips':
        # Không tạo/đổi gì: đọc thẳng danh sách rồi in phiếu (cần có sẵn password).
        results = acc.parse_rows(text)
        for r in results:
            r['status'] = 'chỉ in phiếu'
    else:
        # Chỉ nhận những nhóm mà CHÍNH người này được phép cấp — không tin POST.
        allowed = {g.name: g for g in _grantable_groups(request.user)}
        chosen = [allowed[n] for n in request.POST.getlist('groups') if n in allowed]
        send_activation = request.POST.get('send_activation') == 'on'
        may_edit = _may_edit_accounts(request.user)
        try:
            results = acc.run_batch(
                text, mode,
                org_slug=request.POST.get('org', '').strip(),
                display_name=bool(request.POST.get('display_name')),
                email_domain=request.POST.get('email_domain', '').strip(),
                send_activation=send_activation,
                base_url=ctx['default_url'],
                groups=chosen,
                mail_subject=request.POST.get('mail_subject', '').strip(),
                mail_body=request.POST.get('mail_body', ''),
                may_update=may_edit,
                # Cờ nhân viên chỉ người quản trị tài khoản mới cấp được: cấp nó
                # là mở cửa vào trang quản trị, không phải thứ ai cũng phát được.
                make_staff=may_edit and request.POST.get('make_staff') == 'on',
            )
        except ValueError as e:
            # Mẻ bị chặn từ đầu (thiếu email mà lại tích gửi thư). Hiện thành cảnh
            # báo trên trang, giữ nguyên nội dung người dùng đã dán để họ sửa.
            ctx['error'] = str(e)
            ctx['keep_text'] = text
            return render(request, 'hcmus/accounts.html', ctx)
    if not results:
        ctx['error'] = _('Không đọc được dòng hợp lệ nào (cần ít nhất cột username).')
        return render(request, 'hcmus/accounts.html', ctx)

    # Cấp quyền vào contest được tích (private_contestants) — mọi chế độ đều có.
    # Chỉ thêm quyền vào, KHÔNG tạo lượt thi, KHÔNG đụng scoreboard.
    contest_note = ''
    contest_keys = request.POST.getlist('contests')
    if contest_keys:
        from judge.models import Contest
        contests = [c for c in Contest.objects.filter(key__in=contest_keys)
                    if request.user.is_superuser or c.is_editable_by(request.user)]
        usernames = [r['username'] for r in results if r.get('username')]
        added = acc.add_users_to_contests(usernames, contests)
        lines = ['Cấp quyền vào contest (private_contestants, không tạo lượt thi):']
        for contest, n, note in added:
            lines.append(f'  {contest.key}: +{n} user' + (f'   [{note}]' if note else ''))
        contest_note = '\n'.join(lines) + '\n'

    csv_text = acc.results_csv(results)
    # Mặc định KHÔNG tạo phiếu: phần lớn lần chạy chỉ để tạo tài khoản + gửi thư,
    # mà dựng PDF cho vài trăm người thì chậm và ra file to không ai dùng.
    # Mặc định KHÔNG tạo phiếu: phần lớn lần chạy chỉ để tạo tài khoản + gửi thư,
    # mà dựng PDF cho vài trăm người thì chậm và ra file to không ai dùng tới.
    want_slips = request.POST.get('want_slips') == 'on'
    pdf_bytes = badge_bytes = tent_bytes = None
    if want_slips:
        # Tên kỳ thi in trên thẻ đeo và bảng tên: lấy ô "kỳ thi" của form, không
        # có thì lấy tiêu đề phiếu.
        event_name = (request.POST.get('slip_contest', '').strip()
                      or request.POST.get('slip_title', '').strip()
                      or 'FIT-HCMUS Online Judge')
        try:
            badge_copies = int(request.POST.get('badge_copies') or 3)
        except ValueError:
            badge_copies = 3
        try:
            pdf_bytes = slips.make_slips_pdf(
                results,
                title=request.POST.get('slip_title', '').strip() or 'FIT-HCMUS Online Judge',
                contest=request.POST.get('slip_contest', '').strip(),
                url=request.POST.get('slip_url', '').strip() or ctx['default_url'],
            )
            badge_bytes = badges.make_badges_pdf(results, event=event_name, copies=badge_copies)
            tent_bytes = badges.make_tents_pdf(results, event=event_name)
        except Exception as e:  # noqa: BLE001  thiếu font/thư viện thì vẫn trả CSV
            csv_text += f'\n# Không tạo được PDF: {e}\n'

    changed = sum(1 for r in results if r['password'])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('tai-khoan.csv', csv_text.encode('utf-8'))
        if pdf_bytes:
            z.writestr('phieu-dang-nhap.pdf', pdf_bytes)
        # Thẻ đeo + bảng tên để bàn dùng chung nguồn dữ liệu với phiếu, nên gói
        # luôn để ban tổ chức chỉ phải tải một lần. Hai thứ này chỉ cần tên đội
        # nên chế độ chỉ-in-phiếu (không đụng mật khẩu) vẫn có.
        if badge_bytes:
            z.writestr('the-deo-ten.pdf', badge_bytes)
        if tent_bytes:
            z.writestr('bang-ten-ban.pdf', tent_bytes)
        if contest_note:
            z.writestr('cap-quyen-contest.txt', contest_note.encode('utf-8'))
    buf.seek(0)
    resp = HttpResponse(buf.getvalue(), content_type='application/zip')
    prefix = {'create': 'tao-moi', 'reset': 'doi-matkhau', 'slips': 'phieu'}[mode]
    resp['Content-Disposition'] = f'attachment; filename="{prefix}-{changed}-tk.zip"'
    return resp


# ---------------------------------------------------------------------------
# In mã nguồn bài nộp cho thí sinh trong giờ thi (luật ICPC). Chỉ in submission
# của chính mình, thuộc contest ĐANG diễn ra; chặn >10 trang (từ chối ngay);
# in thẳng máy in đang chọn, không cần giám thị duyệt. Xem docs/06 §2.8.
# ---------------------------------------------------------------------------

@require_POST
def print_submission(request, submission):
    from judge.models import Submission

    from hcmus import printing
    from hcmus.models import ContestPrinter, PrintRequest, TeamRoom

    if not request.user.is_authenticated:
        raise PermissionDenied()
    sub = get_object_or_404(
        Submission.objects.select_related('user__user', 'problem', 'language', 'source'),
        id=submission)
    profile = request.user.profile

    def result(ok, message):
        return render(request, 'hcmus/print-result.html',
                      {'title': _('In bài'), 'ok': ok, 'message': message, 'submission': sub})

    # Chỉ chủ nhân bài, và phải là bài của contest đang diễn ra mà họ đang dự.
    if sub.user_id != profile.id:
        raise PermissionDenied()
    cp = profile.current_contest
    if cp is None or cp.ended or sub.contest_object_id != cp.contest_id:
        return result(False, _('Chỉ in được bài bạn đã nộp trong kỳ thi đang diễn ra.'))

    team = profile.display_name
    room = TeamRoom.room_of(profile.id)
    try:
        pdf, pages = printing.render_submission_pdf(sub, team, room)
    except Exception as e:  # noqa: BLE001
        return result(False, _('Không dựng được bản in: %s. Báo giám thị.') % e)

    limit = getattr(settings, 'HCMUS_PRINT_PAGE_LIMIT', printing.PAGE_LIMIT_DEFAULT)
    common = dict(profile=profile, submission=sub, contest_id=cp.contest_id, team=team, room=room,
                  problem=sub.problem.name[:100], language=sub.language.name[:40], pages=pages)

    if pages > limit:
        PrintRequest.objects.create(status=PrintRequest.REJECTED, **common)
        return result(False, _('Bài in dài %(p)d trang, vượt trần %(l)d trang nên KHÔNG in. '
                               'Hãy in gọn lại (bỏ phần thừa).') % {'p': pages, 'l': limit})

    # Máy in do TỪNG kỳ thi chọn (admin → sửa contest). Không chọn = kỳ đó không in.
    printer = ContestPrinter.printer_for(cp.contest_id)
    if printer is None:
        return result(False, _('Kỳ thi này chưa bật in bài (giám thị chưa chọn máy in).'))

    pr = PrintRequest.objects.create(status=PrintRequest.QUEUED, printer=printer.name, **common)
    ok, msg = printing.send_to_printer(pdf, printer.cups_dest, job_name=f'{team}-{sub.problem.code}')
    pr.status = PrintRequest.PRINTED if ok else PrintRequest.FAILED
    pr.error = '' if ok else msg
    pr.save(update_fields=['status', 'error'])
    if ok:
        return result(True, _('Đã gửi bài đến máy in (%(p)d trang). Giám thị sẽ mang bản in tới bàn của bạn.')
                      % {'p': pages})
    return result(False, _('Gửi máy in lỗi: %s. Báo giám thị.') % msg)


# ---------------------------------------------------------------- tìm teammate

class TeammatePostForm(forms.ModelForm):
    """Form đăng/sửa mẩu tin tìm teammate.

    KHÔNG có field `status`: trạng thái đổi bằng nút riêng (teammate_status) để
    một cú bấm là xong, không phải mở form sửa rồi lưu lại.
    """
    class Meta:
        model = TeammatePost
        fields = ['display_name', 'cohort', 'strengths', 'achievements',
                  'contact', 'note', 'team_name']
        widgets = {
            'achievements': forms.Textarea(attrs={'rows': 3}),
            'note': forms.Textarea(attrs={'rows': 3}),
        }


def teammate_board(request):
    """Bảng tin tìm teammate: ai cũng đọc được, đăng nhập mới đăng được.

    Mỗi người một mẩu tin nên form vừa dùng để tạo vừa dùng để sửa (instance là
    mẩu tin sẵn có nếu đã đăng).
    """
    mine = None
    if request.user.is_authenticated:
        mine = TeammatePost.objects.filter(profile=request.user.profile).first()

    if request.method == 'POST':
        if not request.user.is_authenticated:
            raise PermissionDenied()
        form = TeammatePostForm(request.POST, instance=mine)
        if form.is_valid():
            post = form.save(commit=False)
            post.profile = request.user.profile
            post.save()
            return HttpResponseRedirect(reverse('hcmus_teammate') + '#cua-toi')
    else:
        initial = {}
        if mine is None and request.user.is_authenticated:
            profile = request.user.profile
            initial['display_name'] = (profile.username_display_override
                                       or request.user.first_name or request.user.username)
        form = TeammatePostForm(instance=mine, initial=initial)

    posts = TeammatePost.board()
    only_looking = request.GET.get('loc') == 'dang-tim'
    if only_looking:
        posts = posts.filter(status=TeammatePost.LOOKING)
    posts = list(posts)

    return render(request, 'hcmus/teammate.html', {
        'title': 'Tìm teammate',
        'posts': posts,
        'mine': mine,
        'form': form,
        'only_looking': only_looking,
        'looking_count': TeammatePost.objects.filter(status=TeammatePost.LOOKING).count(),
        'total_count': TeammatePost.objects.count(),
    })


@login_required
@require_POST
def teammate_status(request):
    """Người đăng tự bật/tắt "đã khớp" cho mẩu tin của mình."""
    post = get_object_or_404(TeammatePost, profile=request.user.profile)
    post.status = TeammatePost.LOOKING if post.is_matched else TeammatePost.MATCHED
    if not post.is_matched:
        # Quay lại tìm tiếp thì tên đội cũ không còn đúng nữa.
        post.team_name = ''
    post.save(update_fields=['status', 'team_name', 'modified'])
    return HttpResponseRedirect(reverse('hcmus_teammate') + '#cua-toi')


@login_required
@require_POST
def teammate_delete(request, pk):
    """Xoá mẩu tin. Chủ mẩu tin xoá của mình; staff dọn được mẩu tin rác."""
    post = get_object_or_404(TeammatePost, pk=pk)
    if post.profile_id != request.user.profile.id and not request.user.is_staff:
        raise PermissionDenied()
    post.delete()
    return HttpResponseRedirect(reverse('hcmus_teammate'))


# ------------------------------------------------- bảng xếp hạng công khai

def _public_rows(contest):
    """Dựng bảng xếp hạng để hiện công khai, từ đúng dữ liệu vnoj đã tính.

    Dùng lại build_payload của resolver nên số liệu khớp bảng chính thức. Nếu kỳ
    thi đang ĐÓNG BĂNG thì trang công khai cũng hiện bản đóng băng — công bố sớm
    hơn bảng trong site là hỏng luật ICPC.
    """
    payload = build_payload(contest)
    frozen = contest.is_frozen

    # Ô "giải đầu tiên" của mỗi bài: mốc giây nhỏ nhất trong các ô ĐANG HIỆN.
    # Lúc bảng còn đóng băng thì chỉ xét những ô đã công bố — nếu người giải sớm
    # nhất nằm trong giờ băng thì chưa được lộ ra qua màu.
    first_at = {}
    for team in payload['teams']:
        for idx, cell in enumerate(team['cells']):
            if frozen and cell['pending']:
                continue
            visible_solved = cell['frozen']['solved'] if frozen else cell['final']['solved']
            sec = cell['final']['seconds']
            if visible_solved and sec is not None:
                if idx not in first_at or sec < first_at[idx]:
                    first_at[idx] = sec

    rows = []
    for team in payload['teams']:
        totals = team['frozen'] if frozen else team['final']
        cells = []
        for idx, cell in enumerate(team['cells']):
            if frozen and cell['pending']:
                cells.append({'state': 'pending', 'top': '?',
                              'bottom': cell['final']['tries'] or ''})
            elif (frozen and cell['frozen']['solved']) or (not frozen and cell['final']['solved']):
                tries = cell['frozen']['tries'] if frozen else cell['final']['tries']
                sec = cell['final']['seconds']
                is_first = sec is not None and first_at.get(idx) == sec
                cells.append({'state': 'first' if is_first else 'ac',
                              'top': cell['final']['minutes'],
                              'bottom': f'{tries} lần' if tries else ''})
            else:
                tries = cell['frozen']['tries'] if frozen else cell['final']['tries']
                cells.append({'state': 'wa' if tries else 'none',
                              'top': f'{tries} lần' if tries else '', 'bottom': ''})
        rows.append({'name': team['name'], 'org': team['org'],
                     'solved': totals['solved'], 'penalty': totals['penalty'], 'cells': cells})

    # Xếp hạng: nhiều bài hơn đứng trên, bằng bài thì ít phạt hơn đứng trên.
    rows.sort(key=lambda r: (-r['solved'], r['penalty'], r['name'].lower()))
    rank = 0
    for i, r in enumerate(rows):
        # Cùng số bài và cùng phạt thì cùng hạng (kiểu xếp hạng thi đấu).
        if i and (rows[i - 1]['solved'], rows[i - 1]['penalty']) == (r['solved'], r['penalty']):
            r['rank'] = rank
        else:
            rank = i + 1
            r['rank'] = rank
    return {'problems': payload['contest']['problems'], 'rows': rows, 'frozen': frozen}


def public_scoreboard(request, token):
    """Trang chỉ-đọc, KHÔNG cần đăng nhập, cho kỳ thi riêng tư (xem PublicScoreboard).

    Chỉ có tên đội + kết quả. Không có đề bài, bài nộp, mã nguồn — mở link này
    không mở kỳ thi.
    """
    board = get_object_or_404(PublicScoreboard.objects.select_related('contest'),
                              token=token, is_enabled=True)
    contest = board.contest

    # Cache ngắn: link công khai có thể bị F5 liên tục lúc đang thi, mà dựng bảng
    # phải quét toàn bộ lượt dự thi.
    cache_key = f'hcmus_public_scoreboard_{token}'
    data = cache.get(cache_key)
    if data is None:
        data = _public_rows(contest)
        cache.set(cache_key, data, 20)

    now = timezone.now()
    return render(request, 'hcmus/public-scoreboard.html', {
        'title': f'Bảng xếp hạng — {contest.name}',
        'contest': contest,
        'board': board,
        'problems': data['problems'],
        'rows': data['rows'],
        'frozen': data['frozen'],
        'running': contest.start_time <= now < contest.end_time,
    })


# ------------------------------------------------- trang giới thiệu tĩnh

def landing_page(request, slug):
    """Trang HTML do biên tập viên soạn (xem model LandingPage).

    Đặt CUỐI danh sách URL của hcmus, mà hcmus lại được nối cuối urlpatterns gốc,
    nên chỉ bắt những đường dẫn không khớp trang nào khác — không che mất URL sẵn có.
    """
    page = get_object_or_404(LandingPage, slug=slug, is_visible=True)
    if page.login_required and not request.user.is_authenticated:
        return HttpResponseRedirect(f"{reverse('auth_login')}?next={page.get_absolute_url()}")
    if page.is_full_document:
        # File trọn vẹn tải lên: trả nguyên văn, không nhét vào khung site.
        return HttpResponse(page.html)
    return render(request, 'hcmus/landing.html', {'title': page.title, 'page': page})
