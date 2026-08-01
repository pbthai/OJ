import django.db.models.deletion
from django.db import migrations, models

import hcmus.models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0232_hcmus_contest_hide_problem_statements'),
        ('hcmus', '0013_teammatepost'),
    ]

    operations = [
        migrations.CreateModel(
            name='PublicScoreboard',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.CharField(
                    max_length=64, unique=True, db_index=True,
                    default=hcmus.models._new_scoreboard_token, verbose_name='mã liên kết',
                    help_text='Phần bí mật trong đường dẫn. Đổi mã = link cũ hết dùng được.')),
                ('is_enabled', models.BooleanField(
                    default=True, verbose_name='bật link công khai',
                    help_text='Bỏ tick là link tắt ngay, không cần xoá.')),
                ('note', models.CharField(
                    max_length=200, blank=True, verbose_name='ghi chú',
                    help_text='Hiện ngay dưới tên kỳ thi trên trang công khai '
                              '(ví dụ: "Kết quả chính thức").')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='tạo lúc')),
                ('contest', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='hcmus_public_scoreboard', to='judge.contest',
                    verbose_name='kỳ thi')),
            ],
            options={
                'verbose_name': 'bảng xếp hạng công khai',
                'verbose_name_plural': 'bảng xếp hạng công khai',
            },
        ),
    ]
