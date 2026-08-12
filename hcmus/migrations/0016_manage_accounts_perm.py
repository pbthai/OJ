from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('hcmus', '0015_publicscoreboard_default_off'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='teamroom',
            options={
                'permissions': (('manage_accounts', 'Cấp tài khoản hàng loạt'),),
                'verbose_name': 'team room',
                'verbose_name_plural': 'team rooms',
            },
        ),
    ]
