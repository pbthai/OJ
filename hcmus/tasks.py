"""Celery task sinh PDF đề bài.

Vì sao không làm đồng bộ: bản cài đặt DMOJ điển hình chạy uwsgi sau nginx với ít
worker đồng bộ và timeout đọc ngắn. Một lượt build cả bộ đề phải chạy pdflatex
nhiều lượt, dễ chạm trần timeout, và mỗi request chiếm trọn một worker. Celery đã
có sẵn trong DMOJ nên cắm vào là xong, lại tái dùng được trang task_status có sẵn.
"""
import os

from celery import shared_task
from django.conf import settings
from django.utils.translation import gettext as _

from hcmus import statement_pdf
from judge.utils.celery import Progress


def cache_dir():
    return getattr(settings, 'HCMUS_PDF_CACHE',
                   os.path.join(os.path.expanduser('~'), 'statement-pdf'))


@shared_task(bind=True)
def build_contest_statement(self, contest_key, sty_name, sty_path=None, lang=None):
    """Trả về tên file PDF trong cache_dir(). Ném exception nếu hỏng — trang
    task_status của DMOJ sẽ hiện thông báo lỗi."""
    sty_upload = None
    if sty_path:
        with open(sty_path, 'rb') as f:
            sty_upload = (os.path.basename(sty_path), f.read())

    with Progress(self, 2, stage=_('Đang sinh LaTeX')) as p:
        out_dir = cache_dir()
        p.did(1)
        path, info = statement_pdf.generate(
            contest_key, sty_name=sty_name, sty_upload=sty_upload,
            lang=lang, out_dir=out_dir)
        p.did(1)

    if sty_path and os.path.exists(sty_path):
        os.remove(sty_path)
    return os.path.basename(path)


@shared_task(bind=True)
def run_accounts_batch(self, text, opts):
    """Chạy một mẻ cấp tài khoản ở chạy nền.

    Vì sao phải chạy nền: băm mật khẩu của Django cố tình chậm (~0,4 giây mỗi tài
    khoản). 150 dòng đã chạm trần 60 giây của nginx, mà uwsgi thì KHÔNG dừng theo
    — request bị cắt trong khi tài khoản vẫn tiếp tục được tạo, và gói mật khẩu
    trả về thì mất trắng. Người chạy có 700 tài khoản mới mà không biết mật khẩu
    của chúng.

    Cách làm: mật khẩu được sinh TRƯỚC ở tầng view và trao ngay cho người chạy
    dưới dạng file, còn đây chỉ ghi vào DB đúng bộ mật khẩu đó. Nhờ vậy phiếu in
    và DB luôn khớp, kể cả khi mẻ chạy dở.

    Trả về list kết quả từng dòng để trang kết quả hiện lên.
    """
    from hcmus import accounts as acc
    from judge.models import Contest

    rows = acc.parse_rows(text)
    with Progress(self, max(len(rows), 1), stage=_('Đang ghi tài khoản')) as p:
        results = acc.run_batch(
            text,
            org_slug=opts.get('org', ''),
            display_name=opts.get('display_name', False),
            email_domain=opts.get('email_domain', ''),
            send_activation=opts.get('send_activation', False),
            base_url=opts.get('base_url', ''),
            groups=_groups(opts.get('groups') or []),
            mail_subject=opts.get('mail_subject', ''),
            mail_body=opts.get('mail_body', ''),
            may_update=opts.get('may_update', False),
            make_staff=opts.get('make_staff', False),
            do_create=opts.get('do_create', False),
            do_update=opts.get('do_update', False),
            do_reset=opts.get('do_reset', False),
            allowed_orgs=_orgs(opts.get('allowed_orgs') or []),
            may_create_org=opts.get('may_create_org', False),
            passwords=opts.get('passwords') or {},
        )
        p.did(len(rows))

    keys = opts.get('contests') or []
    if keys:
        contests = list(Contest.objects.filter(key__in=keys))
        usernames = [r['username'] for r in results if r.get('username')]
        acc.add_users_to_contests(usernames, contests)
    return results


def _groups(names):
    from django.contrib.auth.models import Group
    return list(Group.objects.filter(name__in=names))


def _orgs(pks):
    from judge.models import Organization
    return list(Organization.objects.filter(pk__in=pks))
