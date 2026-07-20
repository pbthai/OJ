"""Sinh PDF đề bài trọn bộ của một contest (FIT-HCMUS).

Lấy tên contest, ngày, danh sách bài + giới hạn từ DB; chuyển Problem.description
(markdown) sang LaTeX; ráp vào khuôn olymp.sty (mặc định icpcHCMUS.sty) rồi biên
dịch bằng pdflatex.

Vài điều đã trả giá để biết, đừng sửa nếu chưa đọc kỹ:

1. PHẢI dùng pdflatex, KHÔNG được xelatex. vietnam.sty (vntex) chạy encoding T5 với
   font Type1 8-bit vnr. Dưới XeTeX nó rơi về Latin Modern T5 và diễn giải sai byte
   UTF-8: "Cho một dãy số" thành "Cho mt dõy s". XeTeX vẫn exit 0 và ra đủ số trang
   nên lỗi này hoàn toàn im lặng.

2. Toán trong markdown của DMOJ dùng dấu ~...~ chứ không phải $...$ (do lua filter
   ở judge/utils/codeforces_polygon.py:51). Đưa thẳng vào pandoc sẽ ra
   \\textasciitilde. Phải tiền xử lý, xem preprocess_markdown().

3. \\exmp KHÔNG phải verbatim (icpcHCMUS.sty:511) — chỉ đặt \\ttfamily\\obeylines.
   Dữ liệu mẫu chứa _ # % & $ ^ { } \\ sẽ làm vỡ biên dịch. Vì vậy sample test được
   ghi ra file .in/.out rồi nhúng bằng \\exmpfile (dùng \\verbatiminput, byte-faithful).

4. \\contest phải đứng TRƯỚC mọi \\begin{problem}: nó \\let \\addcontentslineICPCstyle,
   thiếu là mọi bài lỗi Undefined control sequence.

5. Tham số thứ 6 (feedback) của môi trường problem bị hỏng trong icpcHCMUS.sty
   (phát ra & và \\\\ ngoài tabular đã bị comment) — luôn phát đúng 5 tham số.

6. \\Scoring in ra chữ "Constraints" chứ không phải "Scoring" (icpcHCMUS.sty:311
   ánh xạ nhầm sang \\kw@Constraints). Với đề ICPC thì lại hợp lý nên giữ nguyên.

7. Logo HCMUSlogo.png và ICPClogo.png BẮT BUỘC có trong thư mục build — fancyhdr
   chèn vào header mọi trang.

Bảo mật: biên dịch LaTeX là THỰC THI MÃ. Nếu user chạy site có quyền sudo (nhiều
bản cài đặt để vậy cho tiện triển khai) thì shell-escape của TeX là đường leo thang
đặc quyền trực tiếp. Xem compile_pdf() — ranh giới bảo mật nằm ở cờ engine cộng
giới hạn tài nguyên của hệ điều hành, KHÔNG nằm ở khâu lọc chuỗi: math mode là
đường vòng mà escape không bịt được.
"""
import os
import re
import resource
import shutil
import subprocess
import tempfile

BASE = os.path.dirname(os.path.abspath(__file__))
TEX_ASSETS = os.path.join(BASE, 'tex')

# .sty đi kèm sẵn. Tên -> mô tả ngắn hiện trên form.
BUNDLED_STY = {
    'icpcHCMUS.sty': 'ICPC style của HCMUS (mặc định) — có logo trường, hiện Memory limit',
    'icpc.sty': 'ICPC style gốc — logo ICPC hai bên, ẩn Memory limit',
    'olpHCMUS.sty': 'Olympic Tin học — tiêu đề tiếng Việt, có mục Hướng dẫn giải',
}
DEFAULT_STY = 'icpcHCMUS.sty'

LOGOS = ('HCMUSlogo.png', 'ICPClogo.png')

# Giới hạn tài nguyên cho MỘT lượt pdflatex. Bộ đề thật 12 bài build hết 3,6s
# (đo trên server), nên 30s đã là dư gấp chục lần cho bộ đề lớn hơn nhiều.
CPU_SECONDS = 30           # ulimit -t: chặn bom vòng lặp \def\x{\x}\x
FILE_SIZE_BYTES = 200 << 20   # ulimit -f: chặn bom đĩa (toàn hệ thống chung 1 phân vùng)
WALL_TIMEOUT = 60          # trần treo I/O của một lượt
STY_MAX_BYTES = 512 << 10

