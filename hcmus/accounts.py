"""Cấp / đổi mật khẩu tài khoản hàng loạt (dùng cho trang web quản trị).

Tách lõi từ myScript/server/create_accounts.py thành hàm gọi được từ view. Hai
chế độ:
  - create: tạo tài khoản MỚI; username đã có thì bỏ qua. Đặt luôn tên hiển thị
            (tuỳ chọn), tổ chức theo trường, email.
  - reset : CHỈ đổi mật khẩu tài khoản đã có, giữ nguyên mọi thứ khác; username
            chưa tồn tại thì bỏ qua.

An toàn: TUYỆT ĐỐI không đụng tài khoản is_staff/is_superuser — không để công cụ
này thành đường đặt lại mật khẩu của quản trị viên rồi chiếm quyền. Việc gọi hàm
còn được gác thêm bằng quyền auth.add_user (create) / auth.change_user (reset) ở
tầng view.

Chỉ đọc/ghi bảng tài khoản (User/Profile/Organization). KHÔNG đụng submission,
scoreboard, điểm hay rating — đổi mật khẩu không làm thay đổi xếp hạng.
"""
import csv
import re
import io
import secrets

from django.contrib.auth.models import User
from django.db import transaction
from django.utils.text import slugify

# Chỉ chữ số 2-9: bỏ 0/1 để khỏi lẫn với o/O/i/l/I khi đọc trên phiếu in.
PASS_ALPHABET = '23456789'
PASS_LEN = 9

HEADER_ALIASES = {
    'username': 'username', 'user': 'username', 'account': 'username',
    'password': 'password', 'pass': 'password', 'matkhau': 'password',
    'name': 'name', 'fullname': 'name', 'hoten': 'name', 'team': 'name',
    'school': 'school', 'truong': 'school', 'org': 'school', 'donvi': 'school',
    'email': 'email', 'mail': 'email', 'thu': 'email',
    # Phòng thi: CHỈ để in lên phiếu (xếp phiếu theo phòng). Không đụng tài khoản.
    'room': 'room', 'phong': 'room', 'phongthi': 'room', 'lab': 'room',
}
COLS = ['username', 'password', 'name', 'school', 'email', 'room']

# Bộ ký tự tên đăng nhập, lấy theo mặc định của Django. Cốt để chặn khoảng trắng
# và tab: dán nhầm định dạng thì cả dòng thành một "tên đăng nhập" khổng lồ.
USERNAME_RE = re.compile(r'^[\w.@+-]+$')


def gen_pass():
    return ''.join(secrets.choice(PASS_ALPHABET) for _ in range(PASS_LEN))


def _norm_header(cell):
    return HEADER_ALIASES.get(cell.strip().lower().replace(' ', '').replace('_', ''))


def parse_rows(text):
    """Chuỗi CSV (dán tay hoặc từ file) -> list dict {username,password,name,school,email}.

    Tự nhận header (có cột 'username' thì map theo tên cột, không thì hiểu theo thứ
    tự username,password,name,school,email). Dòng thiếu username thì bỏ.

    Nhận cả dấu phẩy lẫn TAB làm dấu ngăn cột, vì bôi đen trên Excel rồi dán ra
    TAB chứ không ra phẩy. KHÔNG nhận ';' — dấu đó đang dùng để ngăn nhiều tổ
    chức trong cùng một ô (xem split_orgs), nhận luôn thì hai cách hiểu đá nhau.
    """
    head = next((ln for ln in text.splitlines() if ln.strip()), '')
    reader = csv.reader(io.StringIO(text), delimiter='\t' if '\t' in head else ',')
    rows = [r for r in reader if r and any(c.strip() for c in r)]
    if not rows:
        return []
    first = [_norm_header(c) for c in rows[0]]
    if 'username' in first:
        mapping = {name: i for i, name in enumerate(first) if name}
        rows = rows[1:]
    else:
        mapping = {name: i for i, name in enumerate(COLS)}
    out = []
    for r in rows:
        def cell(key):
            i = mapping.get(key)
            return r[i].strip() if i is not None and i < len(r) else ''
        if not cell('username'):
            continue
        out.append({k: cell(k) for k in COLS})
    return out


