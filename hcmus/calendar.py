"""Lịch trang chủ và hai bảng xếp hạng cột phải (FIT-HCMUS).

Lịch hợp nhất HAI nguồn lúc đọc, không nhân bản:
  - Kỳ thi: Contest.get_visible_contests(user) — tạo contest là tự lên lịch.
  - Sự kiện tay: CalendarEvent.visible_to(user) — ICPC vùng, tập huấn, họp ban...

Cả hai đều lọc theo đúng người đang xem. Feed .ics công khai thì CHỈ lấy phần
công khai (contest công khai + sự kiện PUBLIC), vì công cụ lịch fetch feed không
mang phiên đăng nhập nên không thể phân quyền theo người ở đó.
"""
from django.utils import timezone

from hcmus.models import CalendarEvent
from judge.models import Contest
from judge.ratings import RATING_INIT


# --- Lịch hiển thị trên web (lọc theo user) --------------------------------

def _contest_items(user, since, until):
    qs = (Contest.get_visible_contests(user)
          .filter(is_visible=True, start_time__lt=until, end_time__gte=since)
          .order_by('start_time'))
    out = []
    for c in qs:
        out.append({
            'kind': 'contest',
            'category': 'contest',
            'title': c.name,
            'start': c.start_time,
            'end': c.end_time,
            'all_day': False,
            'url': c.get_absolute_url(),
        })
    return out


def _event_items(user, since, until):
    # effective_end là property (không lọc DB được), nên lọc thô theo start_time
    # rồi loại phần đã kết thúc trong Python. Số sự kiện tay vốn nhỏ.
    qs = (CalendarEvent.visible_to(user)
          .filter(start_time__lt=until)
          .prefetch_related('organizations')
          .order_by('start_time'))
    out = []
    for e in qs:
        if e.effective_end < since:
            continue
        out.append({
            'kind': 'event',
            'category': e.category,
            'title': e.title,
            'start': e.start_time,
            'end': e.end_time,
            'all_day': e.all_day,
            'url': e.url or '',
            'location': e.location,
            'obj': e,
        })
    return out


def agenda(user, days_back=1, days_ahead=365):
    """Danh sách sự kiện (contest + tay) mà user được xem, sắp theo thời gian.
    Dùng cho trang lịch và cho sidebox. Mỗi phần tử là dict thống nhất hai nguồn.

    Cửa sổ mặc định một năm: mùa giải ICPC/Olympic trải dài cả năm học, cắt ngắn
    hơn sẽ giấu mất cụm sự kiện tháng 11-12 (Vòng Quốc gia, ICPC Đà Nẵng, OLP)."""
    now = timezone.now()
    since = now - timezone.timedelta(days=days_back)
    until = now + timezone.timedelta(days=days_ahead)
    items = _contest_items(user, since, until) + _event_items(user, since, until)
    items.sort(key=lambda it: it['start'])
    return items


def upcoming(user, limit=6):
    """Vài sự kiện sắp tới cho sidebox trang chủ. Chỉ lấy cái CHƯA kết thúc."""
    now = timezone.now()
    items = [it for it in agenda(user, days_back=0, days_ahead=365)
             if (it['end'] or it['start']) >= now]
    return items[:limit]


# --- Feed .ics công khai (không phân quyền, chỉ phần PUBLIC) ----------------

