import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0232_hcmus_contest_hide_problem_statements'),
        ('hcmus', '0012_contestprinter'),
    ]

    operations = [
        migrations.CreateModel(
            name='TeammatePost',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('display_name', models.CharField(
                    max_length=100, verbose_name='tên hiển thị',
                    help_text='Tên bạn muốn hiện trên mẩu tin.')),
                ('cohort', models.CharField(
                    max_length=40, blank=True, verbose_name='khoá',
                    help_text='Ví dụ: K22, CTT 2023, 12A1...')),
                ('strengths', models.CharField(
                    max_length=200, blank=True, verbose_name='thế mạnh',
                    help_text='Mảng bạn làm tốt: quy hoạch động, đồ thị, hình học, số học...')),
                ('achievements', models.TextField(
                    blank=True, verbose_name='thành tích',
                    help_text='Giải thưởng, kỳ thi từng dự, rating...')),
                ('contact', models.CharField(
                    max_length=200, blank=True, verbose_name='liên hệ',
                    help_text='Facebook, Discord, email... để người khác liên lạc được.')),
                ('note', models.TextField(
                    blank=True, verbose_name='ghi chú',
                    help_text='Điều khác muốn nói: cần teammate thế nào, '
                              'thời gian luyện tập được...')),
                ('status', models.CharField(
                    max_length=10, default='looking', verbose_name='trạng thái',
                    choices=[('looking', 'Đang tìm teammate'), ('matched', 'Đã khớp')])),
                ('team_name', models.CharField(
                    max_length=100, blank=True, verbose_name='tên đội',
                    help_text='Khi đã khớp, tên đội của bạn (không bắt buộc).')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='đăng lúc')),
                ('modified', models.DateTimeField(auto_now=True, verbose_name='sửa lần cuối')),
                ('profile', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, related_name='teammate_post',
                    to='judge.profile', verbose_name='tài khoản')),
            ],
            options={
                'verbose_name': 'mẩu tin tìm teammate',
                'verbose_name_plural': 'mẩu tin tìm teammate',
                'ordering': ['-modified'],
            },
        ),
        migrations.AlterField(
            model_name='homesection',
            name='kind',
            field=models.CharField(
                max_length=16, default='custom', verbose_name='kind',
                choices=[('post', 'Blog post'), ('ranking', 'Team ranking'), ('contest', 'Contest'),
                         ('custom', 'Custom content'), ('feed', 'Blog feed (the usual list of posts)'),
                         ('teammate', 'Bảng tin tìm teammate')]),
        ),
    ]