def split_orgs(value):
    """Tách danh sách tổ chức. Dùng ';' hoặc '|' làm dấu phân cách chứ KHÔNG dùng
    dấu phẩy — file là CSV, dấu phẩy đã là dấu ngăn cột."""
    out = []
    for part in (value or '').replace('|', ';').split(';'):
        part = part.strip()
        if part and part not in out:
            out.append(part)
    return out


def _school_org(name, cache):
    """get_or_create Organization theo tên trường (so khớp không phân biệt hoa thường)."""
    from judge.models import Organization
    key = name.lower()
    if key in cache:
        return cache[key]
    org = Organization.objects.filter(name__iexact=name).first()
    if org is None:
        slug = slugify(name)[:128] or 'school'
        if not slug[0].isalpha():
            slug = 'org-' + slug
        base, n = slug, 2
        while Organization.objects.filter(slug=slug).exists():
            slug, n = f'{base}-{n}', n + 1
        org = Organization.objects.create(
            slug=slug, name=name, short_name=name[:20], about=name, is_open=False)
    cache[key] = org
    return org


# --- Mã sinh viên -> email + tổ chức -------------------------------------
# Mã sinh viên HCMUS là 8 chữ số (kiểm trên dữ liệu thật: 24120438, 24120040...),
# email trường cấp là <mã>@student.hcmus.edu.vn. Giảng viên KHÔNG có mẫu nào đoán
# được nên bắt buộc phải nhập email tay — đó là lý do hàm này chỉ nhận toàn số.
STUDENT_ID_RE = re.compile(r'^\d{8}$')
STUDENT_EMAIL_DOMAIN = 'student.hcmus.edu.vn'
STUDENT_ORG_SLUG = 'hcmus'


def is_student_id(username):
    return bool(STUDENT_ID_RE.match((username or '').strip()))


def resolve_email(email, username, email_domain=''):
    """Email dùng cho tài khoản: ưu tiên cột email, rồi tới mã sinh viên, rồi domain
    do người chạy nhập. Trả về '' nếu không suy ra được."""
    email = (email or '').strip()
    if email:
        return email
    if is_student_id(username):
        return f'{username}@{STUDENT_EMAIL_DOMAIN}'
    if email_domain:
        return f'{username}@{email_domain}'
    return ''


def rows_without_email(rows, email_domain=''):
    """Các dòng không thể gửi mail kích hoạt: không có email và cũng không phải mã
    sinh viên. Dùng để CHẶN TRƯỚC cả mẻ thay vì tạo được một nửa rồi mới báo."""
    return [r['username'] for r in rows
            if not resolve_email(r['email'], r['username'], email_domain)]


def activation_link(user, base_url):
    """Link để người dùng tự đặt mật khẩu.

    Thực chất là link đặt lại mật khẩu của Django, nhưng thư gửi đi gọi là 'kích
    hoạt tài khoản' cho người nhận dễ hiểu. Hạn dùng theo PASSWORD_RESET_TIMEOUT
    (đặt 60 ngày ở local_settings) và tự hết hiệu lực ngay khi người dùng đặt xong mật khẩu.
    """
    from django.contrib.auth.tokens import default_token_generator
    from django.urls import reverse
    from django.utils.encoding import force_bytes
    from django.utils.http import urlsafe_base64_encode
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    return base_url.rstrip('/') + reverse('password_reset_confirm',
                                          kwargs={'uidb64': uid, 'token': token})


# Bản nháp mặc định của thư kích hoạt. Người tạo tài khoản sửa được trên form;
# các chỗ {ten} {username} {link} {site} sẽ được thay bằng dữ liệu thật.
# Thay bằng str.replace chứ KHÔNG dùng .format(): người dùng sửa thư có thể gõ dấu
# ngoặc nhọn cho mục đích khác, .format() gặp là ném lỗi giữa lúc gửi cả mẻ.
MAIL_PLACEHOLDERS = ('{ten}', '{username}', '{link}', '{site}')

DEFAULT_MAIL_SUBJECT = 'Kích hoạt tài khoản {site}'

DEFAULT_MAIL_BODY = """Chào {ten},

Tài khoản của bạn trên {site} đã được tạo:

    Tên đăng nhập: {username}

Bấm vào liên kết dưới đây để kích hoạt tài khoản và tự đặt mật khẩu:

    {link}

Liên kết có hạn 60 ngày. Nếu hết hạn, vào trang đăng nhập và bấm "Quên mật khẩu"
để nhận liên kết mới.

Nếu bạn không yêu cầu tài khoản này, hãy bỏ qua thư.

{site}
"""


