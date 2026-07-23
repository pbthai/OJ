"""Tính lại điểm động cho từng user (r = 10/log2(n+2) mỗi bài đã AC).

Chạy định kỳ 3h sáng qua cron, hoặc chạy tay bất cứ lúc nào:
    python manage.py hcmus_recompute_scores
    python manage.py hcmus_recompute_scores --all-problems --top 20
"""
from django.core.management.base import BaseCommand

from hcmus.scoring import recompute_user_scores


class Command(BaseCommand):
    help = 'Tính lại bảng điểm động (hcmus.UserScore) từ các submission AC.'

    def add_arguments(self, parser):
        parser.add_argument('--public-only', action='store_true',
                            help='Chỉ tính bài công khai (mặc định: tính mọi bài đã tạo).')
        parser.add_argument('--top', type=int, default=10,
                            help='In ra top N sau khi tính (mặc định 10).')

    def handle(self, *args, **options):
        from hcmus.models import UserScore
        n = recompute_user_scores(public_only=options['public_only'])
        scope = 'bài công khai' if options['public_only'] else 'mọi bài'
        self.stdout.write(self.style.SUCCESS(f'Đã tính điểm cho {n} user ({scope}).'))
        top = options['top']
        if top > 0:
            self.stdout.write(f'Top {top} (total = rating + điểm bài):')
            rows = UserScore.objects.select_related('profile__user')[:top]
            for i, s in enumerate(rows, 1):
                rating = s.rating if s.rating is not None else '—'
                self.stdout.write(f'  {i:2}. {s.profile.user.username:24} '
                                  f'total={s.total:8.2f}  (rating={rating}, '
                                  f'điểm bài={s.points:g}, {s.solved} bài)')
