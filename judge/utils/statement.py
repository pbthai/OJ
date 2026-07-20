"""Che đề bài khi contest bật hide_problem_statements (thi trên giấy).

Không đụng dữ liệu: Problem.description luôn giữ đề thật, chỉ tầng hiển thị đổi.
Xem Contest.statements_hidden_for và Problem.statement_hidden_for.
"""
from django.utils.translation import gettext as _


def hidden_statement_markdown():
    """Nội dung thay thế hiện cho thí sinh. Trả markdown, cùng dạng với description
    thật nên mọi chỗ render đang có không cần biết gì thêm."""
    return '## %s\n\n%s\n' % (
        _('The statement is handed out on paper'),
        _('This page is only for submitting code. Submit under the problem letter '
          '(A, B, C, ...) shown on the printed statement.'),
    )


def apply_statement_hiding(context, problem, user):
    """Đặt context['description'] thành bản che nếu phải che.

    Luôn đặt context['hide_statement'] để template biết mà KHÔNG vào block
    {% cache %} — khoá cache 'problem_html' chỉ gồm (problem.id, MATH_ENGINE,
    LANGUAGE_CODE), không có user/contest, nên nếu vẫn cache thì một lần render
    sẽ đầu độc cả hai chiều: staff xem trước thì thí sinh đọc trúng đề thật, thí
    sinh xem trước thì giảng viên nhìn thấy placeholder suốt 24 tiếng.

    Trả về True nếu đang che.
    """
    hidden = problem.statement_hidden_for(user)
    if hidden:
        context['description'] = hidden_statement_markdown()
    context['hide_statement'] = hidden
    return hidden
