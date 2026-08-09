"""Sinh PDF thẻ đeo tên và bảng tên để bàn cho kỳ thi (ICPC).

Hai thứ này in cùng lúc với phiếu đăng nhập (xem hcmus/slips.py) vì cùng một
nguồn dữ liệu: mỗi dòng là một đội với username / name / school / room.

Bố cục bám theo cách ICPC làm ở các regional:

  Thẻ đeo  — vừa bìa đeo cỡ 4×3 inch nằm ngang (101,6 × 76,2 mm), là cỡ bìa đeo
             phổ biến nhất ở các kỳ ICPC. Ta dùng 100 × 75 mm cho chẵn số: A4 dọc
             xếp vừa 2 cột × 3 hàng, thừa lề đều 5 mm.
             Trên thẻ: dải màu tên kỳ thi, TÊN ĐỘI to nhất, trường, phòng thi, và
             một dòng kẻ trống để thí sinh tự ghi tên mình — hệ thống chỉ quản lý
             tài khoản theo đội nên không có sẵn tên từng thành viên.
             Mặc định in 3 thẻ mỗi đội (đội ICPC 3 người), đổi được.

  Bảng tên — A4 NẰM NGANG, mỗi đội một tờ, gấp đôi theo chiều ngang thành hình
             lều dựng trên bàn. Nửa trên in ngược 180° để khi gấp thì cả hai phía
             đều đọc được: một phía cho thí sinh, một phía cho người chụp ảnh và
             giám thị đi ngoài. Tên đội cỡ rất lớn để lên hình còn đọc được.

Font dùng chung với slips.py (DejaVu, đủ tiếng Việt).
"""
from hcmus.slips import _fonts

# Thẻ đeo: 100 × 75 mm, A4 dọc 2 cột × 3 hàng.
BADGE_W, BADGE_H = 100.0, 75.0
BADGE_COLS, BADGE_ROWS = 2, 3
BADGE_MX = (210.0 - BADGE_COLS * BADGE_W) / 2
BADGE_MY = (297.0 - BADGE_ROWS * BADGE_H) / 2

NAVY = (12, 72, 143)            # xanh nhận diện trường, dùng cả ở phiếu đăng nhập
GREY = (110, 110, 110)


def _new_pdf(orientation):
    from fpdf import FPDF
    f = _fonts()
    pdf = FPDF(orientation=orientation, unit='mm', format='A4')
    pdf.set_auto_page_break(False)
    pdf.add_font('Sans', '', f['sans'])
    pdf.add_font('Sans', 'B', f['sans_bold'])
    pdf.add_font('Mono', '', f['mono'])
    pdf.add_font('Mono', 'B', f['mono_bold'])
    return pdf


def _fit_font(pdf, text, max_w, start, min_size, style='B'):
    """Giảm cỡ chữ tới khi vừa bề ngang. Tên đội dài ngắn rất khác nhau
    (HCMUS-3Y0P với HCMUS-NguoiTinhMuaDong), cỡ cố định thì hoặc tràn hoặc phí chỗ."""
    size = start
    while size > min_size:
        pdf.set_font('Sans', style, size)
        if pdf.get_string_width(text) <= max_w:
            break
        size -= 0.5
    pdf.set_font('Sans', style, size)
    return size


def _draw_badge(pdf, x, y, row, event, role):
    pdf.set_draw_color(190)
    pdf.set_dash_pattern(dash=1.5, gap=1.5)
    pdf.rect(x, y, BADGE_W, BADGE_H)
    pdf.set_dash_pattern()

    # Dải tên kỳ thi trên cùng
    pdf.set_fill_color(*NAVY)
    pdf.rect(x, y, BADGE_W, 12, style='F')
    pdf.set_xy(x + 5, y + 3)
    pdf.set_font('Sans', 'B', 10)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(BADGE_W - 10, 6, (event or 'FIT-HCMUS Online Judge')[:60])

    # Tên đội — chữ to nhất trên thẻ
    name = (row.get('name') or row.get('username') or '').strip()
    pdf.set_text_color(20, 20, 20)
    _fit_font(pdf, name, BADGE_W - 12, 22, 11)
    pdf.set_xy(x + 6, y + 20)
    pdf.cell(BADGE_W - 12, 12, name, align='C')

    # Trường
    school = (row.get('school') or '').strip()
    if school:
        pdf.set_font('Sans', '', 11)
        pdf.set_text_color(*GREY)
        pdf.set_xy(x + 6, y + 33)
        pdf.cell(BADGE_W - 12, 6, school, align='C')

    # Dòng trống để thí sinh tự ghi tên (hệ thống chỉ có dữ liệu theo đội)
    line_y = y + 50
    pdf.set_draw_color(160)
    pdf.line(x + 12, line_y, x + BADGE_W - 12, line_y)
    pdf.set_xy(x + 12, line_y + 0.5)
    pdf.set_font('Sans', '', 7.5)
    pdf.set_text_color(150)
    pdf.cell(BADGE_W - 24, 4, 'Họ và tên', align='C')

    # Chân thẻ: vai trò bên trái, phòng thi bên phải
    pdf.set_xy(x + 6, y + BADGE_H - 12)
    pdf.set_font('Sans', 'B', 9)
    pdf.set_text_color(*NAVY)
    pdf.cell((BADGE_W - 12) / 2, 5, role)
    room = (row.get('room') or '').strip()
    if room:
        pdf.set_font('Sans', '', 9)
        pdf.set_text_color(*GREY)
        pdf.cell((BADGE_W - 12) / 2, 5, room, align='R')


