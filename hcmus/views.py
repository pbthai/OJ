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
from django.http import Http404, HttpResponse, JsonResponse

from judge.models import Contest, ContestParticipation

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