PLACEHOLDER_MARK = 'Đề bài được phát trên giấy'


class StatementError(Exception):
    pass


# --------------------------------------------------------------------------
# Thu thập dữ liệu
# --------------------------------------------------------------------------

def _label(contest, index):
    try:
        return contest.get_label_for_problem(index)
    except Exception:
        return chr(65 + index) if index < 26 else str(index + 1)


def collect(contest, lang=None):
    """Đọc contest -> dict thuần Python, không để ORM rò sang phần sinh tex."""
    from django.conf import settings
    from django.utils import timezone

    # Không còn đọc ~/exam-statements-<key>.json nữa: từ khi ẩn đề chuyển sang cờ
    # Contest.hide_problem_statements, description trong DB LUÔN là đề thật.
    # Vẫn nhận diện placeholder của cách cũ để cảnh báo nếu có bài kẹt lại.
    still_placeholder = []

    items = []
    cps = contest.contest_problems.select_related('problem').order_by('order')
    for i, cp in enumerate(cps):
        p = cp.problem
        tr = p.translations.filter(language=lang).first() if lang else None
        markdown = (tr.description if tr else p.description) or ''

        if PLACEHOLDER_MARK in markdown:
            still_placeholder.append(p.code)

        items.append({
            'label': _label(contest, i),
            'code': p.code,
            'name': (tr.name if tr else p.name) or p.code.upper(),
            'markdown': markdown,
            'time_limit': p.time_limit,
            'memory_limit': p.memory_limit,   # KB
        })

    tz = timezone.get_default_timezone()
    user_tz = getattr(settings, 'DEFAULT_USER_TIME_ZONE', None)
    if user_tz:
        try:
            import pytz
            tz = pytz.timezone(user_tz)
        except Exception:
            pass
    start = timezone.localtime(contest.start_time, tz)

    return {
        'key': contest.key,
        'name': contest.name,
        'date': start,
        'problems': items,
        'still_placeholder': still_placeholder,
    }


# --------------------------------------------------------------------------
# markdown -> LaTeX
# --------------------------------------------------------------------------

TEX_SPECIAL = {
    '\\': r'\textbackslash{}', '{': r'\{', '}': r'\}', '$': r'\$', '&': r'\&',
    '#': r'\#', '^': r'\textasciicircum{}', '_': r'\_', '~': r'\textasciitilde{}',
    '%': r'\%',
}


def tex_escape(text):
    """Escape cho phần văn bản thuần (tên contest, tên bài). Tên contest hay có
    dấu # ("Contest #01") — không escape là hỏng biên dịch."""
    return ''.join(TEX_SPECIAL.get(c, c) for c in str(text))


# Placeholder tạm để ký tự $ và ~ THẬT không bị nhầm là toán.
_DOLLAR = '\x00HCMUSDOLLAR\x00'
_TILDE = '\x00HCMUSTILDE\x00'
_DISPLAY = '\x00HCMUSDISP%d\x00'

_SPAN_CODE = re.compile(
    r'<span style="font-family: courier new,monospace;">(.*?)</span>', re.DOTALL)
_SPAN_DOLLAR = re.compile(r'<span>\$</span>')
_SPAN_TILDE = re.compile(r'<span>~</span>')