def make_badges_pdf(rows, event='', role='THÍ SINH · CONTESTANT', copies=3):
    """Thẻ đeo tên, mặc định 3 thẻ mỗi đội. Trả về bytes PDF, None nếu không có dòng nào."""
    rows = [r for r in rows if (r.get('name') or r.get('username'))]
    if not rows:
        return None
    copies = max(1, min(int(copies or 1), 10))
    # Xếp theo phòng rồi tên đội: phát thẻ theo phòng cho khớp lúc đón thí sinh.
    rows = sorted(rows, key=lambda r: ((r.get('room') or '~').strip(),
                                       (r.get('name') or r.get('username') or '').strip()))
    # Các thẻ của cùng một đội nằm liền nhau để cắt xong là gom được ngay.
    expanded = [r for r in rows for _ in range(copies)]

    pdf = _new_pdf('P')
    pdf.set_title(f'{event} — thẻ đeo tên' if event else 'Thẻ đeo tên')
    per_page = BADGE_COLS * BADGE_ROWS
    for i, row in enumerate(expanded):
        if i % per_page == 0:
            pdf.add_page()
        pos = i % per_page
        px = BADGE_MX + (pos % BADGE_COLS) * BADGE_W
        py = BADGE_MY + (pos // BADGE_COLS) * BADGE_H
        _draw_badge(pdf, px, py, row, event, role)
    return bytes(pdf.output())


def _draw_tent_face(pdf, y_top, height, row, event, flip):
    """Vẽ một mặt của bảng tên. flip=True thì xoay 180° quanh tâm mặt đó."""
    page_w = 297.0
    cx, cy = page_w / 2, y_top + height / 2

    ctx = pdf.rotation(180, cx, cy) if flip else pdf.local_context()
    with ctx:
        name = (row.get('name') or row.get('username') or '').strip()
        school = (row.get('school') or '').strip()
        room = (row.get('room') or '').strip()

        if event:
            pdf.set_font('Sans', '', 12)
            pdf.set_text_color(*GREY)
            pdf.set_xy(15, y_top + 10)
            pdf.cell(page_w - 30, 7, event, align='C')

        pdf.set_text_color(*NAVY)
        _fit_font(pdf, name, page_w - 40, 60, 18)
        pdf.set_xy(20, y_top + height / 2 - 22)
        pdf.cell(page_w - 40, 26, name, align='C')

        if school:
            pdf.set_font('Sans', '', 18)
            pdf.set_text_color(60, 60, 60)
            pdf.set_xy(20, y_top + height / 2 + 8)
            pdf.cell(page_w - 40, 10, school, align='C')

        if room:
            pdf.set_font('Sans', '', 11)
            pdf.set_text_color(*GREY)
            pdf.set_xy(20, y_top + height - 18)
            pdf.cell(page_w - 40, 6, room, align='C')


def make_tents_pdf(rows, event=''):
    """Bảng tên để bàn: A4 ngang, mỗi đội một tờ, gấp đôi thành hình lều.
    Nửa trên in ngược để gấp xong cả hai phía đều đọc được."""
    rows = [r for r in rows if (r.get('name') or r.get('username'))]
    if not rows:
        return None
    rows = sorted(rows, key=lambda r: ((r.get('room') or '~').strip(),
                                       (r.get('name') or r.get('username') or '').strip()))
    pdf = _new_pdf('L')
    pdf.set_title(f'{event} — bảng tên để bàn' if event else 'Bảng tên để bàn')
    page_w, page_h = 297.0, 210.0
    half = page_h / 2

    for row in rows:
        pdf.add_page()
        _draw_tent_face(pdf, 0, half, row, event, flip=True)       # mặt sau (gấp xuống)
        _draw_tent_face(pdf, half, half, row, event, flip=False)   # mặt trước
        # Đường gấp giữa tờ
        pdf.set_draw_color(200)
        pdf.set_dash_pattern(dash=2, gap=2)
        pdf.line(10, half, page_w - 10, half)
        pdf.set_dash_pattern()
        pdf.set_xy(page_w - 45, half - 4)
        pdf.set_font('Sans', '', 7)
        pdf.set_text_color(180)
        pdf.cell(35, 3, 'gấp theo đường này', align='R')
    return bytes(pdf.output())