def render_mail(text, user, link, site_name):
    """Thay các chỗ giữ chỗ trong mẫu thư bằng dữ liệu thật."""
    return (text.replace('{ten}', user.first_name or user.username)
                .replace('{username}', user.username)
                .replace('{link}', link)
                .replace('{site}', site_name))


def send_activation_mail(user, base_url, subject='', body='',
                         site_name='FIT-HCMUS Online Judge'):
    """Gửi thư kích hoạt. subject/body để trống thì dùng bản nháp mặc định."""
    from django.conf import settings
    from django.core.mail import send_mail
    link = activation_link(user, base_url)
    send_mail(subject=render_mail(subject or DEFAULT_MAIL_SUBJECT, user, link, site_name),
              message=render_mail(body or DEFAULT_MAIL_BODY, user, link, site_name),
              from_email=settings.DEFAULT_FROM_EMAIL,
              recipient_list=[user.email], fail_silently=False)
    return True


def _orgs_for(school, extra_orgs, username, cache):
    """Toàn bộ tổ chức cần gắn cho một tài khoản, theo thứ tự:
      1. cột `school` trong CSV — cho phép NHIỀU, ngăn bằng ';' hoặc '|';
      2. các tổ chức chọn trên form (ô "thêm vào tổ chức"), cũng cho phép nhiều;
      3. hcmus nếu tên đăng nhập là mã sinh viên.
    Trả về danh sách đã khử trùng, giữ nguyên thứ tự.
    """
    from judge.models import Organization
    out = []
    for name in split_orgs(school):
        out.append(_school_org(name, cache))
    for org in (extra_orgs or []):
        out.append(org)
    if is_student_id(username):
        org = Organization.objects.filter(slug=STUDENT_ORG_SLUG).first()
        if org is not None:
            out.append(org)
    seen, uniq = set(), []
    for o in out:
        if o.pk not in seen:
            seen.add(o.pk)
            uniq.append(o)
    return uniq


