import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Xem chú thích 0003: bám 0232 để có Profile/Submission/Contest, KHÔNG bám 0233.
        ('judge', '0232_hcmus_contest_hide_problem_statements'),
        ('hcmus', '0010_userscore_rating_total'),
    ]

    operations = [
        migrations.CreateModel(
            name='TeamRoom',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('room', models.CharField(blank=True, max_length=60, verbose_name='room')),
                ('updated', models.DateTimeField(auto_now=True, verbose_name='last updated')),
                ('profile', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE,
                                                 related_name='team_room', to='judge.profile', verbose_name='user')),
            ],
            options={'verbose_name': 'team room', 'verbose_name_plural': 'team rooms'},
        ),
        migrations.CreateModel(
            name='Printer',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60, verbose_name='name')),
                ('cups_dest', models.CharField(
                    help_text='Tên hàng đợi CUPS (lpstat -e) hoặc URI ipp://.../socket://ip:9100',
                    max_length=120, verbose_name='CUPS destination')),
                ('is_active', models.BooleanField(
                    default=False, help_text='Máy in đang dùng để in bài. Chỉ một cái nên bật.',
                    verbose_name='active')),
                ('note', models.CharField(blank=True, max_length=200, verbose_name='note')),
            ],
            options={'verbose_name': 'printer', 'verbose_name_plural': 'printers'},
        ),
        migrations.CreateModel(
            name='PrintRequest',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('team', models.CharField(blank=True, max_length=100, verbose_name='team')),
                ('room', models.CharField(blank=True, max_length=60, verbose_name='room')),
                ('problem', models.CharField(blank=True, max_length=100, verbose_name='problem')),
                ('language', models.CharField(blank=True, max_length=40, verbose_name='language')),
                ('pages', models.PositiveIntegerField(default=0, verbose_name='pages')),
                ('status', models.CharField(choices=[('Q', 'Queued'), ('P', 'Printed'), ('F', 'Failed'),
                                                     ('R', 'Rejected — over page limit')],
                                            default='Q', max_length=1, verbose_name='status')),
                ('printer', models.CharField(blank=True, max_length=60, verbose_name='printer')),
                ('error', models.CharField(blank=True, max_length=300, verbose_name='error')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='created')),
                ('contest', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                              related_name='+', to='judge.contest', verbose_name='contest')),
                ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                              related_name='print_requests', to='judge.profile', verbose_name='user')),
                ('submission', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                                 related_name='+', to='judge.submission', verbose_name='submission')),
            ],
            options={
                'verbose_name': 'print request', 'verbose_name_plural': 'print requests',
                'ordering': ['-created'],
                'permissions': (('view_print_queue', 'View the in-contest print queue'),),
            },
        ),
    ]
