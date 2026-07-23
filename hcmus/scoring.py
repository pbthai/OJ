"""Điểm động theo số người giải (FIT-HCMUS).

Mỗi bài bắt đầu 10 điểm; giá trị tại một thời điểm là hàm của số người ĐÃ AC:

    r = 10 / log2(n + 2)      (làm tròn 2 chữ số)

n gấp đôi thì mẫu số +1 nên điểm giảm dần — bài càng nhiều người giải càng "rẻ".
Mọi người đã AC một bài đều nhận CÙNG giá trị r hiện tại của bài đó (không đóng
băng theo thời điểm giải). Tổng điểm của một user = Σ r trên các bài họ đã AC.

n lấy từ `Problem.user_count` — DMOJ đã duy trì sẵn theo thời gian thực và định
nghĩa đúng bằng "số user KHÔNG ẩn có ít nhất một submission AC" (judge/models/
problem.py:update_stats). Nhờ vậy giá trị mỗi bài tự cập nhật mỗi lần có thêm một
người AC mà không cần móc vào đường chấm bài.

Đây là hệ thống RIÊNG, KHÔNG đụng points/performance_points gốc của DMOJ (những
cái đó gắn với contest và bảng rank sẵn có). Chỉ đọc submission, ghi vào bảng
hcmus.UserScore.
"""
import math

from django.db import transaction
from django.utils import timezone

BASE_POINTS = 10.0


def problem_value(solvers):
    """Điểm hiện tại của một bài theo số người đã AC (n >= 0).

    n = 0 (bài mới, chưa ai giải) -> 10.00. Không bao giờ chia cho 0 vì mẫu là
    log2(n+2) >= 1.
    """
    return round(BASE_POINTS / math.log2(solvers + 2), 2)


def recompute_user_scores(public_only=False):
    """Tính lại toàn bộ bảng UserScore. Trả về số user đã ghi.

    total = điểm giải bài + contest rating (Profile.rating). Người có rating NHƯNG
    chưa giải bài nào vẫn được xếp; người có điểm bài NHƯNG chưa rated thì rating
    để None và total = điểm bài. Hiện chưa contest nào rated nên total = điểm bài
    với mọi người, sẽ tự khớp khi có kỳ rated.

    public_only=False (mặc định): tính MỌI bài đã tạo — đúng ý "các bài đã tạo", và
    cần thiết ở deploy này vì phần lớn bài luyện tập để riêng, lọc công khai thì
    bảng gần như rỗng. True: chỉ tính bài công khai, không riêng-cho-tổ-chức.
    """
    from judge.models import Problem, Profile, Submission

    from hcmus.models import UserScore

    problems = Problem.objects.all()
    if public_only:
        problems = problems.filter(is_public=True, is_organization_private=False)
    # r cho từng bài, theo user_count (số người AC không tính user ẩn).
    value = {pid: problem_value(uc)
             for pid, uc in problems.values_list('id', 'user_count')}

    # Cặp (user, bài) AC duy nhất, chỉ trên các bài được tính, bỏ user ẩn.
    pairs = (Submission.objects
             .filter(result='AC', user__is_unlisted=False, problem_id__in=value.keys())
             .values_list('user_id', 'problem_id').distinct())

    points = {}   # user_id -> [điểm giải bài, số bài]
    for user_id, problem_id in pairs.iterator():
        acc = points.setdefault(user_id, [0.0, 0])
        acc[0] += value[problem_id]
        acc[1] += 1

    # Ảnh chụp contest rating (không tính user ẩn). Người có rating nhưng chưa giải
    # bài cũng được vào bảng.
    ratings = dict(Profile.objects.filter(is_unlisted=False, rating__isnull=False)
                   .values_list('id', 'rating'))

    now = timezone.now()
    rows = []
    for user_id in set(points) | set(ratings):
        pts, solved = points.get(user_id, [0.0, 0])
        pts = round(pts, 2)
        rating = ratings.get(user_id)                 # None nếu chưa rated
        total = round(pts + (rating or 0), 2)
        rows.append(UserScore(profile_id=user_id, points=pts, rating=rating,
                              total=total, solved=solved, updated=now))

    with transaction.atomic():
        # Tính lại từ đầu cho sạch: xoá hết rồi ghi lại. Bảng nhỏ (cỡ số người dùng).
        UserScore.objects.all().delete()
        UserScore.objects.bulk_create(rows)
    return len(rows)