def run_batch(text, mode, org_slug='', display_name=False, email_domain='',
              send_activation=False, base_url='', groups=(),
              mail_subject='', mail_body='', may_update=True, make_staff=False):
    """Chạy một mẻ tạo/đổi mật khẩu. Trả về list dict kết quả, mỗi dòng có thêm
    'status' (và 'password' = mật khẩu mới, rỗng nếu dòng bị bỏ qua).

    send_activation: gửi thư để người dùng tự đặt mật khẩu (xem send_activation_mail).
      Bật thì mọi dòng PHẢI suy ra được email, nếu không cả mẻ bị chặn từ đầu —
      tạo được một nửa rồi mới báo lỗi thì dọn rất mệt.
    groups: các Group gán cho tài khoản mới tạo VÀ tài khoản được cập nhật (chỉ
      THÊM, không gỡ nhóm sẵn có).
    may_update: người chạy có quyền SỬA tài khoản đã tồn tại không. Tắt thì chỉ tạo
      mới được; dòng nào trùng email/tên đăng nhập sẽ bị bỏ qua kèm lý do. Nhân
      viên thường chỉ được tạo mới, còn sửa thông tin người khác là việc của người
      có quyền quản trị tài khoản.
    make_staff: đặt cờ 'tình trạng nhân viên' cho tài khoản MỚI TẠO.
    """
    from judge.models import Language, Organization, Profile
    if mode not in ('create', 'reset'):
        raise ValueError('mode phải là create hoặc reset')

    rows = parse_rows(text)
    if send_activation and mode == 'create':
        thieu = rows_without_email(rows, email_domain)
        if thieu:
            raise ValueError(
                'Đã tích "gửi email kích hoạt" nhưng {} dòng không có email và cũng '
                'không phải mã sinh viên (8 chữ số): {}. Giảng viên không đoán được '
                'email từ tên đăng nhập nên phải nhập tay. Chưa tạo tài khoản nào.'
                .format(len(thieu), ', '.join(thieu[:10]) + ('...' if len(thieu) > 10 else '')))
    # Ô 'thêm vào tổ chức' nhận NHIỀU slug, ngăn bằng dấu cách, phẩy hoặc ';'.
    wanted = [x for x in re.split(r'[\s,;|]+', org_slug or '') if x]
    extra_org = list(Organization.objects.filter(slug__in=wanted)) if wanted else []
    lang = Language.get_default_language()
    org_cache = {}
    results = []
    to_notify = []          # tài khoản mới tạo / vừa đổi tên -> gửi thư kích hoạt

    for row in rows:
        username = row['username']
        name, school, room = row['name'], row['school'], row['room']
        email = resolve_email(row['email'], username, email_domain)
        password = row['password'] or gen_pass()
        skip = dict(row, password='', status='')
        if not USERNAME_RE.match(username):
            results.append({**skip, 'status':
                            'LỖI: tên đăng nhập có ký tự lạ (khoảng trắng, tab...). '
                            'Kiểm lại dấu ngăn cột của dữ liệu dán vào.'})
            continue
        try:
            with transaction.atomic():
                user = User.objects.filter(username=username).first()

                # Chặn cứng: không bao giờ động vào tài khoản quản trị.
                if user and (user.is_staff or user.is_superuser):
                    results.append({**skip, 'status': 'BỎ QUA: tài khoản quản trị'})
                    continue

                if mode == 'create':
                    # DANH TÍNH LÀ EMAIL. Ba đường đi:
                    #   1. trùng email  -> CẬP NHẬT tài khoản đó (tên đăng nhập, họ
                    #      tên, tổ chức, nhóm quyền). Gửi thư nếu đổi tên đăng nhập.
                    #   2. khác email nhưng trùng tên đăng nhập -> GHI ĐÈ email mới
                    #      và huỷ mật khẩu cũ, gửi thư. Đây là cách trả tên đăng nhập
                    #      về đúng người: ai đó đăng ký chiếm sẵn tên của người khác
                    #      thì mất quyền vào, không thì họ vẫn đăng nhập bằng mật
                    #      khẩu cũ dù email đã sang chủ mới.
                    #   3. không trùng gì -> tạo mới, gửi thư.
                    by_email = (User.objects.filter(email__iexact=email).first()
                                if email else None)
                    target, mode_row = None, ''
                    if by_email is not None:
                        if by_email.is_staff or by_email.is_superuser:
                            results.append({**skip, 'status': 'BỎ QUA: email thuộc tài khoản quản trị'})
                            continue
                        if user is not None and user.pk != by_email.pk:
                            results.append({**skip, 'status':
                                            f'LỖI: tên "{username}" đã thuộc tài khoản khác'})
                            continue
                        target, mode_row = by_email, 'cập nhật'
                    elif user is not None:
                        target, mode_row = user, 'ghi đè email'

                    if target is not None and not may_update:
                        results.append({**skip, 'status':
                                        f'BỎ QUA: đã có tài khoản ({mode_row}) — '
                                        'bạn không có quyền sửa tài khoản đã tồn tại'})
                        continue

                    if target is not None:
                        renamed = target.username != username
                        old_name = target.username
                        target.username = username
                        fields = ['username']
                        if mode_row == 'ghi đè email':
                            target.email = email
                            # Mật khẩu cũ phải chết theo, nếu không chủ cũ vẫn vào được.
                            target.set_unusable_password()
                            fields += ['email', 'password']
                        if name:
                            target.first_name = name[:150]
                            fields.append('first_name')
                        target.save(update_fields=sorted(set(fields)))
                        profile = target.profile
                        if name and display_name:
                            profile.username_display_override = name[:100]
                            profile.save(update_fields=['username_display_override'])
                        status = mode_row
                        if renamed:
                            status += f': {old_name} -> {username}'
                        for org in _orgs_for(school, extra_org, username, org_cache):
                            if not profile.organizations.filter(pk=org.pk).exists():
                                profile.organizations.add(org)
                                status += f' +{org.slug}'
                        if groups:
                            target.groups.add(*groups)
                            status += ' +quyền:' + ','.join(g.name for g in groups)
                        # Gửi thư khi TẠO MỚI hoặc ĐỔI TÊN ĐĂNG NHẬP hoặc GHI ĐÈ EMAIL.
                        if send_activation and email and (renamed or mode_row == 'ghi đè email'):
                            to_notify.append(target)
                        if room:
                            from hcmus.models import TeamRoom
                            TeamRoom.objects.update_or_create(profile=profile,
                                                              defaults={'room': room})
                        results.append({'username': username, 'password': '', 'name': name,
                                        'school': school, 'email': email, 'room': room,
                                        'status': status})
                        continue

                    user = User.objects.create_user(username=username, password=password)
                    if make_staff:
                        user.is_staff = True
                    profile = Profile(user=user, language=lang)
                    profile.save()
                    if email:
                        user.email = email
                    if name:
                        user.first_name = name[:150]
                        if display_name:
                            profile.username_display_override = name[:100]
                            profile.save()
                    user.save()
                    status = 'tạo mới' + (' +nhân viên' if make_staff else '')
                    for org in _orgs_for(school, extra_org, username, org_cache):
                        profile.organizations.add(org)
                        status += f' +{org.slug}'
                    if groups:
                        user.groups.add(*groups)
                        status += ' +quyền:' + ','.join(g.name for g in groups)
                    if send_activation and email:
                        to_notify.append(user)
                else:  # reset: chỉ đổi mật khẩu, không đụng gì khác
                    if not user:
                        results.append({**skip, 'status': 'KHÔNG TỒN TẠI - bỏ qua'})
                        continue
                    user.set_password(password)
                    user.save(update_fields=['password'])
                    status = 'đổi mật khẩu'

                # Lưu phòng thi để tính năng in bài in lên header phiếu. Chỉ lưu khi
                # có nhập room; không nhập thì giữ nguyên (không xoá room cũ).
                if room:
                    from hcmus.models import TeamRoom
                    TeamRoom.objects.update_or_create(profile=user.profile,
                                                      defaults={'room': room})
                results.append({'username': username, 'password': password, 'name': name,
                                'school': school, 'email': email, 'room': room, 'status': status})
        except Exception as e:  # noqa: BLE001  một dòng hỏng không được làm sập cả mẻ
            results.append({**skip, 'status': f'LỖI: {e}'})

    # Gửi thư SAU khi đã ghi xong DB: SMTP chậm và có thể lỗi, không nên giữ
    # transaction mở trong lúc chờ mạng. Thư hỏng thì ghi vào status của dòng đó,
    # tài khoản vẫn còn nguyên để gửi lại sau.
    if send_activation and to_notify:
        by_name = {r['username']: r for r in results}
        for u in to_notify:
            try:
                send_activation_mail(u, base_url or 'https://coding.fit.hcmus.edu.vn',
                                     subject=mail_subject, body=mail_body)
                if u.username in by_name:
                    by_name[u.username]['status'] += ' +đã gửi mail'
            except Exception as e:  # noqa: BLE001
                if u.username in by_name:
                    by_name[u.username]['status'] += f' | LỖI GỬI MAIL: {e}'

    return results