def preprocess_markdown(md):
    """Đưa markdown kiểu DMOJ về markdown mà pandoc hiểu đúng.

    Cần thiết vì importer Polygon sinh ra 3 thứ pandoc không xử lý được:
      - toán inline bọc trong ~...~ (không phải $...$)
      - \\texttt{} biến thành raw HTML <span style="font-family: courier new...">
        mà latex writer của pandoc VỨT IM LẶNG, mất luôn nội dung định dạng
      - ký tự $ và ~ thật bọc trong <span>$</span> / <span>~</span>
    """
    # 0. Chuẩn hoá xuống dòng. Package Polygon có bài dùng CRLF (bài capquang của
    #    2026training01 là một), làm regex fence ``` trượt và nhét cả dấu fence lẫn
    #    ký tự CR vào file mẫu. Lỗi này KHÔNG làm hỏng biên dịch nên rất dễ lọt.
    md = md.replace('\r\n', '\n').replace('\r', '\n')

    # 1. Ký tự thật -> placeholder (làm trước để khỏi bị bước 4 hiểu nhầm)
    md = _SPAN_DOLLAR.sub(_DOLLAR, md)
    md = _SPAN_TILDE.sub(_TILDE, md)

    # 2. span monospace -> inline code của markdown
    def _code(m):
        inner = m.group(1).replace('\n', ' ')
        fence = '`'
        while fence in inner:
            fence += '`'
        return f'{fence}{inner}{fence}'
    md = _SPAN_CODE.sub(_code, md)

    # 3. Giữ display math nguyên vẹn khỏi bước 4
    displays = []

    def _keep(m):
        displays.append(m.group(0))
        return _DISPLAY % (len(displays) - 1)
    md = re.sub(r'\$\$.*?\$\$', _keep, md, flags=re.DOTALL)

    # 4. Toán inline ~...~ -> $...$. Giới hạn trong 1 dòng: pandoc không bao giờ
    #    ngắt dòng giữa RawInline math nên cặp ~ luôn nằm cùng dòng.
    md = re.sub(r'~([^~\n]+?)~', r'$\1$', md)

    # 5. Trả lại display math
    for i, d in enumerate(displays):
        md = md.replace(_DISPLAY % i, d)

    # 6. HTML entity mà filter sinh ra
    md = (md.replace('&mdash;', '---').replace('&ndash;', '--')
            .replace('&nbsp;', ' ').replace('&amp;', '&')
            .replace('&lt;', '<').replace('&gt;', '>'))

    # 8. Bỏ thẻ HTML còn sót, GIỮ chữ bên trong. Phải làm ở đây vì bước sau tắt
    #    raw_html của pandoc; không dọn thì thẻ lạ sẽ in ra nguyên văn <span>.
    #    Regex đòi có chữ cái ngay sau '<' nên không nuốt nhầm bất đẳng thức "a < b".
    md = re.sub(r'</?[a-zA-Z][^>]*>', '', md)

    # 9. Ký tự thật trở về
    return md.replace(_DOLLAR, r'\$').replace(_TILDE, r'\textasciitilde{}')


_LONGTABLE = re.compile(
    r'(?:\{\\def\\LTcaptype\{none\}[^\n]*\n)?'      # bọc ngoài pandoc hay thêm
    r'\\begin\{longtable\}(?:\[[^\]]*\])?\{(?P<spec>.*?)\}\n'
    r'(?P<body>.*?)'
    r'\\end\{longtable\}\n?'
    r'(?:\}\n?)?',
    re.DOTALL)


def _longtable_to_tabular(tex):
    """Hạ longtable xuống tabular.

    Bắt buộc phải làm: longtable CHẾT bên trong environment problem với
    "! LaTeX Error: No counter 'none' defined." dù chạy tốt ở ngoài.

    Không thể chỉ xoá \\endhead/\\endlastfoot: trong longtable phần chân bảng
    (\\bottomrule) được ĐỊNH NGHĨA TRƯỚC phần thân, nên xoá suông sẽ đẩy đường kẻ
    đáy lên nằm trên các dòng dữ liệu. Phải tách ra rồi ráp lại đúng thứ tự.
    """
    def repl(m):
        spec = m.group('spec').replace('@{}', '').strip()
        body = m.group('body').replace('\\noalign{}', '')

        head, sep, rest = body.partition('\\endhead')
        if not sep:
            head, rest = '', body
        foot, sep2, rows = rest.partition('\\endlastfoot')
        if not sep2:
            foot, rows = '', rest

        chunks = [c.strip('\n') for c in (head, rows, foot) if c.strip()]
        return ('\\begin{tabular}{%s}\n%s\n\\end{tabular}\n'
                % (spec, '\n'.join(chunks)))

    return _LONGTABLE.sub(repl, tex)


def _postprocess_tex(tex):
    # pandoc chống ligature bằng -\/-\/- , trả về em dash cho đúng
    tex = tex.replace('-\\/-\\/-', '---').replace('-\\/-', '--')
    if 'longtable' in tex:
        tex = _longtable_to_tabular(tex)
    return tex.strip()


