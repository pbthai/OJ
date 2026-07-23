from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('hcmus', '0009_userscore'),
    ]

    operations = [
        migrations.AddField(
            model_name='userscore',
            name='rating',
            field=models.IntegerField(default=None, null=True, verbose_name='contest rating'),
        ),
        migrations.AddField(
            model_name='userscore',
            name='total',
            field=models.FloatField(default=0, verbose_name='total'),
        ),
        migrations.AlterField(
            model_name='userscore',
            name='points',
            field=models.FloatField(default=0, verbose_name='problem points'),
        ),
        migrations.AlterModelOptions(
            name='userscore',
            options={'ordering': ['-total', 'profile__user__username'],
                     'verbose_name': 'user score', 'verbose_name_plural': 'user scores'},
        ),
    ]
