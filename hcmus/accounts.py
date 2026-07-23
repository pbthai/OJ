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


def gen_pass():
    return ''.join(secrets.choice(PASS_ALPHABET) for _ in range(PASS_LEN))


def _norm_header(cell):
    return HEADER_ALIASES.get(cell.strip().lower().replace(' ', '').replace('_', ''))


def parse_rows(text):
    """Chuỗi CSV (dán tay hoặc từ file) -> list dict {username,password,name,school,email}.

    Tự nhận header (có cột 'username' thì map theo tên cột, không thì hiểu theo thứ
    tự username,password,name,school,email). Dòng thiếu username thì bỏ.
    """
    reader = csv.reader(io.StringIO(text))
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


def run_batch(text, mode, org_slug='', display_name=False, email_domain=''):
    """Chạy một mẻ tạo/đổi mật khẩu. Trả về list dict kết quả, mỗi dòng có thêm
    'status' (và 'password' = mật khẩu mới, rỗng nếu dòng bị bỏ qua)."""
    from judge.models import Language, Organization, Profile
    if mode not in ('create', 'reset'):
        raise ValueError('mode phải là create hoặc reset')

    rows = parse_rows(text)
    extra_org = Organization.objects.filter(slug=org_slug).first() if org_slug else None
    lang = Language.get_default_language()
    org_cache = {}
    results = []

    for row in rows:
        username = row['username']
        name, school, email, room = row['name'], row['school'], row['email'], row['room']
        if not email and email_domain:
            email = f'{username}@{email_domain}'
        password = row['password'] or gen_pass()
        skip = dict(row, password='', status='')
        try:
            with transaction.atomic():
                user = User.objects.filter(username=username).first()

                # Chặn cứng: không bao giờ động vào tài khoản quản trị.
                if user and (user.is_staff or user.is_superuser):
                    results.append({**skip, 'status': 'BỎ QUA: tài khoản quản trị'})
                    continue

                if mode == 'create':
                    if user:
                        results.append({**skip, 'status': 'ĐÃ TỒN TẠI - bỏ qua'})
                        continue
                    user = User.objects.create_user(username=username, password=password)
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
                    status = 'tạo mới'
                    if school:
                        profile.organizations.add(_school_org(school, org_cache))
                        status += ' +trường'
                    if extra_org is not None:
                        profile.organizations.add(extra_org)
                        status += f' +{extra_org.slug}'
                else:  # reset: chỉ đổi mật khẩu, không đụng gì khác
                    if not user:
                        results.append({**skip, 'status': 'KHÔNG TỒN TẠI - bỏ qua'})
                        continue
                    user.set_password(password)
                    user.save(update_fields=['password'])
                    status = 'đổi mật khẩu'

                results.append({'username': username, 'password': password, 'name': name,
                                'school': school, 'email': email, 'room': room, 'status': status})
        except Exception as e:  # noqa: BLE001  một dòng hỏng không được làm sập cả mẻ
            results.append({**skip, 'status': f'LỖI: {e}'})

    return results


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
