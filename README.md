# FIT-HCMUS Online Judge

Hệ thống chấm bài trực tuyến của Khoa Công nghệ Thông tin, Trường Đại học Khoa học Tự nhiên, ĐHQG-HCM.
Chạy tại [coding.fit.hcmus.edu.vn](https://coding.fit.hcmus.edu.vn), phục vụ luyện tập ICPC, Olympic
Tin học, các kỳ thi chính thức của Khoa và bài tập các lớp học phần.

Đây là bản fork của [VNOJ](https://github.com/VNOI-Admin/OJ), vốn là fork của
[DMOJ](https://github.com/DMOJ/online-judge). Giấy phép AGPL-3.0, giữ nguyên theo repo gốc.

## Nhánh

Toàn bộ tuỳ biến nằm trên nhánh `hcmus`; `master` giữ nguyên để đồng bộ với upstream.

```bash
git remote add vnoj https://github.com/VNOI-Admin/OJ.git
git fetch vnoj
```

Nguyên tắc khi tuỳ biến: **đụng vào mã lõi càng ít càng tốt**. Phần lớn tính năng mới nằm trong app
riêng `hcmus/`; chỗ nào buộc phải sửa lõi thì chỉ thêm một chỗ móc (block template, một dòng
context) rồi để app mới lấp vào.

## Tính năng thêm so với VNOJ

| Tính năng | Đường dẫn | Ai dùng được |
|---|---|---|
| Sinh PDF đề bài trọn bộ của một kỳ thi | `/de-bai/` | staff |
| ICPC Resolver, diễn hoạt mở băng bảng xếp hạng | `/resolver/` | staff |
| Bảng xếp hạng team tổng hợp nhiều kỳ thi | `/xep-hang/` | tuỳ mức hiển thị |
| Quản lý nội dung trang chủ | `/admin/hcmus/homesection/` | superuser |
| Phân quyền theo tập hợp lồng nhau | `/admin/hcmus/permset/` | superuser |
| Ẩn đề bài khi thi trên giấy | ô tick trên form sửa kỳ thi | người quản lý kỳ thi |
| Nhập bài Polygon chấm theo chữ ký hàm, và bài `run-count=N` | trang import có sẵn của vnoj | staff |
| Sức khoẻ hệ thống, kèm công tắc bật/tắt máy chấm | `/suc-khoe/` | staff; công tắc cần quyền riêng |

### Sinh PDF đề bài

Gom đề của cả kỳ thi thành một file PDF theo khuôn olymp.sty. Nội dung lấy từ `Problem.description`
(markdown), chuyển sang LaTeX rồi biên dịch. Chọn được kiểu trình bày hoặc tải lên `.sty` riêng.
Biên dịch chạy nền qua Celery.

Vài điều đã trả giá để biết, ghi trong docstring của `hcmus/statement_pdf.py`:

- Bắt buộc `pdflatex`, không được `xelatex`. Dưới XeTeX, `vietnam.sty` rơi về font sai và diễn giải
  hỏng byte UTF-8 — "Cho một dãy số" thành "Cho mt dõy s" — mà vẫn exit 0, đủ số trang. Lỗi im lặng.
- Dữ liệu mẫu phải ghi ra file rồi nhúng bằng `\exmpfile`. `\exmp` không phải verbatim nên ký tự
  `_ # % & $` trong test làm vỡ biên dịch.
- Biên dịch LaTeX là thực thi mã. Phải chạy trong hộp cát: `-no-shell-escape`, `openin_any=p`,
  giới hạn CPU và dung lượng file. Lọc chuỗi không thay được cho việc này vì math mode là đường vòng.

### Bảng xếp hạng team

Gộp kết quả nhiều kỳ thi: mỗi kỳ thi có trọng số, mỗi team có thể được ghi nhận là người ra đề
(nhận điểm tối đa, penalty 0, không cần thi). Điểm bằng tổng điểm nhân trọng số; vắng mặt tính điểm
âm; hoà thì so tổng penalty. Bảng vàng tô nổi bật top N kèm huy chương ba hạng đầu.

Ba cái bẫy dữ liệu đã xử lý, ghi trong `hcmus/ranking.py`: chỉ lấy `virtual=LIVE` (một user có nhiều
lượt tham gia cho cùng một kỳ thi), bỏ `is_disqualified` (bị truất quyền thì `score` bị ghi đè thành
-9999), và `cumtime` không cùng đơn vị giữa các format nên trộn kỳ thi khác format thì cột penalty
mất ý nghĩa.

### Phân quyền theo tập hợp lồng nhau

Django Group không lồng nhau được, chỉ có một danh sách quyền phẳng. Model `PermSet` là tầng đại số
phía trên: một tập gồm quyền lẻ cộng các tập khác. Mỗi lần lưu, hệ thống nở ra rồi ghi kết quả phẳng
vào Django Group, nên phần còn lại của Django chạy y như cũ.

Hai tầng: khối nguyên tử (việc nhỏ, chỉ để lắp ghép) và vai trò (thành Group thật, gán cho người
dùng). Ví dụ `giangvien = ra-de + to-chuc-thi + mo-lop + quan-ly-xep-hang`, mà `ra-de` và
`to-chuc-thi` lại cùng chứa `ho-tro-ky-thuat` — khối này khai một lần, dùng ba nơi. Sửa một khối con
thì mọi vai trò chứa nó đổi theo cùng lúc.

Hai điều đáng lưu ý khi cấu hình quyền cho bản DMOJ nói chung:

- `auth.change_user` tương đương superuser. Form sửa user của Django có ô `is_superuser` và không
  khoá, nên ai sửa được user thì tự nâng mình lên root.
- Django admin đòi quyền trên chính model của inline, không kế thừa từ model cha. Thiếu
  `delete_contestproblem` thì không gỡ được bài khỏi kỳ thi; thiếu `add_ticketmessage` thì đọc được
  báo cáo mà không trả lời được.

### Ẩn đề bài khi thi trên giấy

Cờ `Contest.hide_problem_statements`. Thí sinh thấy nội dung thay thế, người quản lý kỳ thi vẫn thấy
đề. Không xoá gì, chỉ đổi tầng hiển thị. Cờ giữ nguyên tới khi bỏ tick, kết thúc kỳ thi không tự mở đề.

Che ở cả bốn đường hiển thị đề: trang chi tiết bài, `/problem/<code>/raw`, `/contest/<key>/all`
(trang này đổ toàn bộ đề và thí sinh đang thi vào được), và thẻ meta trong `<head>`.

Nhánh che phải nằm **ngoài** khối `{% cache %}` của `problem-detail.html`: khoá cache
`problem_html` chỉ gồm `(problem.id, MATH_ENGINE, LANGUAGE_CODE)`, không có user, nên cache cả nhánh
che sẽ đầu độc theo hai chiều — thí sinh đọc trúng đề thật, hoặc giảng viên nhìn thấy nội dung thay
thế suốt 24 giờ.

### Sức khoẻ hệ thống

Hàng đợi chấm, máy chấm, CPU, RAM, đĩa, dịch vụ, sao lưu; tự làm mới mỗi 3 giây.

Số liệu chia theo **độ trễ** chứ không theo nguồn. Hàng đợi, máy chấm, RAM, đĩa và load
đọc thẳng mỗi lần làm mới nên độ trễ bằng 0. Chỉ những thứ cần quyền root — trạng thái
dịch vụ, container, thư mục sao lưu — mới đi qua một file JSON do tiến trình chạy dưới
root ghi mỗi 3 giây.

Kèm công tắc bật/tắt từng máy chấm. Web **không tự chạy docker**: nó ghi ý muốn ra file
spool, tiến trình root đọc rồi thi hành, và chỉ chấp nhận tên khớp `^judge[0-9]{1,2}$`
nằm trong danh sách container nó tự liệt kê được. Cho tiến trình web quyền sudo là biến
một lỗ trong Django thành lỗ root.

Chạy nhiều máy chấm song song trên một máy chủ có làm lệch thời gian không: đo trên 8
nhân với 6 máy chấm cùng bận, thời gian CPU phình 1% ở trung vị và 4% ở trường hợp xấu
nhất so với chạy một mình. Xem `docs/10-them-may-cham.md` ở repo công cụ vận hành.

### Nhập bài Polygon nâng cao

vnoj chưa nhập được hai dạng bài này:

- **Chấm theo chữ ký hàm** (IOI signature grading): resource `.h` có `<asset name="solution"/>`,
  tách thành entry chứa `main()` và header khai báo, giữ nguyên tên file để `#include` của thí sinh
  resolve đúng.
- **`run-count=N`**: bài chạy nhiều lượt, output lượt trước nối vào input lượt sau. Sinh custom judge
  tương ứng.

## Sửa vào mã lõi

Cố ý giữ ở mức nhỏ nhất có thể, để còn merge upstream được.

| File | Thay đổi |
|---|---|
| `judge/utils/codeforces_polygon.py` | Nhập bài chấm theo chữ ký hàm và bài `run-count=N` |
| `judge/utils/problem_data.py` | Sinh custom judge cho bài chạy nhiều lượt |
| `judge/models/contest.py` | Cờ `hide_problem_statements` và `statements_hidden_for()` |
| `judge/models/problem.py` | `Problem.statement_hidden_for()`, điểm kiểm tra dùng chung |
| `judge/utils/statement.py` | File mới: nội dung thay thế khi đề bị ẩn |
| `judge/views/problem.py` | Che đề ở trang chi tiết, `/raw`, và bản PDF |
| `judge/views/blog.py` | Bơm dữ liệu trang chủ vào context, bọc `try/except` để deploy không có app `hcmus` vẫn chạy |
| `judge/admin/contest.py`, `judge/forms.py` | Thêm cờ ẩn đề vào hai form sửa kỳ thi |
| `templates/base.html` | Quy ước key navbar: `adm` chỉ hiện với staff, `root` chỉ hiện với superuser |
| `templates/blog/list.html` | Block `main_column_top`; tách vòng lặp bài ra `post-loop.html` |
| `templates/home.html` | Ghi đè hai block để nhét nội dung của `hcmus` |
| `templates/problem/problem-detail.html` | Nhánh che đề, đặt ngoài khối `{% cache %}` |
| `locale/vi/LC_MESSAGES/django.po` | Dịch chuỗi mới sang tiếng Việt |

Ngoài ra là phần thương hiệu: logo trường, favicon, mask icon cho pinned tab, footer, trang 502, và
logo trong thư đặt lại mật khẩu.

## Cài đặt

Theo [tài liệu của vnoj](https://vnoi-admin.github.io/vnoj-docs/#/site/installation), chỉ khác là
clone repo này và dùng nhánh `hcmus`. Vài điều bản này cần thêm:

- **`websocket-client`** phải cài thủ công. Nó thiếu trong `requirements.txt` của vnoj, và thiếu nó
  thì `manage.py` chết với `ImportError: cannot import name 'WebSocketException'`.
- **`pandoc`** cho phần nhập bài Polygon và sinh PDF đề.
- **TeX Live** nếu dùng tính năng sinh PDF đề. Bộ tối thiểu trên Ubuntu 24.04 khoảng 0,4 GB, so với
  7,8 GB của `texlive-full`:

  ```
  texlive-latex-base texlive-latex-recommended texlive-latex-extra
  texlive-pictures texlive-fonts-recommended texlive-lang-other
  ```

  `texlive-lang-other` chứa vntex, dễ bị bỏ sót vì tên không gợi ý gì tới tiếng Việt.
- App `hcmus` bật bằng `INSTALLED_APPS += ('hcmus',)` trong `local_settings.py`, URL nối bằng
  `urlpatterns.append(path("", include("hcmus.urls")))` trong `local_urls.py`. Hai file này nằm trong
  `.gitignore`.

## Giấy phép

AGPL-3.0, theo repo gốc. Xem [LICENSE](LICENSE).

Các file trong `hcmus/tex/` (`icpcHCMUS.sty`, `icpc.sty`, `olpHCMUS.sty`) là bản chỉnh sửa từ
[olymp.sty](https://github.com/GassaFM/olymp.sty), giấy phép MIT. Nguyên văn giấy phép và danh sách
chỗ đã sửa nằm ở [hcmus/tex/LICENSE.olymp](hcmus/tex/LICENSE.olymp) — MIT buộc bản phân phối lại
phải kèm đủ thông báo bản quyền, một liên kết là chưa đủ.

`ICPClogo.png` là nhãn hiệu của ICPC Foundation, `HCMUSlogo.png` là nhãn hiệu của Trường Đại học
Khoa học Tự nhiên. Hai file này đi kèm để tính năng sinh PDF chạy được ngay; nếu bạn dùng lại mã
này cho đơn vị khác thì nên thay bằng logo của mình.

Tài liệu gốc của thượng nguồn: [VNOJ](https://github.com/VNOI-Admin/OJ) ·
[DMOJ](https://github.com/DMOJ/online-judge) · [tính năng](https://github.com/DMOJ/online-judge#features)
