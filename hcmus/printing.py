"""In mã nguồn bài nộp cho thí sinh trong giờ thi (luật ICPC cho phép in).

Dựng PDF bằng Pygments (tách token để tô màu) + fpdf2 (vẽ, font DejaVuSansMono có
đủ tiếng Việt), header MỖI TRANG là tên đội + phòng thi, có số dòng và số trang.
Đếm số trang để chặn >10 trang (từ chối ngay). Gửi máy in qua CUPS `lp`.

Chỉ phụ thuộc thứ đã có trên server: Pygments, fpdf2, font DejaVu, CUPS.
"""
import os
import subprocess
import tempfile

from django.utils import timezone

FONT_DIR = '/usr/share/fonts/truetype/dejavu'
PAGE_LIMIT_DEFAULT = 10
_SIZE = 8.5          # cỡ chữ mã nguồn (pt)
_LINE_H = 3.9        # cao mỗi dòng (mm)
_GUTTER = 11.0       # lề trái cho số dòng (mm)


def _style_for(ttype):
    """Token Pygments -> ((r,g,b), bold). Đi ngược cây token tới kiểu khớp gần nhất.
    Màu vẫn đọc được khi in đen trắng vì keyword/kiểu để in đậm."""
    from pygments.token import Comment, Error, Keyword, Name, Number, Operator, String
    table = {
        Keyword: ((0, 0, 200), True),
        Keyword.Type: ((0, 0, 200), True),
        Name.Function: ((90, 20, 130), True),
        Name.Class: ((90, 20, 130), True),
        Name.Builtin: ((0, 90, 140), False),
        Comment: ((110, 110, 110), False),
        String: ((160, 30, 20), False),
        Number: ((0, 100, 120), False),
        Operator: ((60, 60, 60), False),
        Error: ((200, 0, 0), True),
    }
    t = ttype
    while t is not None:
        if t in table:
            return table[t]
        t = t.parent
    return ((0, 0, 0), False)


def _tokens_to_lines(tokens):
    """(ttype, value) của Pygments -> list các dòng, mỗi dòng là list (ttype, text)."""
    lines = [[]]
    for ttype, value in tokens:
        parts = value.replace('\r', '').split('\n')
        for j, part in enumerate(parts):
            if j > 0:
                lines.append([])
            if part:
                lines[-1].append((ttype, part.replace('\t', '    ')))
    if lines and not lines[-1]:
        lines.pop()
    return lines or [[]]


def _make_pdf(header_left, header_right):
    from fpdf import FPDF

    class CodePDF(FPDF):
        def header(self):
            self.set_y(8)
            self.set_font('sans', 'B', 10)
            self.set_text_color(0)
            self.cell(0, 5, header_left, align='L')
            self.set_y(8)
            self.set_font('sans', '', 8.5)
            self.set_text_color(90)
            self.cell(0, 5, header_right, align='R')
            self.set_draw_color(160)
            self.set_line_width(0.2)
            self.line(self.l_margin, 14.6, self.w - self.r_margin, 14.6)
            self.set_y(17.5)

        def footer(self):
            self.set_y(-11)
            self.set_font('sans', '', 8)
            self.set_text_color(120)
            self.cell(0, 5, 'Trang %s/{nb}' % self.page_no(), align='C')

    pdf = CodePDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(False)            # tự quản lý ngắt trang để căn số dòng
    pdf.add_font('mono', '', f'{FONT_DIR}/DejaVuSansMono.ttf')
    pdf.add_font('mono', 'B', f'{FONT_DIR}/DejaVuSansMono-Bold.ttf')
    pdf.add_font('sans', '', f'{FONT_DIR}/DejaVuSans.ttf')
    pdf.add_font('sans', 'B', f'{FONT_DIR}/DejaVuSans-Bold.ttf')
    pdf.alias_nb_pages()
    return pdf


