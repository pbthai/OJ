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
from django.db.models import Q
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

# Chốt số dòng cho một lần chạy. Không còn là ngưỡng timeout: việc ghi DB đã đẩy
# sang celery, còn phần chạy trong request chỉ là dựng gói ZIP (đo trên server:
# 800 dòng hết 2,7 giây). Con số này giờ chỉ để chặn mẻ vô lý, ví dụ dán nhầm cả
# một file log vào ô. 1000 dòng đủ cho kỳ ICPC ~700 đội trong một lần chạy.
MAX_ROWS = 1000


def trung_trong_me(rows, email_domain=''):
    """Các dòng trùng tên đăng nhập hoặc trùng email NGAY TRONG danh sách nhập.

    Phải chặn trước khi chạy: hai dòng cùng email sẽ lần lượt đổi tên cùng một tài
    khoản, in ra hai phiếu mà chỉ phiếu cuối dùng được.
    """
    thay_ten, thay_mail, loi = {}, {}, []
    for i, r in enumerate(rows, start=1):
        ten = r['username'].strip().lower()
        if ten in thay_ten:
            loi.append(f'dòng {i} trùng tên đăng nhập "{r["username"]}" với dòng {thay_ten[ten]}')
        else:
            thay_ten[ten] = i
        mail = resolve_email(r['email'], r['username'], email_domain).strip().lower()
        if mail:
            if mail in thay_mail:
                loi.append(f'dòng {i} trùng email "{mail}" với dòng {thay_mail[mail]}')
            else:
                thay_mail[mail] = i
    return loi


def kiem_truoc(text, email_domain='', send_activation=False, do_create=False):
    """Các phép kiểm phải chạy TRƯỚC khi đẩy mẻ sang chạy nền.

    Chạy nền thì lỗi không quay về được màn hình người dùng nữa, nên mọi thứ có
    thể chặn cả mẻ đều phải chặn ngay ở đây. Ném ValueError kèm lý do.
    """
    rows = parse_rows(text)
    if not rows:
        raise ValueError('Không đọc được dòng hợp lệ nào (cần ít nhất cột username).')
    if len(rows) > MAX_ROWS:
        raise ValueError(
            f'Danh sách có {len(rows)} dòng, quá {MAX_ROWS} dòng cho một lần chạy. '
            'Hãy chia nhỏ danh sách.')
    trung = trung_trong_me(rows, email_domain)
    if trung:
        raise ValueError(
            'Danh sách có dòng trùng nhau, sửa rồi chạy lại (chạy tiếp sẽ đổi tên một '
            'tài khoản nhiều lần và in ra phiếu đã chết): ' + '; '.join(trung[:5])
            + ('...' if len(trung) > 5 else ''))
    if send_activation and do_create:
        thieu = rows_without_email(rows, email_domain)
        if thieu:
            raise ValueError(
                'Đã tích "gửi email kích hoạt" nhưng {} dòng không có email và cũng '
                'không phải mã sinh viên (8 chữ số): {}. Giảng viên không đoán được '
                'email từ tên đăng nhập nên phải nhập tay. Chưa tạo tài khoản nào.'
                .format(len(thieu), ', '.join(thieu[:10]) + ('...' if len(thieu) > 10 else '')))
    return rows


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


def _school_org(name, cache, create=True):
    """Organization theo tên trường (so khớp không phân biệt hoa thường).

    create=False thì tên chưa có trả về None thay vì đẻ ra tổ chức mới."""
    from judge.models import Organization
    key = name.lower()
    if key in cache:
        return cache[key]
    org = Organization.objects.filter(name__iexact=name).first()
    if org is None and not create:
        cache[key] = None
        return None
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


# --- Email khoa -> giảng viên --------------------------------------------
# Hộp thư @fit.hcmus.edu.vn do trường cấp và chỉ cấp cho người của Khoa, nên nắm
# được hộp thư đó là bằng chứng đủ để nhận quyền giảng viên. Không cần ai duyệt tay.
#
# ĐIỀU KIỆN: chỉ cấp khi đã chứng minh là CHỦ hộp thư — người dùng tự đăng ký rồi
# bấm link trong thư, hoặc tài khoản tạo hàng loạt có gửi thư kích hoạt (mật khẩu
# để trống, đường vào duy nhất là link trong hộp thư đó). Gõ email của người khác
# vào ô rồi tự đặt mật khẩu thì KHÔNG được cấp, nếu không thì bất kỳ nhân viên nào
# cũng tự nâng mình lên giảng viên bằng cách gõ email của một thầy trong khoa.
FACULTY_EMAIL_DOMAIN = 'fit.hcmus.edu.vn'
FACULTY_GROUP = 'giangvien'