def md_to_tex(md):
    """Chạy pandoc.

    gfm không hỗ trợ extension raw_tex, nên lệnh TeX thô trong description bị
    escape sẵn — đúng cái ta muốn (đề do giảng viên nạp từ Polygon, không nên
    tin). tex_math_dollars thì gfm bật sẵn nên $...$ vẫn ra math.

    Tắt raw_html: bật thì latex writer của pandoc VỨT IM LẶNG mọi thẻ HTML còn
    sót, mất nội dung mà không báo gì. preprocess_markdown() đã dọn thẻ trước.

    Đây là phòng thủ theo tầng. Hàng rào thật nằm ở cờ engine trong compile_pdf():
    math mode là đường vòng không bịt được bằng escape ($\\input{/etc/passwd}$ vẫn
    lọt qua mọi bộ lọc markdown).
    """
    md = md.strip()
    if not md:
        return ''
    proc = subprocess.run(
        ['pandoc', '-f', 'gfm-raw_html', '-t', 'latex', '--wrap=preserve'],
        input=md.encode('utf-8'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise StatementError('pandoc lỗi: ' + proc.stderr.decode('utf-8', 'replace')[:400])
    return _postprocess_tex(proc.stdout.decode('utf-8'))


# --------------------------------------------------------------------------
# Tách markdown thành các mục theo heading do importer sinh ra
# --------------------------------------------------------------------------

_HEADING = re.compile(r'^##\s+(.+?)\s*$', re.MULTILINE)
# [^\n]* thay vì [a-zA-Z]*: chịu được cả CRLF sót lẫn info-string lạ sau dấu fence
_FENCE = re.compile(r'```[^\n]*\n(.*?)```', re.DOTALL)


def _strip_fence(body):
    """Lấy nội dung trong khối ```. Nếu vì lý do gì đó không khớp thì gỡ dấu fence
    bằng tay — tuyệt đối không để ``` lọt vào file mẫu (in ra PDF là thấy ngay)."""
    m = _FENCE.search(body)
    text = m.group(1) if m else body
    return '\n'.join(ln for ln in text.splitlines()
                     if not ln.strip().startswith('```'))


def split_sections(md):
    """Importer Polygon ghép description theo thứ tự cố định (codeforces_polygon.py:739):
    legend (không heading), ## Input, ## Output, ## Interaction, ## Scoring,
    ## Sample Input i + ## Sample Output i, ## Notes.
    Tách ngược lại đúng theo mốc đó."""
    marks = list(_HEADING.finditer(md))
    legend = md[:marks[0].start()] if marks else md
    out = {'legend': legend.strip(), 'samples': []}

    pending_in = None
    for i, m in enumerate(marks):
        title = m.group(1).strip()
        body = md[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(md))]
        low = title.lower()

        if low.startswith('sample input'):
            pending_in = _strip_fence(body)
        elif low.startswith('sample output'):
            out['samples'].append((pending_in or '', _strip_fence(body)))
            pending_in = None
        elif low == 'input':
            out['input'] = body.strip()
        elif low == 'output':
            out['output'] = body.strip()
        elif low == 'interaction':
            out['interaction'] = body.strip()
        elif low in ('scoring', 'constraints'):
            out['scoring'] = body.strip()
        elif low in ('notes', 'note', 'explanation'):
            out['notes'] = body.strip()
        else:
            # Heading lạ -> gộp vào legend, giữ nguyên chữ, không mất nội dung
            out['legend'] += f'\n\n**{title}**\n\n{body.strip()}'
    return out


# --------------------------------------------------------------------------
# Sinh tex một bài
# --------------------------------------------------------------------------

def _fmt_time(seconds):
    return ('%g' % float(seconds)) + 's'


def _fmt_mem(kb):
    mb = int(kb) / 1024.0
    return ('%g' % mb) + 'MB' if mb < 1024 else ('%g' % (mb / 1024.0)) + 'GB'


def problem_tex(item, workdir):
    """Sinh nội dung .tex cho một bài, đồng thời ghi file mẫu .in/.out ra workdir."""
    sec = split_sections(preprocess_markdown(item['markdown']))

    parts = [
        '\\begin{problem}{%s}{stdin}{stdout}{%s}{%s}' % (
            tex_escape(item['name']), _fmt_time(item['time_limit']),
            _fmt_mem(item['memory_limit'])),
        '\\small{',
        # Bắt buộc có, dù trông như thừa với icpcHCMUS.
        # icpcHCMUS.sty:662 vô hiệu hoá \addcontentsline rồi tự gọi bản đã lưu ở
        # :474, nên dòng này thành no-op — vô hại.
        # olpHCMUS.sty comment cả hai cơ chế đó (:426 và :588), nên nó TRÔNG CHỜ
        # file đề tự khai. Thiếu dòng này thì mục lục OVERVIEW trang đầu rỗng trơn
        # mà biên dịch vẫn báo thành công.
        '\\addcontentsline{toc}{subsection}{%s}' % tex_escape(item['name']),
        md_to_tex(sec.get('legend', '')),
    ]

    if sec.get('input'):
        parts += ['', '\\InputFile', md_to_tex(sec['input'])]
    if sec.get('output'):
        parts += ['', '\\OutputFile', md_to_tex(sec['output'])]
    if sec.get('interaction'):
        parts += ['', '\\Interaction', md_to_tex(sec['interaction'])]

    if sec['samples']:
        parts += ['', '\\Examples', '\\begin{example}%']
        for i, (sin, sout) in enumerate(sec['samples'], start=1):
            fin = f"ex-{item['code']}-{i}.in"
            fout = f"ex-{item['code']}-{i}.out"
            # verbatiminput cần newline cuối, thiếu là nuốt dòng chót
            with open(os.path.join(workdir, fin), 'w', encoding='utf-8') as f:
                f.write(sin.strip('\n') + '\n')
            with open(os.path.join(workdir, fout), 'w', encoding='utf-8') as f:
                f.write(sout.strip('\n') + '\n')
            parts.append('\\exmpfile{%s}{%s}%%' % (fin, fout))
        parts.append('\\end{example}')

    if sec.get('scoring'):
        # \Scoring in ra chữ "Constraints" (icpcHCMUS.sty:311) — đúng ý cho đề ICPC
        parts += ['', '\\Scoring', md_to_tex(sec['scoring'])]
    if sec.get('notes'):
        parts += ['', '\\Notes', md_to_tex(sec['notes'])]

    parts += ['}', '\\end{problem}', '']
    return '\n'.join(parts)


ORDINAL = {1: 'st', 2: 'nd', 3: 'rd'}


def _ordinal(day):
    if 11 <= day <= 13:
        return 'th'
    return ORDINAL.get(day % 10, 'th')


MASTER = r"""\documentclass[12pt,a4paper,oneside]{article}

\usepackage{%(sty)s}

\usepackage[utf8]{vietnam}
\usepackage{amsmath}
\usepackage{amssymb}
\usepackage{graphicx}
\usepackage{expdlist}
\usepackage{comment}
\usepackage{listings}
\usepackage{url}
\usepackage{tikz}
\usepackage[labelformat=empty]{caption}
\usepackage{float}
\usepackage{longtable}
\usepackage{booktabs}
\usepackage{array}
\providecommand{\tightlist}{\setlength{\itemsep}{0pt}\setlength{\parskip}{0pt}}

\contest{%(contest)s}{}{}

\begin{document}
\thispagestyle{empty}

\begin{center}

\includegraphics[width=0.7\textwidth]{HCMUSlogo.png}

\textbf{\Large \thecontestname}

\textbf{Date: %(month)s %(day)d$^{%(ord)s}$, %(year)d}

\vspace{1 cm}

\textbf{\large OVERVIEW}
\renewcommand*\contentsname{\empty}
\vspace{-1.5cm}
\tableofcontents

\end{center}

\pagebreak
\setcounter{page}{1}

%(inputs)s

\end{document}
"""


def build_tex(data, workdir, sty_name=DEFAULT_STY):
    """Ghi toàn bộ .tex + file mẫu vào workdir, trả về đường dẫn file master."""
    inputs = []
    for item in data['problems']:
        body = problem_tex(item, workdir)
        fname = f"prob-{item['code']}.tex"
        with open(os.path.join(workdir, fname), 'w', encoding='utf-8') as f:
            f.write(body)
        inputs.append('\\input{%s}' % fname[:-4])

    d = data['date']
    master = MASTER % {
        'sty': sty_name[:-4] if sty_name.endswith('.sty') else sty_name,
        'contest': tex_escape(data['name']),
        'month': d.strftime('%B'), 'day': d.day, 'ord': _ordinal(d.day),
        'year': d.year,
        'inputs': '\n'.join(inputs),
    }
    path = os.path.join(workdir, 'statement.tex')
    with open(path, 'w', encoding='utf-8') as f:
        f.write(master)
    return path


# --------------------------------------------------------------------------
# Biên dịch
# --------------------------------------------------------------------------

def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_BYTES, FILE_SIZE_BYTES))
    os.nice(10)


