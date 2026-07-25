"""Trang admin cho bảng xếp hạng team.

Bám khuôn mẫu sẵn có của repo: inline kiểu ContestProblemInline
(judge/admin/contest.py:64) và cặp get_queryset + has_change_permission kiểu
BlogPostAdmin (judge/admin/interface.py:94).
"""
import re

from adminsortable2.admin import SortableAdminMixin
from django import forms
from django.contrib import admin, messages
from django.contrib.admin.widgets import FilteredSelectMultiple
from django.db.models import Max, Q
from django.forms import ModelForm
from django.urls import reverse
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _, ngettext

from hcmus.models import (CalendarEvent, HomeSection, JudgeSwitch, PermSet, Printer,
                          PrintRequest, Ranking, RankingContest, SidebarSection, TeamRoom,
                          UserScore)
from judge.models import Profile
from judge.widgets import (AdminHeavySelect2MultipleWidget, AdminHeavySelect2Widget,
                           AdminMartorWidget)

# Tách theo khoảng trắng, xuống dòng, phẩy, chấm phẩy, tab — dán từ Excel hay từ
# email đều vào được mà không phải sửa tay.
SPLIT_RE = re.compile(r'[\s,;]+')
MAX_BULK_FILE = 256 * 1024


def parse_usernames(text):
    """Chuỗi bất kỳ -> danh sách username, giữ thứ tự, bỏ trùng."""
    seen, out = set(), []
    for tok in SPLIT_RE.split(text or ''):
        tok = tok.strip()
        if tok and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


