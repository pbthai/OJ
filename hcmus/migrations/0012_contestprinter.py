import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('judge', '0232_hcmus_contest_hide_problem_statements'),
        ('hcmus', '0011_printing'),
    ]

    operations = [
        migrations.CreateModel(
            name='ContestPrinter',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('contest', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, related_name='hcmus_printer',
                    to='judge.contest', verbose_name='contest')),
                ('printer', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+',
                    to='hcmus.printer', verbose_name='printer',
                    help_text='Chọn máy in để CHO PHÉP thí sinh in bài trong kỳ thi này. '
                              'Để trống = không cho phép in.')),
            ],
            options={'verbose_name': 'contest printing', 'verbose_name_plural': 'contest printing'},
        ),
    ]
