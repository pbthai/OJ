# Sinh bằng makemigrations trên server rồi mang về repo (máy local không có Django).
# Bảng xếp hạng team tổng hợp nhiều contest — xem hcmus/models.py.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        # Bám vào 0232 (migration của nhánh hcmus), KHÔNG bám 0233: 0233 là migration
        # RÁC do makemigrations tự sinh từ chỗ lệch có sẵn của repo vnoj (choices của
        # Profile.timezone lấy động từ pytz nên luôn khác snapshot). Nó không nằm
        # trong repo, nên bám vào là migration này sẽ chết ở máy khác.
        ('judge', '0232_hcmus_contest_hide_problem_statements'),
    ]

    operations = [
        migrations.CreateModel(
            name='Ranking',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, verbose_name='name')),
                ('slug', models.SlugField(help_text='Used in the URL, e.g. /xep-hang/&lt;identifier&gt;/', max_length=64, unique=True, verbose_name='identifier')),
                ('description', models.TextField(blank=True, help_text='Shown above the table. Markdown is allowed.', verbose_name='description')),
                ('visibility', models.CharField(choices=[('P', 'Public — anyone can view it in the rankings tab'), ('F', 'Featured — public, and shown on the home page'), ('H', 'Hidden — only staff can view it'), ('V', 'Private — only the creator and superusers can view it')], default='H', max_length=1, verbose_name='visibility')),
                ('absent_score', models.FloatField(default=-1, help_text='Score counted for a team that did not take part in a contest, before the weight is applied. Negative values penalise skipping a contest.', verbose_name='score when absent')),
                ('created', models.DateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', models.DateTimeField(auto_now=True, verbose_name='last modified')),
                ('creator', models.ForeignKey(blank=True, help_text='This user and superusers can edit the ranking.', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_rankings', to='judge.profile', verbose_name='creator')),
                ('teams', models.ManyToManyField(blank=True, help_text='Only these accounts appear in the table.', related_name='rankings', to='judge.profile', verbose_name='teams')),
            ],
            options={
                'verbose_name': 'team ranking',
                'verbose_name_plural': 'team rankings',
                'ordering': ['-modified'],
            },
        ),
        migrations.CreateModel(
            name='RankingContest',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('weight', models.FloatField(default=1, help_text='The contest score is multiplied by this.', verbose_name='weight')),
                ('order', models.PositiveIntegerField(db_index=True, default=0, verbose_name='order')),
                ('contest', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='judge.contest', verbose_name='contest')),
                ('ranking', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contests', to='hcmus.ranking', verbose_name='ranking')),
            ],
            options={
                'verbose_name': 'ranking contest',
                'verbose_name_plural': 'ranking contests',
                'ordering': ['order', 'id'],
                'unique_together': {('ranking', 'contest')},
            },
        ),
    ]
