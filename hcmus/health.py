"""Sức khoẻ hệ thống cho trang quản trị.

Chia số liệu theo ĐỘ TRỄ, không phải theo nguồn:

  - Đọc thẳng, độ trễ 0: hàng đợi chấm, máy chấm, RAM, đĩa, load. Tiến trình web
    lấy được hết mà không cần quyền gì thêm, nên không có lý do gì bắt chúng đi
    vòng qua file.
  - Qua file JSON, trễ tối đa một chu kỳ thu thập: trạng thái dịch vụ, container,
    và bản sao lưu. Ba thứ này cần quyền root (supervisorctl, docker socket,
    /backup.daily là root:root) mà cho tiến trình web quyền sudo thì một lỗ trong
    Django sẽ thành lỗ root.

CPU phần trăm nằm ở nhóm sau dù không cần quyền: nó phải so hai mẫu /proc/stat
cách nhau một khoảng, mà mỗi request web là một lần chạy rời rạc, không có mẫu
trước để so. Tiến trình thu thập chạy vòng lặp liên tục nên tính đúng.
"""
import json
import os
import shutil
import time

HEALTH_FILE = '/var/lib/oj-health/status.json'

# Ngưỡng đổi màu. Đặt ở đây để sửa một chỗ, template chỉ đọc mức đã tính.
DISK_WARN, DISK_BAD = 80, 90              # phần trăm đã dùng
MEM_WARN, MEM_BAD = 85, 95
QUEUE_WARN, QUEUE_BAD = 20, 100           # số bài đang chờ
QUEUE_AGE_WARN, QUEUE_AGE_BAD = 60, 300   # giây, bài chờ lâu nhất
STALE_WARN, STALE_BAD = 20, 60            # giây, file JSON cũ tới mức nào


def _level(value, warn, bad):
    if value is None:
        return 'unknown'
    return 'bad' if value >= bad else ('warn' if value >= warn else 'ok')


def _meminfo():
    out = {}
    try:
        with open('/proc/meminfo') as f:
            for line in f:
                k, _, v = line.partition(':')
                out[k] = int(v.split()[0]) * 1024
    except Exception:
        return None
    total = out.get('MemTotal')
    avail = out.get('MemAvailable')
    if not total:
        return None
    used = total - (avail or 0)
    return {'total': total, 'used': used, 'percent': round(100.0 * used / total, 1)}


def _loadavg():
    try:
        with open('/proc/loadavg') as f:
            a, b, c = f.readline().split()[:3]
        return [float(a), float(b), float(c)]
    except Exception:
        return None


def _ncpu():
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def _from_file():
    """Phần cần quyền root. Trả về (dữ liệu, số giây đã cũ)."""
    try:
        with open(HEALTH_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return data, max(0, int(time.time() - data.get('ts', 0)))
    except FileNotFoundError:
        return None, None
    except Exception:
        return None, None


def _judges(filedata=None):
    """Ghép ba nguồn: bản ghi Judge (CSDL), công tắc, và trạng thái container thật."""
    from hcmus.models import JudgeSwitch
    from judge.models import Judge

    # Container do tiến trình root liệt kê; dùng để biết máy đang chạy hay đã dừng
    cont = {c['name']: c.get('status', '') for c in (filedata or {}).get('containers', [])}
    # Thấy container mới thì tự tạo công tắc, mặc định bật
    names = [n for n in cont if n.startswith('judge')]
    if names:
        try:
            JudgeSwitch.sync_from_containers(names)
        except Exception:
            pass
    sw = dict(JudgeSwitch.objects.values_list('name', 'enabled'))

    out = []
    for j in Judge.objects.order_by('name'):
        st = cont.get(j.name)
        out.append({
            'name': j.name,
            'online': j.online,
            'ping_ms': round(j.ping_ms, 1) if j.online and j.ping_ms else None,
            'load': round(j.load, 3) if j.online and j.load is not None else None,
            'switch': sw.get(j.name),          # None = chưa có container tương ứng
            'container': st,
            'running': bool(st and st.startswith('Up')),
        })
    return out


def _queue():
    from django.utils import timezone
    from judge.models import Submission
    # Các trạng thái coi là ĐÃ XONG; còn lại là đang chờ hoặc đang chấm.
    done = ['D', 'IE', 'CE', 'AB']
    qs = Submission.objects.exclude(status__in=done)
    n = qs.count()
    oldest = qs.order_by('date').values_list('date', flat=True).first()
    age = int((timezone.now() - oldest).total_seconds()) if oldest else 0
    return {'count': n, 'oldest_age_sec': age,
            'level': max(_level(n, QUEUE_WARN, QUEUE_BAD),
                         _level(age, QUEUE_AGE_WARN, QUEUE_AGE_BAD),
                         key=['ok', 'warn', 'bad', 'unknown'].index)}


def snapshot():
    """Toàn bộ số liệu cho trang và cho endpoint JSON."""
    from django.utils import timezone
    from judge.models import Submission

    filedata, stale = _from_file()
    mem = _meminfo()
    load = _loadavg()
    ncpu = _ncpu()

    try:
        total, used, free = shutil.disk_usage('/')
        disk = {'total': total, 'used': used, 'free': free,
                'percent': round(100.0 * used / total, 1)}
    except Exception:
        disk = None

    # problems_size do tiến trình thu thập đo (mỗi 60 giây). Quét cả cây bài ngay
    # trong request thì mỗi nhịp tự làm mới lại quét một lần — vô nghĩa vì con số
    # này gần như không đổi.
    day_ago = timezone.now() - timezone.timedelta(days=1)
    judges = _judges(filedata)

    return {
        'now': int(time.time()),
        'live': {
            'queue': _queue(),
            'judges': judges,
            'judges_online': sum(1 for j in judges if j['online']),
            'judges_total': len(judges),
            'submissions_24h': Submission.objects.filter(date__gte=day_ago).count(),
            'mem': mem,
            'mem_level': _level(mem['percent'] if mem else None, MEM_WARN, MEM_BAD),
            'disk': disk,
            'disk_level': _level(disk['percent'] if disk else None, DISK_WARN, DISK_BAD),
            'load': load,
            'ncpu': ncpu,
            'load_level': _level(round(100.0 * load[0] / ncpu, 1) if load else None, 80, 100),
            'problems_size': (filedata or {}).get('problems_size'),
        },
        'privileged': filedata,
        'stale_sec': stale,
        'stale_level': _level(stale, STALE_WARN, STALE_BAD),
        'health_file': HEALTH_FILE,
    }
