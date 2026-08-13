"""Đọc đề bài viết bằng LaTeX theo bộ macro olymp/ptnk -> Markdown của site.

Đây là định dạng dùng để in đề kỳ thi: mỗi bài là một môi trường `problem`, các
mục đánh dấu bằng \\InputFile, \\OutputFile, \\Examples, \\Scoring...

    \\begin{problem}{TÊN BÀI}{stdin}{stdout}{1 s}{256 MB}
    Phần dẫn đề...
    \\InputFile
    ...
    \\OutputFile
    ...
    \\Examples
    \\begin{example}
    \\exmp{dữ liệu vào}{kết quả ra}
    \\end{example}
    \\Explanation
    ...
    \\Scoring
    ...
    \\end{problem}

Kết quả ghép theo ĐÚNG thứ tự và tiêu đề mà importer Polygon sinh ra
(codeforces_polygon.py:739), vì hcmus/statement_pdf.py tách ngược lại theo đúng
các mốc đó để dựng LaTeX khi xuất PDF. Đổi tiêu đề ở đây là hỏng đường xuất PDF.

Phần văn xuôi để pandoc dịch, dùng lại pandoc_tex_to_markdown của importer
Polygon — cùng một bộ lọc Lua, nên đề nhập bằng hai đường cho ra cùng một kiểu.
Riêng dữ liệu mẫu KHÔNG qua pandoc: nó là văn bản thô, phải giữ nguyên từng ký tự.
"""
import re

from judge.utils.codeforces_polygon import pandoc_tex_to_markdown


class TexImportError(Exception):
    pass


# Các macro đánh mục. Một macro có thể viết nhiều kiểu (\Note và \Notes), gom hết
# về một khoá. \Constraints và \Subtask xếp chung với \Scoring vì trên site chỉ có
# một mục "Scoring" (statement_pdf.py cũng gộp như vậy).
SECTION_MACROS = {
    'InputFile': 'input', 'Input': 'input',
    'OutputFile': 'output', 'Output': 'output',
    'Interaction': 'interaction',
    'Scoring': 'scoring', 'Constraints': 'scoring', 'Subtask': 'scoring',
    'Subtasks': 'scoring', 'Limits': 'scoring',
    'Explanation': 'notes', 'Note': 'notes', 'Notes': 'notes',
    'Examples': '__examples__', 'Example': '__examples__',
}
_SECTION_RE = re.compile(r'\\(' + '|'.join(sorted(SECTION_MACROS, key=len, reverse=True)) +
                         r')\b\s*')

# Lệnh chỉ phục vụ việc in ấn, bỏ đi cho sạch trước khi đưa qua pandoc.
_DROP_RE = re.compile(
    r'\\(?:addcontentsline|thispagestyle|pagestyle|vspace\*?|hspace\*?|newpage|clearpage|'
    r'noindent|centering|par)\b\s*(?:\{[^{}]*\}){0,3}(?:\[[^\]]*\])?')


def strip_comments(text):
    """Bỏ chú thích LaTeX: '%' tới hết dòng, trừ '\\%'.

    Làm đúng như LaTeX làm, kể cả bên trong \\exmp: bộ macro olymp không đổi
    catcode nên '%' trong dữ liệu mẫu vốn cũng đã bị LaTeX nuốt. Ai cần dấu phần
    trăm trong dữ liệu mẫu thì phải viết '\\%', và ở đây trả nó về '%'.
    """
    out = []
    for line in text.splitlines():
        buf, i = [], 0
        while i < len(line):
            ch = line[i]
            if ch == '\\' and i + 1 < len(line):
                buf.append(line[i:i + 2])
                i += 2
                continue
            if ch == '%':
                break
            buf.append(ch)
            i += 1
        out.append(''.join(buf))
    return '\n'.join(out)


def read_group(text, pos):
    """Đọc một nhóm {...} bắt đầu từ vị trí pos (bỏ qua khoảng trắng đứng trước).

    Trả về (nội dung, vị trí sau dấu '}'). Đếm ngoặc lồng nhau và bỏ qua ngoặc đã
    được escape, nếu không thì $\\{a, b\\}$ trong đề sẽ cắt nhầm chỗ.
    """
    while pos < len(text) and text[pos] in ' \t\r\n':
        pos += 1
    if pos >= len(text) or text[pos] != '{':
        return None, pos
    depth, i, start = 0, pos, pos + 1
    while i < len(text):
        ch = text[i]
        if ch == '\\':
            i += 2
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i], i + 1
        i += 1
    raise TexImportError('Thiếu dấu } đóng — file .tex chưa cân ngoặc.')


