"""Ghi vết khi một lượt tham dự contest (ContestParticipation) bị XOÁ.

Vì sao cần: xoá một participation sẽ cascade xoá ContestSubmission (on_delete
CASCADE, judge/models/contest.py:811), làm các bài nộp mất link contest và biến
mất khỏi bảng xếp hạng. Việc này KHÔNG được hệ thống ghi log ở đâu (không
LogEntry vì không qua admin, không reversion), nên một lần xoá nhầm — dù bằng
shell, script, hay cascade — trước đây không để lại vết nào, phải dò cả buổi
mới ra. Module này ghi lại mỗi lần xoá kèm STACK TRACE, để lần sau truy ra ngay
CHỖ MÃ nào đã gọi xoá, kể cả khi không có request/người dùng (shell/script).

Chỉ log XOÁ, không log mỗi lần lưu: recompute chạy liên tục sẽ lưu participation
rất nhiều, log hết thì nhiễu. Xoá là sự kiện hiếm và đúng là thứ gây mất dữ liệu.
"""
import logging
import os
import traceback

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from judge.models import ContestParticipation

logger = logging.getLogger('hcmus.audit')
logger.setLevel(logging.INFO)

# Gắn một FileHandler bền, vào chỗ tiến trình web (user deploy) ghi được. Thử vài
# đường dẫn rồi mới chịu thua để không phụ thuộc một thư mục cụ thể có sẵn hay chưa.
_LOG_CANDIDATES = [
    os.environ.get('OJ_AUDIT_LOG', ''),
    '/home/deploy/logs/participation-audit.log',
    '/var/log/dmoj/participation-audit.log',
    '/tmp/oj-participation-audit.log',
]
if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
    for _path in _LOG_CANDIDATES:
        if not _path:
            continue
        try:
            os.makedirs(os.path.dirname(_path), exist_ok=True)
            _h = logging.FileHandler(_path, encoding='utf-8')
            _h.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            logger.addHandler(_h)
            break
        except OSError:
            continue


@receiver(pre_delete, sender=ContestParticipation)
def _log_participation_delete(sender, instance, **kwargs):
    # Bọc toàn bộ trong try: ghi log KHÔNG được phép làm hỏng thao tác xoá thật.
    try:
        try:
            user = instance.user.user.username
        except Exception:
            user = '?'
        contest = getattr(instance.contest, 'key', None) or instance.contest_id
        # Bỏ frame cuối (chính hàm này); giữ ~10 frame gần nhất cho gọn mà vẫn đủ
        # thấy chỗ gọi. Cascade xoá contest sẽ log nhiều dòng, mỗi dòng vẫn có stack.
        stack = ''.join(traceback.format_stack()[:-1][-10:])
        logger.warning(
            'DELETE ContestParticipation id=%s contest=%s user=%s virtual=%s '
            'score=%s cumtime=%s\nSTACK:\n%s',
            instance.pk, contest, user, instance.virtual,
            instance.score, instance.cumtime, stack)
    except Exception:
        pass
