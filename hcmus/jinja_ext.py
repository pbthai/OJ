"""Hàm dùng trong template Jinja của FIT-HCMUS.

Đăng ký vào registry sẵn có của vnoj (judge/jinja2/registry.py) ngay lúc app khởi
động (apps.ready) nên KHÔNG phải sửa file lõi. env.globals.update(registry.globals)
chạy khi tạo môi trường Jinja (lần render đầu), lúc đó hàm này đã có trong globals.
"""
from judge.jinja2 import registry


@registry.function('hcmus_contest_printer')
def contest_printer(contest):
    """Máy in đã chọn cho `contest` (Printer) hoặc None nếu kỳ thi đó không bật in.
    Dùng để ẩn/hiện nút In bài theo từng kỳ thi."""
    if contest is None:
        return None
    from hcmus.models import ContestPrinter
    return ContestPrinter.printer_for(contest.id)