def is_faculty_email(email):
    """Email của Khoa? So khớp cả '@' để 'a@gia-fit.hcmus.edu.vn' không lọt."""
    return (email or '').strip().lower().endswith('@' + FACULTY_EMAIL_DOMAIN)


def promote_faculty(user):
    """Email Khoa -> nhân viên + nhóm giangvien. Trả về True nếu có thay đổi.

    Gọi được nhiều lần, lần sau không làm gì thêm. Không bao giờ đụng tới
    is_superuser: quyền đó chỉ cấp tay.
    """
    from django.contrib.auth.models import Group
    if not is_faculty_email(user.email):
        return False
    changed = False
    if not user.is_staff:
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        changed = True
    group = Group.objects.filter(name=FACULTY_GROUP).first()
    if group is not None and not user.groups.filter(pk=group.pk).exists():
        user.groups.add(group)
        changed = True
    return changed


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
                         site_name='FIT-HCMUS Online Judge', connection=None):
    """Gửi thư kích hoạt. subject/body để trống thì dùng bản nháp mặc định.

    connection: truyền vào một kết nối SMTP đang mở để dùng lại. Không truyền thì
    Django tự mở rồi đóng một kết nối riêng cho thư này — đo trên server, bắt tay
    với Gmail mất 2,3 giây, nên mẻ vài trăm thư mà mỗi thư một kết nối là mất cả
    nửa tiếng chỉ để chào hỏi.
    """
    from django.conf import settings
    from django.core.mail import send_mail
    link = activation_link(user, base_url)
    send_mail(subject=render_mail(subject or DEFAULT_MAIL_SUBJECT, user, link, site_name),
              message=render_mail(body or DEFAULT_MAIL_BODY, user, link, site_name),
              from_email=settings.DEFAULT_FROM_EMAIL,
              recipient_list=[user.email], fail_silently=False,
              connection=connection)
    return True


def _orgs_for(school, extra_orgs, username, cache, may_create_org=False):
    """Toàn bộ tổ chức cần gắn cho một tài khoản, theo thứ tự:
      1. cột `school` trong CSV — cho phép NHIỀU, ngăn bằng ';' hoặc '|';
      2. các tổ chức chọn trên form (ô "thêm vào tổ chức"), cũng cho phép nhiều;
      3. hcmus nếu tên đăng nhập là mã sinh viên.
    Trả về danh sách đã khử trùng, giữ nguyên thứ tự.

    may_create_org: được TẠO tổ chức mới từ cột school hay không. Tắt thì tên lạ
    bị bỏ qua. Gõ một cái tên chưa có mà sinh ra tổ chức mới trên site là quyền
    quá rộng cho một trang cấp tài khoản.
    """
    from judge.models import Organization
    out = []
    for name in split_orgs(school):
        org = _school_org(name, cache, create=may_create_org)
        if org is not None:
            out.append(org)
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