def compile_pdf(workdir, passes=3):
    """pdflatex, chạy trong hộp cát.

    Vì sao từng cờ có mặt (đã kiểm chứng bằng khai thác thật, không phải cargo cult):
      -no-shell-escape  mặc định của pdflatex là RESTRICTED shell escape, tức VẪN BẬT.
                        Không truyền cờ này thì \\write18 chạy được, tức người tải .sty
                        lên chạy được lệnh hệ thống dưới quyền của tiến trình web.
      openin_any=p      mặc định là 'a': \\input{/etc/passwd} đọc được file hệ thống và
                        nhả nội dung vào PDF. Nguy hiểm nhất là file cấu hình cục bộ
                        chứa SECRET_KEY và thông tin kết nối CSDL — tiến trình web đọc
                        được nó, nên TeX cũng đọc được.
      openout_any=p     mặc định đã là 'p' nhưng đặt tường minh để không phụ thuộc bản TeX.
      TEXMFHOME/VAR     cô lập, không cho .sty tải lên cài đè macro vào home của user
                        chạy site.
      RLIMIT_CPU        \\def\\x{\\x}\\x treo vô hạn, ulimit -t giết bằng SIGXCPU.
      RLIMIT_FSIZE      bom đĩa; toàn hệ thống chung một phân vùng với DB và judge.
      nice              contest đang chạy thì chấm bài phải được ưu tiên hơn build PDF.
    """
    env = dict(os.environ)
    env.update({
        'openin_any': 'p',
        'openout_any': 'p',
        'shell_escape': 'f',
        'TEXMFHOME': os.path.join(workdir, '.texmf'),
        'TEXMFVAR': os.path.join(workdir, '.texmf-var'),
        'TEXMFCONFIG': os.path.join(workdir, '.texmf-config'),
        'TEXINPUTS': workdir + ':',
        'HOME': workdir,
    })

    pdf = os.path.join(workdir, 'statement.pdf')
    log = ''
    for i in range(passes):
        try:
            proc = subprocess.run(
                ['pdflatex', '-no-shell-escape', '-interaction=nonstopmode',
                 '-file-line-error', 'statement.tex'],
                cwd=workdir, env=env, preexec_fn=_limits,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=WALL_TIMEOUT)
        except subprocess.TimeoutExpired:
            raise StatementError(f'pdflatex quá {WALL_TIMEOUT}s một lượt, đã huỷ')
        log = proc.stdout.decode('utf-8', 'replace')

        # Thoát sớm khi lượt này chết hẳn. Không có dòng này thì một .sty bom CPU
        # vẫn ăn trọn CPU_SECONDS Ở CẢ 3 LƯỢT (đo thật: 361s thay vì 120s) — chạy
        # thêm 2 lượt nữa cũng không cứu được gì.
        if proc.returncode != 0 and not os.path.exists(pdf):
            break

    if not os.path.exists(pdf):
        raise StatementError('pdflatex không sinh được PDF.\n\n' + _error_digest(log))
    return pdf


