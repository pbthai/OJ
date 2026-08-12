"""Signal của app hcmus. Nạp ở HCMUSConfig.ready()."""
import logging

from django.dispatch import receiver
from registration.signals import user_activated

logger = logging.getLogger('hcmus')


@receiver(user_activated, dispatch_uid='hcmus_promote_faculty')
def promote_faculty_on_activation(sender, user, request=None, **kwargs):
    """Người tự đăng ký bằng email @fit.hcmus.edu.vn -> nhân viên + giảng viên.

    Móc vào lúc KÍCH HOẠT chứ không phải lúc điền form: tín hiệu này chỉ bắn sau
    khi người dùng bấm link trong thư, tức là đã chứng minh mình giữ hộp thư đó.
    Gắn vào lúc đăng ký thì ai gõ email của một thầy trong khoa cũng thành giảng
    viên.
    """
    from hcmus.accounts import promote_faculty
    try:
        if promote_faculty(user):
            logger.info('Cấp quyền giảng viên theo email khoa: %s <%s>',
                        user.username, user.email)
    except Exception:  # noqa: BLE001  hỏng chỗ này không được chặn việc kích hoạt
        logger.exception('Không cấp được quyền giảng viên cho %s', user.username)