def run_batch(text, mode='batch', org_slug='', display_name=False, email_domain='',
              send_activation=False, base_url='', groups=(),
              mail_subject='', mail_body='', may_update=True, make_staff=False,
              do_create=None, do_update=None, do_reset=None,
              allowed_orgs=None, may_create_org=False, passwords=None):
    """Chạy một mẻ thao tác tài khoản. Trả về list dict kết quả, mỗi dòng có thêm
    'status' (và 'password' = mật khẩu mới, rỗng nếu dòng không đổi mật khẩu).

    BA VIỆC ĐỘC LẬP, tích cái nào làm cái đó, không tích thì dòng tương ứng bị bỏ
    qua kèm lý do:
      do_create: dòng CHƯA có tài khoản -> tạo mới.
      do_update: dòng ĐÃ có tài khoản -> cập nhật tên đăng nhập / họ tên / tổ chức
        / nhóm quyền theo danh sách (danh tính là email, xem bên dưới).
      do_reset : dòng ĐÃ có tài khoản -> đặt mật khẩu mới.
    Tách ba việc ra vì trước đây chúng gộp trong một 'chế độ', người chạy không
    thấy được mẻ này sẽ đụng vào cái gì cho tới khi mọi thứ đã xảy ra rồi.

    mode để tương thích ngược: 'create' = chỉ tạo + cập nhật, 'reset' = chỉ đổi
    mật khẩu. Ba cờ do_* nếu truyền vào thì được ưu tiên.

    send_activation: gửi thư để người dùng tự đặt mật khẩu (xem send_activation_mail).
      Bật thì mọi dòng PHẢI suy ra được email, nếu không cả mẻ bị chặn từ đầu —
      tạo được một nửa rồi mới báo lỗi thì dọn rất mệt.
    groups: các Group gán cho tài khoản mới tạo VÀ tài khoản được cập nhật (chỉ
      THÊM, không gỡ nhóm sẵn có).
    may_update: người chạy có quyền SỬA tài khoản đã tồn tại không. Đây là chốt
      cuối, không tin form: tắt thì mọi dòng đụng tài khoản đã có đều bị bỏ qua.
    make_staff: đặt cờ 'tình trạng nhân viên' cho tài khoản MỚI TẠO.
    passwords: {username: mật khẩu} sinh sẵn từ trước. Dùng khi gói phiếu in đã
      được dựng và trao cho người chạy TRƯỚC, còn việc ghi DB chạy nền sau — mật
      khẩu trên phiếu và mật khẩu ghi vào DB buộc phải là một.
      Lưu ý: vẫn phân biệt với row['password'] (mật khẩu do người chạy tự nhập),
      vì luật cấp quyền giảng viên dựa vào việc cột đó CÓ BỎ TRỐNG hay không.
    """
    from judge.models import Language, Organization, Profile
    if do_create is None and do_update is None and do_reset is None:
        if mode not in ('create', 'reset'):
            raise ValueError('mode phải là create hoặc reset')
        do_create = do_update = (mode == 'create')
        do_reset = (mode == 'reset')
    do_create, do_update, do_reset = bool(do_create), bool(do_update), bool(do_reset)
    if not (do_create or do_update or do_reset):
        raise ValueError('Chưa chọn việc nào để làm.')
    # Không có quyền sửa tài khoản đã có thì hai việc kia tắt hẳn ở đây luôn, khỏi
    # phải nhớ kiểm lại ở từng nhánh bên dưới.
    if not may_update:
        do_update = do_reset = False

    rows = parse_rows(text)
    if len(rows) > MAX_ROWS:
        raise ValueError(
            f'Danh sách có {len(rows)} dòng, quá {MAX_ROWS} dòng cho một lần chạy. '
            'Hãy chia nhỏ danh sách, hoặc kiểm lại xem có dán nhầm file không.')
    trung = trung_trong_me(rows, email_domain)
    if trung:
        raise ValueError(
            'Danh sách có dòng trùng nhau, sửa rồi chạy lại (chạy tiếp sẽ đổi tên một '
            'tài khoản nhiều lần và in ra phiếu đã chết): ' + '; '.join(trung[:5])
            + ('...' if len(trung) > 5 else ''))
    if send_activation and do_create:
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
    # Không cấp được tổ chức mình không có quyền — cùng luật với _grantable_groups.
    if allowed_orgs is not None:
        cho_phep = {o.pk for o in allowed_orgs}
        extra_org = [o for o in extra_org if o.pk in cho_phep]
    lang = Language.get_default_language()
    org_cache = {}
    results = []
    to_notify = []          # tài khoản mới tạo / vừa đổi tên -> gửi thư kích hoạt

    for row in rows:
        username = row['username']
        name, school, room = row['name'], row['school'], row['room']
        email = resolve_email(row['email'], username, email_domain)
        password = row['password'] or (passwords or {}).get(username) or gen_pass()
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

                # DANH TÍNH LÀ EMAIL. Ba đường đi:
                #   1. trùng email  -> tài khoản đó là đích (đổi tên đăng nhập về
                #      đúng tên trong danh sách nếu khác).
                #   2. khác email nhưng trùng tên đăng nhập -> GHI ĐÈ email mới và
                #      huỷ mật khẩu cũ. Đây là cách trả tên đăng nhập về đúng
                #      người: ai đó đăng ký chiếm sẵn tên của người khác thì mất
                #      quyền vào, không thì họ vẫn đăng nhập bằng mật khẩu cũ dù
                #      email đã sang chủ mới.
                #   3. không trùng gì -> chưa có tài khoản.
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
                    # CHỈ coi là ghi đè khi thật sự có email MỚI và KHÁC email cũ.
                    # Trước đây thiếu vế này: danh sách kiểu 'username,name' không có
                    # cột email làm email='' rơi vào nhánh ghi đè, xoá trắng email
                    # rồi huỷ mật khẩu — nạn nhân mất luôn cả đường "quên mật khẩu"
                    # vì Django cần email để gửi link.
                    if email and email.lower() != (user.email or '').lower():
                        target, mode_row = user, 'ghi đè email'
                    else:
                        target, mode_row = user, 'cập nhật'

                if target is None:
                    if not do_create:
                        results.append({**skip, 'status':
                                        'CHƯA CÓ tài khoản — bỏ qua (không tích "tạo '
                                        'tài khoản chưa có")'})
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
                    for org in _orgs_for(school, extra_org, username, org_cache, may_create_org):
                        profile.organizations.add(org)
                        status += f' +{org.slug}'
                    if groups:
                        user.groups.add(*groups)
                        status += ' +quyền:' + ','.join(g.name for g in groups)
                    # Email Khoa -> giảng viên, nhưng chỉ khi mật khẩu bỏ trống và
                    # có gửi thư: lúc đó đường vào duy nhất là link trong hộp thư
                    # đó. Xem promote_faculty.
                    if is_faculty_email(email):
                        if send_activation and not row['password']:
                            user.set_unusable_password()
                            user.save(update_fields=['password'])
                            # Mật khẩu sinh tự động không còn dùng được nữa, đừng
                            # in nó lên phiếu để khỏi ai ngồi gõ một chuỗi đã chết.
                            password = ''
                            if promote_faculty(user):
                                status += ' +giảng viên'
                        else:
                            status += ' (email Khoa nhưng CHƯA cấp quyền giảng viên: '
                            status += ('phải để trống cột password' if send_activation
                                       else 'phải tích gửi thư kích hoạt')
                            status += ')'
                    if send_activation and email:
                        to_notify.append(user)
                else:
                    if not (do_update or do_reset):
                        ly_do = ('bạn không có quyền sửa tài khoản đã tồn tại'
                                 if not may_update else
                                 'không tích việc nào cho tài khoản đã có')
                        results.append({**skip, 'status':
                                        f'ĐÃ CÓ tài khoản ({mode_row}) — bỏ qua ({ly_do})'})
                        continue
                    # Dòng khớp theo EMAIL nhưng tên đăng nhập trong danh sách khác
                    # tên thật của tài khoản. Nếu không tích "cập nhật" thì không ai
                    # đổi tên cả, mà vẫn đổi mật khẩu thì phiếu in ra mang một tên
                    # đăng nhập không tồn tại — người cầm phiếu không vào được, còn
                    # chủ thật thì mất mật khẩu mà không biết.
                    if do_reset and not do_update and target.username != username:
                        results.append({**skip, 'status':
                                        f'BỎ QUA: email này thuộc tài khoản '
                                        f'"{target.username}", không phải "{username}". '
                                        'Tích thêm "cập nhật" nếu muốn đổi tên đăng nhập.'})
                        continue
                    user = target
                    profile = target.profile
                    phan = []
                    if do_update:
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
                        if name and display_name:
                            profile.username_display_override = name[:100]
                            profile.save(update_fields=['username_display_override'])
                        buoc = mode_row + (f': {old_name} -> {username}' if renamed else '')
                        for org in _orgs_for(school, extra_org, username, org_cache, may_create_org):
                            if not profile.organizations.filter(pk=org.pk).exists():
                                profile.organizations.add(org)
                                buoc += f' +{org.slug}'
                        if groups:
                            target.groups.add(*groups)
                            buoc += ' +quyền:' + ','.join(g.name for g in groups)
                        phan.append(buoc)
                        # Gửi thư khi ĐỔI TÊN ĐĂNG NHẬP hoặc GHI ĐÈ EMAIL: hai việc
                        # này làm người dùng mất đường vào cũ.
                        if send_activation and email and (renamed or mode_row == 'ghi đè email'):
                            to_notify.append(target)
                    if do_reset:
                        target.set_password(password)
                        target.save(update_fields=['password'])
                        phan.append('đổi mật khẩu')
                        if do_update and mode_row == 'ghi đè email':
                            phan.append('(mật khẩu mới ghi đè lên việc khoá tài khoản cũ)')
                    else:
                        # Không đổi mật khẩu thì không có gì để in lên phiếu.
                        password = ''
                        # Ghi đè email có huỷ mật khẩu cũ. Nếu không gửi thư và cũng
                        # không đặt mật khẩu mới thì tài khoản đó thành không ai vào
                        # được — phải nói ra, đừng để người chạy tự phát hiện sau.
                        if mode_row == 'ghi đè email' and not send_activation:
                            phan.append('CẢNH BÁO: đã khoá mật khẩu cũ mà chưa có đường '
                                        'vào mới — tích "gửi thư kích hoạt" hoặc '
                                        '"đổi mật khẩu"')
                    status = ' '.join(phan)

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
        from django.core.mail import get_connection
        by_name = {r['username']: r for r in results}
        # MỘT kết nối cho cả mẻ. Bắt tay với Gmail mất 2,3 giây, mở lại cho từng
        # thư thì 700 thư tốn gần nửa tiếng chỉ để bắt tay, lại dễ bị Gmail chặn
        # vì mở quá nhiều kết nối liên tiếp.
        conn = get_connection()
        try:
            conn.open()
        except Exception:  # noqa: BLE001  không mở nổi thì để Django tự lo từng thư
            conn = None
        try:
            for u in to_notify:
                try:
                    send_activation_mail(u, base_url or 'https://coding.fit.hcmus.edu.vn',
                                         subject=mail_subject, body=mail_body,
                                         connection=conn)
                    if u.username in by_name:
                        by_name[u.username]['status'] += ' +đã gửi mail'
                except Exception as e:  # noqa: BLE001
                    if u.username in by_name:
                        by_name[u.username]['status'] += f' | LỖI GỬI MAIL: {e}'
        finally:
            if conn is not None:
                conn.close()

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


