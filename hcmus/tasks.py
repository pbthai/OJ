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