def public_ical(request):
    """VCALENDAR gộp contest công khai + CalendarEvent công khai.

    Chỉ phần công khai: feed này phục vụ cho công cụ lịch của bên thứ ba (Google,
    Apple) fetch không kèm phiên đăng nhập, nên không có cách nào phân quyền theo
    người ở tầng này. Lịch riêng chỉ hiện trên web.
    """
    from icalendar import Calendar as ICalendar, Event

    cal = ICalendar()
    cal.add('prodid', '-//FIT-HCMUS//Online Judge//VI')
    cal.add('version', '2.0')
    cal.add('x-wr-calname', 'FIT-HCMUS Online Judge')

    now = timezone.now().astimezone(timezone.utc)
    domain = request.get_host()

    for c in Contest.get_public_contests().order_by('start_time'):
        ev = Event()
        # UID phải ỔN ĐỊNH qua mọi lần render, nếu không client tạo bản trùng thay
        # vì cập nhật. Khoá bền vững: key của contest + domain.
        ev.add('uid', 'contest-%s@%s' % (c.key, domain))
        ev.add('summary', c.name)
        ev.add('location', request.build_absolute_uri(c.get_absolute_url()))
        ev.add('dtstart', c.start_time.astimezone(timezone.utc))
        ev.add('dtend', c.end_time.astimezone(timezone.utc))
        ev.add('dtstamp', now)
        cal.add_component(ev)

    for e in CalendarEvent.objects.filter(visibility=CalendarEvent.PUBLIC).order_by('start_time'):
        ev = Event()
        ev.add('uid', 'calevent-%d@%s' % (e.pk, domain))
        ev.add('summary', e.title)
        if e.description:
            ev.add('description', e.description)
        if e.location:
            ev.add('location', e.location)
        if e.all_day:
            # DATE, và DTEND là ngày kế tiếp vì mốc kết thúc không bao gồm.
            ev.add('dtstart', e.start_time.date())
            end = (e.end_time or e.start_time).date() + timezone.timedelta(days=1)
            ev.add('dtend', end)
        else:
            ev.add('dtstart', e.start_time.astimezone(timezone.utc))
            ev.add('dtend', e.effective_end.astimezone(timezone.utc))
        ev.add('dtstamp', now)
        cal.add_component(ev)

    return cal.to_ical()


# --- Bảng xếp hạng: top tăng rating trong tuần -----------------------------

def weekly_rating_gain(limit=10, days=7):
    """(profile, mức tăng) của những người TĂNG RATING nhiều nhất trong `days` ngày.

    Rating (judge/models/contest.py) lưu một dòng cho mỗi cặp user–contest, có
    `rating` sau kỳ đó và `last_rated` đã index. Nên tính được mà không cần bảng
    lịch sử mới:
      - mức tăng = rating hiện tại − rating ngay TRƯỚC cửa sổ tuần.
      - người chưa từng được rate trước tuần này (mới thi lần đầu) lấy mốc so là
        RATING_INIT (1200) — mức khởi điểm của tân binh.
    Chỉ tính người ĐÃ được rate trong cửa sổ, tức thật sự có hoạt động tuần này.
    """
    from judge.models import Profile, Rating

    window_start = timezone.now() - timezone.timedelta(days=days)

    rated_recently = set(
        Rating.objects.filter(last_rated__gte=window_start)
        .values_list('user_id', flat=True))
    if not rated_recently:
        return []

    # Rating ngay trước cửa sổ, cho từng người. Sắp giảm dần theo last_rated rồi
    # lấy dòng đầu mỗi user = rating gần nhất TRƯỚC tuần.
    before = {}
    for uid, r in (Rating.objects
                   .filter(user_id__in=rated_recently, last_rated__lt=window_start)
                   .order_by('user_id', '-last_rated')
                   .values_list('user_id', 'rating')):
        before.setdefault(uid, r)

    rows = []
    profiles = (Profile.objects.filter(id__in=rated_recently, is_unlisted=False,
                                       rating__isnull=False)
                .select_related('user', 'display_badge')
                .only('user', 'rating', 'display_rank', 'display_badge',
                      'username_display_override'))
    for p in profiles:
        gain = p.rating - before.get(p.id, RATING_INIT)
        rows.append((p, gain))

    # Chỉ khoe người TĂNG (gain > 0); tụt rating không phải thứ để lên bảng vàng.
    rows = [t for t in rows if t[1] > 0]
    rows.sort(key=lambda t: -t[1])
    return rows[:limit]