# --- Đọc thử: xem trước mỗi dòng sẽ đụng vào tài khoản nào -----------------
# Chạy TRƯỚC khi thực thi, không ghi gì vào DB. Mục đích là để người chạy nhìn
# thấy "dòng này sẽ tạo mới, dòng kia đã có rồi" TRƯỚC khi bấm, thay vì đọc cột
# trạng thái sau khi mọi thứ đã xảy ra rồi.

TT_CHUA_CO = 'chua-co'
TT_DA_CO_TEN = 'da-co-ten'
TT_DA_CO_EMAIL = 'da-co-email'
TT_XUNG_DOT = 'xung-dot'
TT_QUAN_TRI = 'quan-tri'
TT_TEN_LA = 'ten-la'

TT_NHAN = {
    TT_CHUA_CO: 'chưa có',
    TT_DA_CO_TEN: 'đã có (khớp tên đăng nhập)',
    TT_DA_CO_EMAIL: 'đã có (khớp email)',
    TT_XUNG_DOT: 'xung đột: tên và email thuộc hai tài khoản khác nhau',
    TT_QUAN_TRI: 'tài khoản quản trị — luôn bỏ qua',
    TT_TEN_LA: 'tên đăng nhập có ký tự lạ',
}


def preview_rows(text, email_domain='', reveal=False):
    """Đọc danh sách rồi tra DB xem từng dòng ứng với tài khoản nào. KHÔNG ghi gì.

    reveal: người xem có quyền sửa tài khoản đã có hay không.
      Tắt (nhân viên thường) thì bảng CHỈ nói dòng đó đã có tài khoản hay chưa.
      Bật mới kèm họ tên / email / tổ chức / nhóm quyền của tài khoản đang có.
      Không có vế này thì trang thành công cụ tra cứu: dán danh sách tên đăng
      nhập (vốn công khai) là lấy về email thật, họ tên thật và điểm mặt được ai
      là quản trị viên. Vì vậy tài khoản quản trị cũng bị gộp vào nhãn "đã có"
      cho người không đủ quyền — nhãn riêng cũng là một cách điểm danh.

    Trả về list dict gồm dữ liệu dòng, 'tinh_trang' (một trong TT_*), 'nhan'
    (chữ tiếng Việt để hiện lên bảng) và 'hien_co' (thông tin tài khoản đang có,
    None nếu chưa có hoặc người xem không đủ quyền).
    """
    from django.contrib.auth.models import User
    rows = parse_rows(text)[:MAX_ROWS + 1]
    emails = {}
    for r in rows:
        emails[r['username']] = resolve_email(r['email'], r['username'], email_domain)

    # Hai truy vấn cho cả mẻ thay vì hai truy vấn mỗi dòng.
    ten_list = [r['username'] for r in rows]
    mail_list = [e for e in emails.values() if e]
    found = (User.objects
             .filter(Q(username__in=ten_list) | Q(email__in=mail_list))
             .select_related('profile')
             .prefetch_related('groups', 'profile__organizations'))
    theo_ten = {u.username.lower(): u for u in found}
    theo_mail = {(u.email or '').lower(): u for u in found if u.email}

    trung_ten, trung_mail = set(), set()
    da_ten, da_mail = set(), set()

    out = []
    for r in rows:
        username = r['username']
        email = emails[username]
        item = dict(r, email=email, tinh_trang=TT_CHUA_CO, hien_co=None, trung='')

        if not USERNAME_RE.match(username):
            item['tinh_trang'] = TT_TEN_LA
            out.append(_gan_nhan(item, reveal))
            continue

        # Trùng ngay trong danh sách nhập: báo ở bảng, và run_batch cũng chặn.
        if username.lower() in da_ten:
            trung_ten.add(username.lower())
            item['trung'] = 'trùng tên đăng nhập với dòng trên'
        da_ten.add(username.lower())
        if email:
            if email.lower() in da_mail:
                trung_mail.add(email.lower())
                item['trung'] = 'trùng email với dòng trên'
            da_mail.add(email.lower())

        user = theo_ten.get(username.lower())
        by_email = theo_mail.get(email.lower()) if email else None

        if (user and (user.is_staff or user.is_superuser)) or \
           (by_email and (by_email.is_staff or by_email.is_superuser)):
            item['tinh_trang'] = TT_QUAN_TRI
            item['hien_co'] = _tom_tat_user(user or by_email) if reveal else None
        elif by_email is not None and user is not None and by_email.pk != user.pk:
            item['tinh_trang'] = TT_XUNG_DOT
            item['hien_co'] = _tom_tat_user(by_email) if reveal else None
        elif by_email is not None:
            item['tinh_trang'] = TT_DA_CO_EMAIL
            item['hien_co'] = _tom_tat_user(by_email) if reveal else None
        elif user is not None:
            item['tinh_trang'] = TT_DA_CO_TEN
            item['hien_co'] = _tom_tat_user(user) if reveal else None
        out.append(_gan_nhan(item, reveal))
    return out