class RankingContestInlineForm(ModelForm):
    class Meta:
        widgets = {
            'contest': AdminHeavySelect2Widget(data_view='contest_select2'),
            'setters': AdminHeavySelect2MultipleWidget(data_view='profile_select2'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'setters' in self.fields:
            self.fields['setters'].queryset = Profile.objects.select_related('user')
            self.fields['setters'].widget.can_add_related = False


class RankingContestInline(admin.TabularInline):
    model = RankingContest
    form = RankingContestInlineForm
    fields = ('contest', 'weight', 'setters', 'order')
    verbose_name = _('contest')
    verbose_name_plural = _('contests used for this ranking')
    extra = 1


class RankingForm(ModelForm):
    bulk_teams = forms.CharField(
        required=False, label=_('Add teams in bulk'),
        widget=forms.Textarea(attrs={'rows': 4, 'cols': 60,
                                     'placeholder': 'team01 team02 team03\nteam04, team05'}),
        help_text=_('Paste usernames separated by spaces, commas or new lines. They are added to '
                    'the list above; nothing is removed. Unknown names are reported as an error.'))
    bulk_file = forms.FileField(
        required=False, label=_('…or upload a text file'),
        help_text=_('A plain text file with the same format. Max 256 KB.'))

    class Meta:
        widgets = {
            'teams': AdminHeavySelect2MultipleWidget(data_view='profile_select2'),
            'creator': AdminHeavySelect2Widget(data_view='profile_select2'),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Hai dòng lấy từ ContestForm của repo (judge/admin/contest.py:117-127):
        # select_related tránh N+1 khi render các lựa chọn đã chọn, và tắt nút '+'
        # vì tạo Profile mới từ đây là vô nghĩa.
        if 'teams' in self.fields:
            self.fields['teams'].queryset = Profile.objects.select_related('user')
            self.fields['teams'].widget.can_add_related = False
        if 'creator' in self.fields:
            self.fields['creator'].widget.can_add_related = False

    def clean(self):
        cleaned = super().clean()
        text = cleaned.get('bulk_teams') or ''

        upload = cleaned.get('bulk_file')
        if upload:
            if upload.size > MAX_BULK_FILE:
                raise forms.ValidationError(_('The uploaded file is larger than 256 KB.'))
            raw = upload.read()
            try:
                text += '\n' + raw.decode('utf-8')
            except UnicodeDecodeError:
                # File xuất từ Excel trên Windows hay là cp1252, đừng bắt người dùng
                # tự đi chuyển mã.
                text += '\n' + raw.decode('latin-1')

        names = parse_usernames(text)
        if not names:
            return cleaned

        found = {p.user.username: p for p in
                 Profile.objects.filter(user__username__in=names).select_related('user')}
        missing = [n for n in names if n not in found]
        if missing:
            raise forms.ValidationError(
                _('No account named: %(names)s') % {'names': ', '.join(missing[:20])})

        # Gộp vào lựa chọn hiện có thay vì thay thế — người dùng dán thêm đợt sau
        # thì không mất đợt trước.
        current = list(cleaned.get('teams') or [])
        cleaned['teams'] = current + [p for p in found.values() if p not in current]
        return cleaned


@admin.register(Ranking)
class RankingAdmin(admin.ModelAdmin):
    form = RankingForm
    inlines = [RankingContestInline]
    prepopulated_fields = {'slug': ('name',)}
    list_display = ('name', 'slug', 'visibility', 'creator', 'team_count', 'contest_count', 'modified')
    list_filter = ('visibility',)
    search_fields = ('name', 'slug')
    fieldsets = (
        (None, {'fields': ('name', 'slug', 'description')}),
        (_('Visibility'), {'fields': ('visibility', 'creator')}),
        (_('Teams'), {'fields': ('teams', 'bulk_teams', 'bulk_file')}),
        (_('Scoring'), {'fields': ('absent_score', 'hall_of_fame')}),
    )

    @admin.display(description=_('teams'))
    def team_count(self, obj):
        return obj.teams.count()

    @admin.display(description=_('contests'))
    def contest_count(self, obj):
        return obj.contests.count()

    def get_queryset(self, request):
        qs = super().get_queryset(request).select_related('creator__user')
        # Không phải superuser thì chỉ thấy bảng mình tạo, cộng bảng công khai/ẩn.
        # Bảng riêng tư của người khác không hiện ra ngay cả trong danh sách.
        # Dùng Q chứ không dùng .union(): queryset hợp không lọc/sắp tiếp được,
        # mà admin còn phải áp search_fields và list_filter lên trên.
        if request.user.is_superuser:
            return qs
        return qs.filter(~Q(visibility=Ranking.PRIVATE) | Q(creator=request.user.profile))

    # obj=None là lúc Django hỏi "có được mở trang danh sách không". Trước đây
    # trả về is_staff, nghĩa là MỌI staff mở được bảng xếp hạng dù không thuộc
    # nhóm quan-ly-xep-hang. Nay hỏi đúng quyền; obj cụ thể thì vẫn theo người tạo.
    def has_change_permission(self, request, obj=None):
        if obj is None:
            return request.user.has_perm('hcmus.change_ranking')
        return obj.is_editable_by(request.user)

    def has_delete_permission(self, request, obj=None):
        if obj is None:
            return request.user.has_perm('hcmus.delete_ranking')
        return obj.is_editable_by(request.user)

    def save_model(self, request, obj, form, change):
        # Gán người tạo ở lần lưu đầu, nếu chưa chọn ai. Không có bước này thì
        # creator rỗng và về sau không ai ngoài superuser sửa được bảng.
        if not change and obj.creator_id is None:
            obj.creator = request.user.profile
        super().save_model(request, obj, form, change)


class HomeSectionForm(ModelForm):
    class Meta:
        widgets = {
            'contest': AdminHeavySelect2Widget(data_view='contest_select2'),
            'content': AdminMartorWidget,
        }


@admin.register(HomeSection)
class HomeSectionAdmin(SortableAdminMixin, admin.ModelAdmin):
    """Quản lý nội dung trang chủ. CHỈ root admin (superuser).

    SortableAdminMixin cho kéo thả sắp thứ tự ngay trong trang danh sách. Ba ràng
    buộc của nó, sai cái nào cũng hỏng:
      - Meta.ordering của model phải có field số nguyên (ở đây là 'order'), và
        khai lại ordering ở đây cho chắc; thiếu là ImproperlyConfigured lúc khởi
        động, tức sập cả trang admin chứ không phải lỗi runtime.
      - KHÔNG đưa 'order' vào fieldsets: mixin chỉ gỡ được field đó khi fieldsets
        là None, khai tường minh thì nó lòi ra form và đè giá trị mixin tự gán.
      - has_change_permission phải giữ obj=None mặc định: endpoint kéo thả gọi
        self.has_change_permission(request) với đúng một tham số.
    """
    form = HomeSectionForm
    ordering = ('order',)
    list_display = ('display_title', 'kind', 'is_visible')
    list_filter = ('kind', 'is_visible')
    fieldsets = (
        (None, {'fields': ('kind', 'title', 'is_visible')}),
        (_('Target'), {'fields': ('post', 'ranking', 'contest'),
                       'description': _('Fill in the one matching the kind above.')}),
        (_('Custom content'), {'fields': ('content',)}),
        (_('Display'), {'fields': ('limit',)}),
    )

    # Trước đây khoá cứng superuser. Nay kiểm theo quyền để giao được cho một nhóm
    # riêng (xem model PermSet ở cuối models.py). Superuser vẫn qua hết vì Django
    # cho superuser mọi quyền.
    #
    # has_change_permission BẮT BUỘC giữ obj=None mặc định: endpoint kéo thả của
    # adminsortable2 gọi self.has_change_permission(request) với đúng một tham số,
    # thiếu default là kéo thả nổ TypeError 500.

    def _may(self, request, action):
        return (request.user.is_active and
                request.user.has_perm(f'hcmus.{action}_homesection'))

    def has_module_permission(self, request):
        return any(self._may(request, a) for a in ('view', 'add', 'change', 'delete'))

    def has_view_permission(self, request, obj=None):
        return self._may(request, 'view') or self._may(request, 'change')

    def has_add_permission(self, request):
        return self._may(request, 'add')

    def has_change_permission(self, request, obj=None):
        return self._may(request, 'change')

    def has_delete_permission(self, request, obj=None):
        return self._may(request, 'delete')

    actions = ['import_homepage_posts']

    # -- nạp các bài blog đang hiện trên trang chủ --------------------------
    #
    # Bài blog lên trang chủ qua luồng riêng của vnoj (global_post=True) nên KHÔNG
    # tự xuất hiện ở đây, và người quản trị không có cách nào tắt chúng từ trang
    # này. Đây là cầu nối.
    #
    # Làm cả hai đường vào: một bulk action, VÀ một nút riêng. Chỉ có action là
    # chưa đủ — Django ẩn thanh action khi danh sách rỗng, đúng lúc cần nó nhất.

    @staticmethod
    def unmanaged_homepage_posts():
        from judge.models import BlogPost
        return BlogPost.objects.filter(visible=True, global_post=True) \
                               .exclude(id__in=HomeSection.managed_post_ids())

    @classmethod
    def _do_import(cls):
        posts = list(cls.unmanaged_homepage_posts())
        base = (HomeSection.objects.aggregate(m=Max('order'))['m'] or 0) + 1
        HomeSection.objects.bulk_create(
            [HomeSection(kind=HomeSection.POST, post=p, order=base + i)
             for i, p in enumerate(posts)])
        # Kèm một khối "dòng bài blog" để các bài về sau vẫn có chỗ hiện, và để
        # người quản trị kéo được luồng bài lên/xuống so với các khối khác.
        if not HomeSection.objects.filter(kind=HomeSection.FEED).exists():
            HomeSection.objects.create(kind=HomeSection.FEED, title=str(_('Blog feed')),
                                       order=base + len(posts))
        return len(posts)

    def get_urls(self):
        from django.urls import path
        return [path('import-posts/', self.admin_site.admin_view(self.import_posts_view),
                     name='hcmus_homesection_import')] + super().get_urls()

    def import_posts_view(self, request):
        from django.http import HttpResponseRedirect
        from django.urls import reverse
        if not self._may(request, 'add'):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied()
        n = self._do_import()
        self.message_user(request, _('Added %(n)d post(s). Untick "visible" on a section to hide '
                                     'it from the home page.') % {'n': n})
        return HttpResponseRedirect(reverse('admin:hcmus_homesection_changelist'))

    def changelist_view(self, request, extra_context=None):
        # Lời nhắc kèm link, luôn hiện — kể cả khi danh sách rỗng.
        missing = self.unmanaged_homepage_posts().count()
        if missing:
            from django.urls import reverse
            self.message_user(request, format_html(
                '{} <a href="{}"><b>{}</b></a>',
                ngettext('%d blog post is showing on the home page but is not managed here.',
                         '%d blog posts are showing on the home page but are not managed here.',
                         missing) % missing,
                reverse('admin:hcmus_homesection_import'),
                _('Add them to this list')), messages.INFO)
        return super().changelist_view(request, extra_context)

    @admin.action(description=_('Add the blog posts currently on the home page'))
    def import_homepage_posts(self, request, queryset):
        n = self._do_import()
        self.message_user(request, _('Added %(n)d post(s). Untick "visible" on a section to hide '
                                     'it from the home page.') % {'n': n})


@admin.register(SidebarSection)
class SidebarSectionAdmin(SortableAdminMixin, admin.ModelAdmin):
    """Kéo thả sắp thứ tự cột phải trang chủ, bật/tắt từng ô.

    Cùng ràng buộc SortableAdminMixin như HomeSectionAdmin (xem chú thích ở đó):
    model có Meta.ordering số nguyên, không đưa 'order' vào form, has_change_permission
    giữ obj=None mặc định. Gác bằng chính quyền quản lý trang chủ (*_homesection) —
    ai sắp được cột chính thì sắp luôn cột phải, khỏi phải cấu hình thêm vai trò.

    Không cho thêm/xoá: danh sách ô là cố định theo code, người dùng chỉ sắp lại và
    bật/tắt. Thêm tay một ô lạ (kind không có template) sẽ vỡ khi render.
    """
    ordering = ('order',)
    list_display = ('__str__', 'kind', 'is_visible')
    list_filter = ('is_visible',)

    def _may(self, request, action):
        return (request.user.is_active and
                request.user.has_perm(f'hcmus.{action}_homesection'))

    def has_module_permission(self, request):
        return any(self._may(request, a) for a in ('view', 'change'))

    def has_view_permission(self, request, obj=None):
        return self._may(request, 'view') or self._may(request, 'change')

    def has_change_permission(self, request, obj=None):
        return self._may(request, 'change')

    def has_add_permission(self, request):
        return False        # danh sách ô cố định theo code, chỉ sắp lại + bật/tắt

    def has_delete_permission(self, request, obj=None):
        return False


class PermSetForm(ModelForm):
    class Meta:
        widgets = {
            'permissions': FilteredSelectMultiple(_('permissions'), False),
            'includes': FilteredSelectMultiple(_('sets'), False),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'permissions' in self.fields:
            # Nhãn 'app.codename | Mô tả' cho dễ tìm, giống cách DMOJ làm ở
            # UserAdmin.formfield_for_manytomany (judge/admin/profile.py:168).
            f = self.fields['permissions']
            f.queryset = f.queryset.select_related('content_type').order_by(
                'content_type__app_label', 'content_type__model', 'codename')
            f.label_from_instance = lambda o: f'{o.content_type.app_label}.{o.codename}  |  {o.name}'
        if 'includes' in self.fields and self.instance.pk:
            # Không cho tự chứa chính mình
            self.fields['includes'].queryset = \
                self.fields['includes'].queryset.exclude(pk=self.instance.pk)

    def clean(self):
        cleaned = super().clean()
        includes = cleaned.get('includes')
        if includes and self.instance.pk:
            # Dò vòng TRƯỚC khi lưu: lưu xong mới phát hiện thì signal materialize
            # sẽ đệ quy vô hạn.
            def walk(node, seen):
                if node.pk in seen:
                    return True
                for ch in node.includes.all():
                    if ch.pk == self.instance.pk or walk(ch, seen | {node.pk}):
                        return True
                return False
            for s in includes:
                if s.pk == self.instance.pk or walk(s, {self.instance.pk}):
                    raise forms.ValidationError(
                        _('"%(name)s" would create a cycle: it already contains this set '
                          '(directly or through another set).') % {'name': s.name})
        return cleaned


@admin.register(PermSet)
class PermSetAdmin(SortableAdminMixin, admin.ModelAdmin):
    """Sửa đại số phân quyền ngay trên web.

    CHỈ superuser. Ai sửa được tập quyền thì tự cấp cho mình bất kỳ quyền nào,
    nên đây không phải thứ giao qua nhóm được.

    Mỗi lần lưu, signal ở models.py nở lại TOÀN BỘ vai trò rồi ghi vào Django
    Group. Không có nút "áp dụng" vì dễ quên bấm; sửa xong là có hiệu lực ngay.
    """
    form = PermSetForm
    ordering = ('order',)
    list_display = ('name', 'label', 'is_role', 'so_quyen', 'gom_tap', 'note_ngan')
    list_filter = ('is_role',)
    search_fields = ('name', 'label')
    fieldsets = (
        (None, {'fields': ('name', 'label', 'is_role', 'note')}),
        (_('Contents'), {'fields': ('includes', 'permissions')}),
    )

    @admin.display(description=_('resolved'))
    def so_quyen(self, obj):
        n = obj.resolved_count()
        return _('cycle!') if n < 0 else n

    # Số quyền lẻ hiện tối đa trên một dòng; quá thì rút gọn kèm số còn lại.
    # soan-bai có 33 quyền lẻ, liệt kê hết thì bảng không đọc được.
    MAX_LEAF = 8

    @admin.display(description=_('includes'))
    def gom_tap(self, obj):
        """Hiện ĐÚNG MỘT CẤP con trực tiếp: các tập con trước, rồi tới quyền lẻ.

        Trước đây chỉ hiện tập con, nên khối nguyên tử (chỉ có quyền lẻ, không có
        tập con) hiện dấu '—' như thể rỗng — nhìn vào tưởng chưa cấu hình gì.
        """
        parts = []
        for child in obj.includes.all():
            parts.append(format_html(
                '<a href="{}" title="{}"><b>{}</b></a>',
                reverse('admin:hcmus_permset_change', args=[child.pk]),
                _('nested set'), child.name))

        perms = sorted(f'{p.content_type.app_label}.{p.codename}'
                       for p in obj.permissions.all())
        for code in perms[:self.MAX_LEAF]:
            parts.append(format_html('<span style="color:#666">{}</span>', code))
        if len(perms) > self.MAX_LEAF:
            parts.append(format_html('<i style="color:#999">+{} {}</i>',
                                     len(perms) - self.MAX_LEAF, _('more')))
        return mark_safe(', '.join(parts)) if parts else '—'

    def get_queryset(self, request):
        # Không prefetch thì mỗi dòng thêm 2 truy vấn cho includes/permissions.
        return (super().get_queryset(request)
                .prefetch_related('includes', 'permissions__content_type'))

    def changelist_view(self, request, extra_context=None):
        # Tính sẵn toàn bộ phép nở một lần cho cả trang, xem PermSet.batch_resolve.
        #
        # PHẢI ép render NGAY trong khối: changelist_view trả về TemplateResponse
        # chưa render, template chỉ chạy ở tầng middleware phía sau — lúc đó khối
        # with đã thoát và bản đồ đã bị xoá, nên không những vô tác dụng mà còn
        # tốn thêm truy vấn. Đã đo thấy 90 -> 93 truy vấn trước khi sửa.
        with PermSet.batch_resolve():
            resp = super().changelist_view(request, extra_context)
            if hasattr(resp, 'render') and not resp.is_rendered:
                resp.render()
            return resp

    @admin.display(description=_('note'))
    def note_ngan(self, obj):
        return (obj.note[:60] + '…') if len(obj.note) > 60 else obj.note

    def _root(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_module_permission(self, request):
        return self._root(request)

    def has_view_permission(self, request, obj=None):
        return self._root(request)

    def has_add_permission(self, request):
        return self._root(request)

    def has_change_permission(self, request, obj=None):
        return self._root(request)

    def has_delete_permission(self, request, obj=None):
        return self._root(request)


@admin.register(UserScore)
class UserScoreAdmin(admin.ModelAdmin):
    """Bảng xếp hạng — CHỈ XEM. total = contest rating + điểm giải bài. Số liệu do
    `hcmus_recompute_scores` tính (cron 3h sáng), không sửa tay. Có nút chạy lại
    ngay để khỏi đợi cron."""
    list_display = ('username', 'total', 'rating', 'points', 'solved', 'updated')
    search_fields = ('profile__user__username',)
    ordering = ('-total',)

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('profile__user')

    @admin.display(description=_('user'), ordering='profile__user__username')
    def username(self, obj):
        return obj.profile.user.username

    # Chỉ xem: không thêm/sửa/xoá tay (số liệu là kết quả tính ra).
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    # Nút "tính lại ngay" theo đúng khuôn nút import của HomeSection: thêm URL riêng
    # + lời nhắc kèm link ở đầu trang danh sách (bulk action không dùng được vì
    # trang chỉ-xem không có ô chọn dòng).
    def get_urls(self):
        from django.urls import path
        return [path('recompute/', self.admin_site.admin_view(self.recompute_view),
                     name='hcmus_userscore_recompute')] + super().get_urls()

    def recompute_view(self, request):
        from django.http import HttpResponseRedirect
        from django.urls import reverse

        from hcmus.scoring import recompute_user_scores
        n = recompute_user_scores()
        self.message_user(request, _('Recomputed scores for %(n)d users.') % {'n': n})
        return HttpResponseRedirect(reverse('admin:hcmus_userscore_changelist'))

    def changelist_view(self, request, extra_context=None):
        from django.urls import reverse
        self.message_user(request, format_html(
            '{} <a href="{}"><b>{}</b></a>',
            _('Scores refresh nightly at 3am.'),
            reverse('admin:hcmus_userscore_recompute'),
            _('Recompute now')), messages.INFO)
        return super().changelist_view(request, extra_context)


@admin.register(Printer)
class PrinterAdmin(admin.ModelAdmin):
    """Cấu hình + chọn máy in để in bài trong giờ thi. `cups_dest` là tên hàng đợi
    CUPS trên server (tạo một lần bằng `lpadmin`, xem docs/06 §2.8). Nút 'In thử'
    để kiểm tra server có tới được máy in không."""
    list_display = ('name', 'cups_dest', 'is_active', 'note')
    list_editable = ('is_active',)
    actions = ['test_print']

    @admin.action(description=_('In thử một trang tới máy in đã chọn'))
    def test_print(self, request, queryset):
        from hcmus import printing
        sample = ('# FIT-HCMUS Online Judge — in thử\n'
                  'print("Xin chào — kiểm tra máy in và tiếng Việt: ăâđêôơư")\n')
        for p in queryset:
            pdf, _pages = printing.render_source_pdf(
                sample, 'Python', 'python', 'IN THỬ', 'P.TEST', 'Test')
            ok, msg = printing.send_to_printer(pdf, p.cups_dest, 'test-print')
            self.message_user(request, f'{p.name}: {"OK" if ok else "LỖI"} — {msg}',
                              messages.SUCCESS if ok else messages.ERROR)


@admin.register(PrintRequest)
class PrintRequestAdmin(admin.ModelAdmin):
    """Hàng đợi + log in bài (CHỈ XEM). In tự động nên đây để giám thị theo dõi và
    soát; muốn in lại thì thí sinh tự bấm 'In bài' lần nữa."""
    list_display = ('created', 'team', 'room', 'problem', 'language', 'pages', 'status',
                    'printer', 'error')
    list_filter = ('status', 'contest')
    search_fields = ('team', 'room', 'problem')
    date_hierarchy = 'created'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(TeamRoom)
class TeamRoomAdmin(admin.ModelAdmin):
    """Phòng thi của từng đội (in lên header phiếu in). Thường nạp tự động từ cột
    room của công cụ cấp tài khoản; sửa tay ở đây khi cần."""
    list_display = ('profile', 'room', 'updated')
    search_fields = ('profile__user__username', 'room')
    raw_id_fields = ('profile',)


@admin.register(JudgeSwitch)
class JudgeSwitchAdmin(admin.ModelAdmin):
    """Bật/tắt máy chấm. Thao tác chính nằm ở trang Sức khoẻ hệ thống; đây là chỗ
    xem lại lịch sử và sửa ghi chú."""
    list_display = ('name', 'enabled', 'note', 'changed_by', 'modified')
    list_filter = ('enabled',)
    readonly_fields = ('changed_by', 'modified')

    def _may(self, request):
        return request.user.is_active and request.user.has_perm('hcmus.control_judges')

    def has_module_permission(self, request):
        return self._may(request)

    def has_view_permission(self, request, obj=None):
        return self._may(request)

    def has_change_permission(self, request, obj=None):
        return self._may(request)

    def has_add_permission(self, request):
        return False        # bản ghi tự sinh từ container, không thêm tay

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CalendarEvent)
class CalendarEventAdmin(admin.ModelAdmin):
    """Sự kiện lịch nhập tay. Gác bằng chính quyền model mặc định
    (add/change/delete/view_calendarevent) — chúng gộp thành khối 'sua-lich',
    con của 'dang-trang-chu' trong cây phân quyền, nên ai được giao quản lý trang
    chủ thì thêm sự kiện được, không phải cấu hình gì thêm."""
    list_display = ('title', 'category', 'visibility', 'start_time', 'end_time', 'created_by')
    list_filter = ('visibility', 'category', 'all_day')
    search_fields = ('title', 'description', 'location')
    date_hierarchy = 'start_time'
    filter_horizontal = ('organizations',)
    readonly_fields = ('created_by', 'created', 'modified')
    fieldsets = (
        (None, {'fields': ('title', 'description', 'location', 'url', 'category')}),
        (_('Time'), {'fields': ('start_time', 'end_time', 'all_day')}),
        (_('Who can see it'), {
            'fields': ('visibility', 'organizations'),
            'description': _('"Class" shows the event only to members of the chosen classes. '
                             '"Internal" needs the "view internal calendar" permission.')}),
        (_('Meta'), {'fields': ('created_by', 'created', 'modified')}),
    )

    def save_model(self, request, obj, form, change):
        # Ghi lại người tạo, chỉ đặt một lần. Không để trống rồi phải nhập tay.
        if not obj.created_by_id and getattr(request, 'profile', None) is not None:
            obj.created_by = request.profile
        super().save_model(request, obj, form, change)
