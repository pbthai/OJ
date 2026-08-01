from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hcmus', '0014_publicscoreboard'),
    ]

    operations = [
        migrations.AlterField(
            model_name='publicscoreboard',
            name='is_enabled',
            field=models.BooleanField(
                default=False, verbose_name='bật link công khai',
                help_text='Tick vào đây rồi bấm Lưu để tạo link. '
                          'Bỏ tick là link tắt ngay, không cần xoá.'),
        ),
    ]