def _error_digest(log):
    """Lấy các dòng lỗi thật, bỏ hàng nghìn dòng nạp font."""
    lines = [ln for ln in log.splitlines()
             if ln.startswith('!') or re.match(r'^\S+\.tex:\d+:', ln)]
    return '\n'.join(lines[:25]) or log[-2000:]


# --------------------------------------------------------------------------
# Hàm dùng ngoài
# --------------------------------------------------------------------------

# Thủ thuật cũ của olymp.sty: dùng ký tự & thật làm mốc so sánh chuỗi rỗng.
# Macro chứa nó được bung BÊN TRONG ô của tabular; khi gói `array` được nạp (bộ
# sinh này luôn nạp vì pandoc cần nó cho bảng), array bung nội dung ô trong lúc
# quét dấu phân cột, nên các ký tự & đó bị hiểu thành dấu ngăn cột và biên dịch
# chết với "Misplaced alignment tab character &". Đổi mốc sang \relax là hết.
# olpHCMUS.sty dính lỗi này, icpcHCMUS.sty thì không (nó vốn đã dùng \relax).
_AMP_SENTINEL = re.compile(r'\\ifx&(#\d)&')


def patch_sty_compat(text):
    r"""Vá tương thích cho .sty. Trả về (nội dung mới, số chỗ đã vá).

    Chạy cho CẢ .sty đóng gói sẵn lẫn .sty người dùng tải lên, nên các file trong
    tex/ giữ nguyên xi bản gốc trong statement/ — sửa tay từng file sẽ sót, vì
    thủ thuật này xuất hiện 4-8 chỗ mỗi file và cả ba file đều có.

    Phép đổi bảo toàn ngữ nghĩa: \ifx so sánh đúng 2 token, nên đổi mốc từ & sang
    \relax cho kết quả y hệt ở cả nhánh rỗng lẫn nhánh không rỗng.
    """
    patched, n = _AMP_SENTINEL.subn(r'\\ifx\\relax\1\\relax', text)
    return patched, n


