"""Sinh PDF phiếu đăng nhập trên server (dùng cho trang web quản trị).

Port từ myScript/make_slips.py. Khác bản chạy-máy-cá-nhân: dùng font DejaVu có
sẵn trên Linux (đủ tiếng Việt) thay Arial/Courier của macOS, và TRẢ VỀ bytes thay
vì ghi file. Bố cục giữ nguyên: A4, 2 cột x 4 phiếu/trang, viền đứt để cắt.

Đường dẫn font đổi được qua settings.HCMUS_SLIP_FONTS nếu server để font chỗ khác.
"""
from django.conf import settings

PAGE_W, PAGE_H = 210, 297           # A4 (mm)
MARGIN = 8
COLS, ROWS = 2, 4
CELL_W = (PAGE_W - 2 * MARGIN) / COLS
CELL_H = (PAGE_H - 2 * MARGIN) / ROWS

_DEFAULT_FONTS = {
    'sans': '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    'sans_bold': '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
    'mono': '/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf',
    'mono_bold': '/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf',
}


def _fonts():
    f = dict(_DEFAULT_FONTS)
    f.update(getattr(settings, 'HCMUS_SLIP_FONTS', {}) or {})
    return f


def _draw_slip(pdf, x, y, row, title, contest, url, index, total):
    pad = 6
    cx = x + pad
    with pdf.local_context():                           # viền đứt để cắt
        pdf.set_draw_color(150)
        pdf.set_dash_pattern(dash=1.5, gap=1.5)
        pdf.rect(x, y, CELL_W, CELL_H)

    pdf.set_xy(cx, y + 5)
    pdf.set_font('Sans', 'B', 11)
    pdf.set_text_color(12, 72, 143)                     # navy nhận diện trường
    pdf.cell(CELL_W - 2 * pad, 5, title)
    line_y = y + 10
    if contest:
        pdf.set_xy(cx, line_y)
        pdf.set_font('Sans', '', 9.5)
        pdf.set_text_color(60)
        pdf.cell(CELL_W - 2 * pad, 4.5, contest)
        line_y += 5.5
    pdf.set_draw_color(12, 72, 143)
    pdf.line(cx, line_y + 1, x + CELL_W - pad, line_y + 1)

    ty = line_y + 4.5
    if row.get('name'):
        pdf.set_xy(cx, ty)
        pdf.set_font('Sans', 'B', 12)
        pdf.set_text_color(0)
        pdf.cell(CELL_W - 2 * pad, 6, row['name'])
        ty += 6.5
    if row.get('school'):
        pdf.set_xy(cx, ty)
        pdf.set_font('Sans', '', 9.5)
        pdf.set_text_color(90)
        pdf.cell(CELL_W - 2 * pad, 4.5, row['school'])
        ty += 5.5

    label_w = 24
    ty += 2
    pdf.set_xy(cx, ty)
    pdf.set_font('Sans', '', 9.5)
    pdf.set_text_color(90)
    pdf.cell(label_w, 7, 'Username')
    pdf.set_font('Mono', 'B', 15)
    pdf.set_text_color(0)
    pdf.cell(CELL_W - 2 * pad - label_w, 7, row['username'])
    ty += 8.5
    pdf.set_xy(cx, ty)
    pdf.set_font('Sans', '', 9.5)
    pdf.set_text_color(90)
    pdf.cell(label_w, 7, 'Password')
    pdf.set_font('Mono', 'B', 15)
    pdf.set_text_color(0)
    pdf.set_char_spacing(1.2)
    pdf.cell(CELL_W - 2 * pad - label_w, 7, row['password'])
    pdf.set_char_spacing(0)

    pdf.set_xy(cx, y + CELL_H - 9)
    pdf.set_font('Mono', '', 10.5)
    pdf.set_text_color(12, 72, 143)
    pdf.cell(CELL_W - 2 * pad - 14, 5, url)
    pdf.set_font('Sans', '', 8)
    pdf.set_text_color(150)
    pdf.cell(14, 5, f'{index}/{total}', align='R')


def make_slips_pdf(rows, title='FIT-HCMUS Online Judge', contest='',
                   url='https://coding.fit.hcmus.edu.vn'):
    """rows: list dict có username + password (+ name/school tuỳ chọn). Trả về bytes
    PDF, hoặc None nếu không có dòng nào kèm mật khẩu (vd cả mẻ đều bị bỏ qua)."""
    from fpdf import FPDF
    rows = [r for r in rows if r.get('username') and r.get('password')]
    if not rows:
        return None
    fonts = _fonts()
    pdf = FPDF(orientation='P', unit='mm', format='A4')
    pdf.set_auto_page_break(False)
    pdf.add_font('Sans', '', fonts['sans'])
    pdf.add_font('Sans', 'B', fonts['sans_bold'])
    pdf.add_font('Mono', '', fonts['mono'])
    pdf.add_font('Mono', 'B', fonts['mono_bold'])
    pdf.set_title(f'{title} — phiếu đăng nhập')

    per_page = COLS * ROWS
    for i, row in enumerate(rows):
        if i % per_page == 0:
            pdf.add_page()
        pos = i % per_page
        px = MARGIN + (pos % COLS) * CELL_W
        py = MARGIN + (pos // COLS) * CELL_H
        _draw_slip(pdf, px, py, row, title, contest, url, i + 1, len(rows))
    return bytes(pdf.output())