def add_users_to_contests(usernames, contests):
    """Cấp quyền vào contest: thêm các user (theo username, chỉ user ĐÃ tồn tại) vào
    private_contestants của từng contest. Idempotent (đã có thì bỏ qua).

    KHÔNG tạo ContestParticipation, KHÔNG đụng scoreboard/submission, KHÔNG tự bật
    is_private (giữ nguyên chế độ contest). Trả về list (contest, số_vừa_thêm, ghi_chú).
    """
    from judge.models import Profile
    profiles = list(Profile.objects.filter(user__username__in=set(usernames)))
    out = []
    for contest in contests:
        have = set(contest.private_contestants.values_list('id', flat=True))
        to_add = [p for p in profiles if p.id not in have]
        if to_add:
            contest.private_contestants.add(*to_add)
        note = '' if contest.is_private else 'contest KHÔNG riêng-tư-cá-nhân nên cấp quyền này vô tác dụng'
        out.append((contest, len(to_add), note))
    return out


def results_csv(results):
    """Kết quả -> chuỗi CSV (utf-8-sig để Excel mở đúng tiếng Việt)."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(['username', 'password', 'name', 'school', 'email', 'room', 'status'])
    for r in results:
        w.writerow([r.get('username', ''), r.get('password', ''), r.get('name', ''),
                    r.get('school', ''), r.get('email', ''), r.get('room', ''),
                    r.get('status', '')])
    return '﻿' + buf.getvalue()
