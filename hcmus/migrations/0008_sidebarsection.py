from django.db import migrations, models


# Danh sách ô cột phải + thứ tự mặc định. Giữ khớp với SidebarSection.DEFAULT_ORDER
# trong models.py; ở migration thì viết thẳng chuỗi để không phụ thuộc code hiện tại.
DEFAULTS = [
    'hall_of_fame', 'calendar', 'my_tickets', 'new_tickets',
    'current_contests', 'future_contests', 'top_rating',
    'weekly_rating', 'new_problems',
]


def seed(apps, schema_editor):
    SidebarSection = apps.get_model('hcmus', 'SidebarSection')
    SidebarSection.objects.bulk_create(
        [SidebarSection(kind=k, is_visible=True, order=i) for i, k in enumerate(DEFAULTS)])


def unseed(apps, schema_editor):
    SidebarSection = apps.get_model('hcmus', 'SidebarSection')
    SidebarSection.objects.filter(kind__in=DEFAULTS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('hcmus', '0007_calendarevent'),
    ]

    operations = [
        migrations.CreateModel(
            name='SidebarSection',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False,
                                        verbose_name='ID')),
                ('kind', models.CharField(
                    choices=[
                        ('hall_of_fame', 'Hall of fame (featured team rankings)'),
                        ('calendar', 'Calendar (upcoming events)'),
                        ('my_tickets', 'My open tickets'),
                        ('new_tickets', 'New tickets (staff only)'),
                        ('current_contests', 'Ongoing contests'),
                        ('future_contests', 'Upcoming contests'),
                        ('top_rating', 'Top rating (all time)'),
                        ('weekly_rating', 'Rating gained this week'),
                        ('new_problems', 'New problems'),
                    ], max_length=32, unique=True, verbose_name='box')),
                ('is_visible', models.BooleanField(default=True, verbose_name='visible')),
                ('order', models.PositiveIntegerField(db_index=True, default=0, verbose_name='order')),
            ],
            options={
                'verbose_name': 'home sidebar box',
                'verbose_name_plural': 'home sidebar boxes',
                'ordering': ['order'],
            },
        ),
        migrations.RunPython(seed, unseed),
        # Đổi ví dụ trong help_text theo URL mới /bang-vang/ (chỉ đổi chữ hiển thị,
        # không đụng schema). Gộp vào đây để trạng thái migration khỏi lệch.
        migrations.AlterField(
            model_name='ranking',
            name='slug',
            field=models.SlugField(
                help_text='Used in the URL, e.g. /bang-vang/&lt;identifier&gt;/',
                max_length=64, unique=True, verbose_name='identifier'),
        ),
    ]