def render_source_pdf(code, language_name, pygments_name, team, room, problem, when=None):
    """Trả về (pdf_bytes, số_trang). team + room in ở header mỗi trang."""
    from pygments import lex
    from pygments.lexers import get_lexer_by_name, guess_lexer
    from pygments.util import ClassNotFound

    when = when or timezone.localtime()
    header_left = team + (f'    Phòng: {room}' if room else '')
    header_right = ' · '.join(x for x in (problem, language_name, when.strftime('%d/%m/%Y %H:%M')) if x)

    pdf = _make_pdf(header_left, header_right)
    pdf.add_page()
    pdf.set_font('mono', '', _SIZE)
    char_w = pdf.get_string_width('0') or 1.7
    x0 = pdf.l_margin + _GUTTER
    avail = pdf.w - pdf.r_margin - x0
    max_chars = max(24, int(avail / char_w))
    bottom = pdf.h - 12

    try:
        lexer = get_lexer_by_name(pygments_name or 'text')
    except ClassNotFound:
        try:
            lexer = guess_lexer(code)
        except ClassNotFound:
            lexer = get_lexer_by_name('text')

    for lineno, toks in enumerate(_tokens_to_lines(lex(code, lexer)), start=1):
        _draw_line(pdf, lineno, toks, x0, char_w, max_chars, bottom)

    return bytes(pdf.output()), pdf.page_no()


def _draw_line(pdf, lineno, toks, x0, char_w, max_chars, bottom):
    runs = [[t, *_style_for(ty)] for ty, t in toks if t] or [['', (0, 0, 0), False]]
    # Ngắt dòng dài theo số ký tự (monospace nên đếm ký tự là đủ).
    visual, cur, cur_len = [], [], 0
    for text, color, bold in runs:
        i = 0
        while i < len(text):
            room_left = max_chars - cur_len
            if room_left <= 0:
                visual.append(cur)
                cur, cur_len, room_left = [], 0, max_chars
            seg = text[i:i + room_left]
            cur.append((seg, color, bold))
            cur_len += len(seg)
            i += len(seg)
    visual.append(cur)

    for v, vline in enumerate(visual):
        if pdf.get_y() + _LINE_H > bottom:
            pdf.add_page()
        y = pdf.get_y()
        pdf.set_xy(pdf.l_margin, y)
        pdf.set_font('mono', '', _SIZE)
        pdf.set_text_color(175, 175, 175)
        pdf.cell(_GUTTER - 1.5, _LINE_H, str(lineno) if v == 0 else '', align='R')
        x = x0
        for seg, color, bold in vline:
            if seg:
                pdf.set_xy(x, y)
                pdf.set_font('mono', 'B' if bold else '', _SIZE)
                pdf.set_text_color(*color)
                pdf.cell(char_w * len(seg), _LINE_H, seg)
                x += char_w * len(seg)
        pdf.set_y(y + _LINE_H)


def render_submission_pdf(submission, team, room):
    src = submission.source.source
    lang = submission.language
    return render_source_pdf(src, lang.name, lang.pygments, team, room,
                             submission.problem.name)


def send_to_printer(pdf_bytes, cups_dest, job_name='in-bai'):
    """Gửi PDF tới máy in qua CUPS `lp -d <cups_dest>`. cups_dest là TÊN HÀNG ĐỢI
    CUPS trên server. Trả về (ok, thông_báo)."""
    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    try:
        tmp.write(pdf_bytes)
        tmp.close()
        proc = subprocess.run(['lp', '-d', cups_dest, '-t', job_name[:60], tmp.name],
                              capture_output=True, timeout=30)
        out = (proc.stdout or b'').decode('utf-8', 'replace').strip()
        err = (proc.stderr or b'').decode('utf-8', 'replace').strip()
        if proc.returncode == 0:
            return True, out or 'đã gửi'
        return False, (err or out or 'lp trả lỗi')[:290]
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:290]
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
