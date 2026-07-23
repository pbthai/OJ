import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        # Xem chú thích ở 0003: bám 0232 để có Profile, KHÔNG bám 0233 (migration rác
        # do choices động của Profile.timezone sinh ra, không nằm trong repo).
        ('judge', '0232_hcmus_contest_hide_problem_statements'),
        ('hcmus', '0008_sidebarsection'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserScore',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False,
                                        verbose_name='ID')),
                ('points', models.FloatField(default=0, verbose_name='points')),
                ('solved', models.PositiveIntegerField(default=0, verbose_name='problems solved')),
                ('updated', models.DateTimeField(verbose_name='last updated')),
                ('profile', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, related_name='dynamic_score',
                    to='judge.profile', verbose_name='user')),
            ],
            options={
                'verbose_name': 'user score',
                'verbose_name_plural': 'user scores',
                'ordering': ['-points', 'profile__user__username'],
            },
        ),
    ]
