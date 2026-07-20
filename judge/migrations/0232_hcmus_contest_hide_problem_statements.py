# Tuỳ biến FIT-HCMUS: cờ che đề bài khi thi trên giấy.
#
# Đặt tên có tiền tố 'hcmus_' để dễ nhận ra khi merge upstream vnoj. Nếu upstream
# cũng thêm một migration 0232_* phụ thuộc 0231, đồ thị migration của app judge sẽ
# có hai nhánh lá và Django báo "Conflicting migrations detected". Cách xử lý là
# chạy `manage.py makemigrations --merge judge` để sinh migration hợp nhất —
# thao tác thường lệ, không mất dữ liệu.
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0231_contest_replay_version'),
    ]

    operations = [
        migrations.AddField(
            model_name='contest',
            name='hide_problem_statements',
            field=models.BooleanField(
                default=False,
                help_text='For paper-based exams. Contest editors still see the statement, and nothing '
                          'is deleted. Stays on until you untick it.',
                verbose_name='hide problem statements'),
        ),
    ]