def validate_sty(name, content):
    """Kiểm file .sty người dùng tải lên.

    Lưu ý về mô hình đe doạ: quét chuỗi ở đây KHÔNG phải hàng rào bảo mật —
    \\csname write\\endcsname 18 vượt qua mọi denylist, đã thử và đúng là qua được.
    Hàng rào thật là -no-shell-escape + openin_any=p ở compile_pdf(). Quét ở đây
    chỉ để báo sớm cho người dùng biết file của họ chứa thứ sẽ không chạy được.
    """
    if not name.endswith('.sty'):
        raise StatementError('chỉ nhận file .sty')
    if len(content) > STY_MAX_BYTES:
        raise StatementError(f'file .sty lớn hơn {STY_MAX_BYTES >> 10}KB')
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        try:
            text = content.decode('latin-1')
        except Exception:
            raise StatementError('không đọc được nội dung .sty')
    warn = [p for p in ('write18', 'ShellEscape', 'immediate\\write18')
            if p in text]
    return warn


def generate(contest_key, sty_name=DEFAULT_STY, sty_upload=None, lang=None,
             out_dir=None):
    """Sinh PDF, trả về (đường dẫn PDF, dict thông tin).

    sty_upload: (tên_file, bytes) nếu admin tải .sty riêng lên.
    out_dir: nơi chép PDF ra; None thì trả về file trong thư mục tạm còn sống.
    """
    from judge.models import Contest

    try:
        contest = Contest.objects.get(key=contest_key)
    except Contest.DoesNotExist:
        raise StatementError(f'không có contest với key "{contest_key}"')

    data = collect(contest, lang=lang)
    if not data['problems']:
        raise StatementError('contest chưa có bài nào')

    workdir = tempfile.mkdtemp(prefix='hcmus-stmt-')
    try:
        for logo in LOGOS:
            shutil.copy(os.path.join(TEX_ASSETS, logo), workdir)

        sty_patched = 0
        if sty_upload:
            up_name, up_content = sty_upload
            validate_sty(up_name, up_content)
            sty_name = os.path.basename(up_name)
            text = up_content.decode('utf-8', 'replace')
            text, sty_patched = patch_sty_compat(text)
            with open(os.path.join(workdir, sty_name), 'w', encoding='utf-8') as f:
                f.write(text)
        else:
            if sty_name not in BUNDLED_STY:
                raise StatementError(f'không có style "{sty_name}"')
            with open(os.path.join(TEX_ASSETS, sty_name), encoding='utf-8') as f:
                text = f.read()
            text, sty_patched = patch_sty_compat(text)
            with open(os.path.join(workdir, sty_name), 'w', encoding='utf-8') as f:
                f.write(text)

        build_tex(data, workdir, sty_name=sty_name)
        pdf = compile_pdf(workdir)

        info = {
            'contest': data['name'],
            'count': len(data['problems']),
            'problems': [f"{p['label']}. {p['name']}" for p in data['problems']],
            'still_placeholder': data['still_placeholder'],
            'sty': sty_name,
            'sty_patched': sty_patched,
        }

        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
            dest = os.path.join(out_dir, f'{contest_key}.pdf')
            shutil.copy(pdf, dest)
            return dest, info
        return pdf, info
    finally:
        if out_dir:
            shutil.rmtree(workdir, ignore_errors=True)
