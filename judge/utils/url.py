import urllib.parse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator

URL_VALIDATOR = URLValidator(schemes=['http', 'https'])


def get_absolute_url(url, host):
    try:
        URL_VALIDATOR(url)
        return url
    except ValidationError:
        return urllib.parse.urljoin(host, url)


def get_absolute_submission_file_url(source):
    # Máy chấm TỰ TẢI file bài nộp (Scratch .sb3, output-only .zip) qua HTTP, xem
    # judge-server helper_files.download_source_code. Nếu dựng URL từ SITE_FULL_URL
    # thì nó đi vòng ra Internet rồi quay lại qua HAProxy của trường — mà HAProxy
    # chặn User-Agent 'python-requests' bằng 403, nên MỌI bài nộp dạng file đều ra
    # lỗi nội bộ (đo được: cùng URL, UA curl ra 200, UA python-requests ra 403).
    #
    # HCMUS_JUDGE_FILE_BASE_URL cho phép trỏ máy chấm vào địa chỉ nội bộ của chính
    # máy chủ. Ngoài việc tránh cái chặn kia, nó còn bỏ được phụ thuộc vào đường
    # Internet của trường trong giờ thi. Không khai thì giữ nguyên hành vi cũ.
    base = getattr(settings, 'HCMUS_JUDGE_FILE_BASE_URL', '') or settings.SITE_FULL_URL
    return get_absolute_url(source, base)


def get_absolute_pdf_url(pdf_url):
    return get_absolute_url(pdf_url, settings.SITE_FULL_URL)