def _clean_body(body):
    """Gỡ lớp bọc chỉ dùng để trình bày: \\small{...}, \\begin{center}..."""
    body = _DROP_RE.sub('', body)
    body = re.sub(r'\\begin\{(center|flushleft|flushright)\}|\\end\{(center|flushleft|flushright)\}',
                  '', body)
    # \small{ ... } bọc cả bài: gỡ đúng một lớp, giữ nguyên phần bên trong.
    m = re.match(r'\s*\\(?:small|footnotesize|large|normalsize)\s*\{', body)
    if m:
        inner, end = read_group(body, m.end() - 1)
        if inner is not None and not body[end:].strip():
            body = inner
    return body.strip()


def parse_examples(text):
    """Lấy các cặp (dữ liệu vào, kết quả ra) từ \\begin{example}...\\end{example}.

    Chỉ đọc \\exmp{}{}. Bỏ qua \\exmpfile{}{} vì nó trỏ tới file ngoài, mà ở đây
    chỉ có mỗi file .tex được tải lên.
    """
    samples, bo_qua = [], 0
    for m in re.finditer(r'\\exmp(file)?\b', text):
        if m.group(1):
            bo_qua += 1
            continue
        inp, pos = read_group(text, m.end())
        out, _ = read_group(text, pos)
        if inp is None or out is None:
            continue
        samples.append((inp.strip('\n').rstrip(), out.strip('\n').rstrip()))
    return samples, bo_qua


def parse_problems(text):
    """Tách file .tex thành danh sách bài. Mỗi bài là một dict các mục đã tách."""
    text = strip_comments(text)
    problems = []
    for m in re.finditer(r'\\begin\{problem\}', text):
        pos = m.end()
        args = []
        for _ in range(5):
            arg, pos = read_group(text, pos)
            if arg is None:
                break
            args.append(arg.strip())
        end = text.find(r'\end{problem}', pos)
        if end < 0:
            raise TexImportError(r'Có \begin{problem} mà không có \end{problem}.')
        problems.append(_split_sections(_clean_body(text[pos:end]), args))
    return problems


def _split_sections(body, args):
    args = (args + [''] * 5)[:5]
    out = {'title': args[0], 'input_file': args[1], 'output_file': args[2],
           'time_limit': args[3], 'memory_limit': args[4],
           'legend': '', 'input': '', 'output': '', 'interaction': '',
           'scoring': '', 'notes': '', 'samples': [], 'exmpfile': 0}

    marks = list(_SECTION_RE.finditer(body))
    out['legend'] = (body[:marks[0].start()] if marks else body).strip()
    for i, m in enumerate(marks):
        key = SECTION_MACROS[m.group(1)]
        chunk = body[m.end():(marks[i + 1].start() if i + 1 < len(marks) else len(body))]
        if key == '__examples__':
            samples, bo_qua = parse_examples(chunk)
            out['samples'] += samples
            out['exmpfile'] += bo_qua
        elif out[key]:
            out[key] += '\n\n' + chunk.strip()   # macro lặp lại thì nối tiếp
        else:
            out[key] = chunk.strip()
    return out


def build_description(problem):
    """Ghép một bài đã tách thành Markdown cho Problem.description.

    Thứ tự và tiêu đề bám theo importer Polygon để statement_pdf.py tách lại được.
    """
    def md(tex):
        return pandoc_tex_to_markdown(tex).strip()

    parts = []
    if problem['legend']:
        parts.append(md(problem['legend']))
    for key, heading in (('input', 'Input'), ('output', 'Output'),
                         ('interaction', 'Interaction'), ('scoring', 'Scoring')):
        if problem[key]:
            parts.append(f'## {heading}\n\n' + md(problem[key]))
    for i, (inp, out) in enumerate(problem['samples'], start=1):
        parts.append(f'## Sample Input {i}\n\n```\n{inp}\n```')
        parts.append(f'## Sample Output {i}\n\n```\n{out}\n```')
    if problem['notes']:
        parts.append('## Notes\n\n' + md(problem['notes']))
    return '\n\n'.join(p for p in parts if p.strip()) + '\n'