def _gan_nhan(item, reveal=False):
    tt = item['tinh_trang']
    # Người không đủ quyền: gộp "quản trị" và "xung đột" vào nhãn trung tính, vì
    # bản thân nhãn riêng đã đủ để điểm mặt ai là quản trị viên.
    if not reveal and tt in (TT_QUAN_TRI, TT_XUNG_DOT):
        item['nhan'] = 'đã có — bạn không sửa được'
    else:
        item['nhan'] = TT_NHAN[tt]
    if item.get('trung'):
        item['nhan'] += f' · {item["trung"]}'
    return item


def du_doan(items, do_create=False, do_update=False, do_reset=False, passwords=None):
    """Dòng nào SẼ có mật khẩu mới, theo đúng luật của run_batch.

    Dùng để dựng gói phiếu in NGAY, trước khi việc ghi DB kịp chạy. Phải bám sát
    run_batch, lệch là in ra phiếu mang mật khẩu không bao giờ được ghi vào DB.
    """
    passwords = passwords or {}
    out = []
    for it in items:
        tt = it['tinh_trang']
        pw, tt_txt = '', ''
        if tt in (TT_TEN_LA, TT_QUAN_TRI, TT_XUNG_DOT) or it.get('trung'):
            tt_txt = 'sẽ bỏ qua: ' + it['nhan']
        elif tt == TT_CHUA_CO:
            if do_create:
                pw, tt_txt = passwords.get(it['username'], ''), 'sẽ tạo mới'
            else:
                tt_txt = 'sẽ bỏ qua: chưa có tài khoản mà không tích tạo mới'
        else:   # đã có tài khoản
            if do_reset:
                # run_batch bỏ qua dòng khớp email mà tên đăng nhập khác, khi
                # không tích cập nhật — không được in phiếu cho dòng đó.
                lech = (tt == TT_DA_CO_EMAIL and it.get('hien_co')
                        and it['hien_co']['username'] != it['username'])
                if lech and not do_update:
                    tt_txt = 'sẽ bỏ qua: email thuộc tài khoản mang tên khác'
                else:
                    pw, tt_txt = passwords.get(it['username'], ''), 'sẽ đổi mật khẩu'
            elif do_update:
                tt_txt = 'sẽ cập nhật (không đổi mật khẩu)'
            else:
                tt_txt = 'sẽ bỏ qua: đã có tài khoản'
        out.append({'username': it['username'], 'password': pw, 'name': it['name'],
                    'school': it['school'], 'email': it['email'], 'room': it['room'],
                    'status': tt_txt})
    return out


