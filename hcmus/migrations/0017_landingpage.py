from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [('hcmus', '0016_manage_accounts_perm')]

    operations = [
        migrations.CreateModel(
            name='LandingPage',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False,
                                        verbose_name='ID')),
                ('slug', models.SlugField(max_length=64, unique=True, verbose_name='đường dẫn',
                                          help_text='Địa chỉ trang: /&lt;đường dẫn&gt;/ — ví dụ "pretest".')),
                ('title', models.CharField(max_length=150, verbose_name='tiêu đề',
                                           help_text='Hiện trên thanh tiêu đề trình duyệt và ở liên kết.')),
                ('html', models.TextField(blank=True, verbose_name='nội dung HTML',
                                          help_text='Dán HTML vào đây, hoặc dùng ô "tải lên" bên dưới.')),
                ('is_visible', models.BooleanField(default=False, verbose_name='cho xem',
                                                   help_text='Bỏ tick là trang trả 404, không cần xoá.')),
                ('login_required', models.BooleanField(default=False,
                                                       verbose_name='phải đăng nhập mới xem')),
                ('modified', models.DateTimeField(auto_now=True, verbose_name='sửa lần cuối')),
            ],
            options={'verbose_name': 'trang giới thiệu',
                     'verbose_name_plural': 'trang giới thiệu', 'ordering': ['slug']},
        ),
    ]