def build_zip(results, contest_note='', want_slips=False, slip=None, ghi_chu=''):
    """Gói ZIP trả về cho người chạy: CSV + các PDF nếu có tích in."""
    import zipfile
    from hcmus import badges, slips as slips_mod

    slip = slip or {}
    csv_text = results_csv(results)
    if ghi_chu:
        csv_text += ghi_chu
    pdf_bytes = badge_bytes = tent_bytes = None
    if want_slips:
        su_kien = (slip.get('contest') or slip.get('title') or 'FIT-HCMUS Online Judge')
        try:
            pdf_bytes = slips_mod.make_slips_pdf(
                results, title=slip.get('title') or 'FIT-HCMUS Online Judge',
                contest=slip.get('contest', ''), url=slip.get('url', ''))
            badge_bytes = badges.make_badges_pdf(results, event=su_kien,
                                                 copies=slip.get('copies', 3))
            tent_bytes = badges.make_tents_pdf(results, event=su_kien)
        except Exception as e:  # noqa: BLE001  thiếu font/thư viện thì vẫn trả CSV
            csv_text += f'\n# Không tạo được PDF: {e}\n'
        if pdf_bytes is None:
            csv_text += ('\n# KHÔNG có phieu-dang-nhap.pdf: không dòng nào có mật khẩu mới.\n'
                         '# Phiếu chỉ in được tài khoản vừa tạo hoặc vừa đổi mật khẩu.\n'
                         '# Xem cột trạng thái ở trên để biết vì sao từng dòng bị bỏ qua.\n')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('tai-khoan.csv', csv_text.encode('utf-8'))
        if pdf_bytes:
            z.writestr('phieu-dang-nhap.pdf', pdf_bytes)
        # Thẻ đeo + bảng tên dùng chung nguồn dữ liệu với phiếu nên gói luôn, ban
        # tổ chức chỉ phải tải một lần. Hai thứ này chỉ cần tên đội.
        if badge_bytes:
            z.writestr('the-deo-ten.pdf', badge_bytes)
        if tent_bytes:
            z.writestr('bang-ten-ban.pdf', tent_bytes)
        if contest_note:
            z.writestr('cap-quyen-contest.txt', contest_note.encode('utf-8'))
    return buf.getvalue()


def _tom_tat_user(user):
    """Tóm tắt tài khoản đang có, để hiện ở cột cuối bảng bước 2.

    CHỈ gọi khi người xem có quyền sửa tài khoản (xem preview_rows), vì nó lộ họ
    tên, email, tổ chức, nhóm quyền và cờ nhân viên của người khác.
    """
    if user is None:
        return None
    try:
        orgs = ', '.join(user.profile.organizations.values_list('slug', flat=True))
    except Exception:  # noqa: BLE001  tài khoản không có profile thì thôi
        orgs = ''
    return {
        'username': user.username,
        'name': user.first_name,
        'email': user.email,
        'orgs': orgs,
        'is_staff': user.is_staff,
        'groups': ', '.join(user.groups.values_list('name', flat=True)),
    }


def thong_ke(items):
    """Đếm theo tình trạng, để hiện dòng tóm tắt phía trên bảng."""
    dem = {}
    for it in items:
        dem[it['tinh_trang']] = dem.get(it['tinh_trang'], 0) + 1
    return {
        'tong': len(items),
        'chua_co': dem.get(TT_CHUA_CO, 0),
        'da_co': dem.get(TT_DA_CO_TEN, 0) + dem.get(TT_DA_CO_EMAIL, 0),
        'co_email': sum(1 for it in items if it['email']),
        'van_de': (dem.get(TT_XUNG_DOT, 0) + dem.get(TT_QUAN_TRI, 0)
                   + dem.get(TT_TEN_LA, 0)),
    }
