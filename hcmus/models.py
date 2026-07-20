"""Bảng xếp hạng team tổng hợp từ nhiều contest (FIT-HCMUS).

Admin tạo một bảng, chọn các contest dùng để xếp + trọng số từng contest, chọn
các team được xếp. Điểm tổng = Σ (điểm contest × trọng số); team không dự một
contest thì tính (-1 × trọng số); hoà điểm thì tổng penalty thấp hơn xếp trên.

"Team" ở đây là Profile (tài khoản đội team01, team02...) — hệ thống không có
model Team riêng, ContestParticipation gắn thẳng vào Profile.
"""
import contextlib
import threading

from django.db import models
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from judge.models import Contest, Profile

_local = threading.local()


class Ranking(models.Model):
    PUBLIC = 'P'
    HIDDEN = 'H'
    PRIVATE = 'V'
    FEATURED = 'F'
    VISIBILITY = (
        (PUBLIC, _('Public — anyone can view it in the rankings tab')),
        (FEATURED, _('Featured — public, and shown on the home page')),
        (HIDDEN, _('Hidden — only staff can view it')),
        (PRIVATE, _('Private — only the creator and superusers can view it')),
    )

    name = models.CharField(max_length=100, verbose_name=_('name'))
    slug = models.SlugField(max_length=64, unique=True, verbose_name=_('identifier'),
                            help_text=_('Used in the URL, e.g. /xep-hang/&lt;identifier&gt;/'))
    description = models.TextField(blank=True, verbose_name=_('description'),
                                   help_text=_('Shown above the table. Markdown is allowed.'))
    visibility = models.CharField(max_length=1, choices=VISIBILITY, default=HIDDEN,
                                  verbose_name=_('visibility'))
    creator = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='created_rankings', verbose_name=_('creator'),
                                help_text=_('This user and superusers can edit the ranking.'))
    teams = models.ManyToManyField(Profile, blank=True, related_name='rankings',
                                   verbose_name=_('teams'),
                                   help_text=_('Only these accounts appear in the table.'))
    hall_of_fame = models.PositiveIntegerField(
        default=0, verbose_name=_('hall of fame size'),
        help_text=_('Number of top teams highlighted in the table. The first three also get a '
                    'gold, silver and bronze medal. Set to 0 to turn this off.'))
    absent_score = models.FloatField(
        default=-1, verbose_name=_('score when absent'),
        help_text=_('Score counted for a team that did not take part in a contest, before the weight '
                    'is applied. Negative values penalise skipping a contest.'))
    created = models.DateTimeField(auto_now_add=True, verbose_name=_('created'))
    modified = models.DateTimeField(auto_now=True, verbose_name=_('last modified'))

    class Meta:
        verbose_name = _('team ranking')
        verbose_name_plural = _('team rankings')
        ordering = ['-modified']

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('hcmus_ranking_detail', args=[self.slug])

    # -- quyền ------------------------------------------------------------

    def is_editable_by(self, user):
        """Người tạo hoặc superuser. is_staff KHÔNG đủ — nhiều giảng viên dùng
        chung site, bảng của người này người kia không nên sửa được."""
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        return self.creator_id is not None and self.creator_id == user.profile.id

    def is_accessible_by(self, user):
        if self.visibility in (self.PUBLIC, self.FEATURED):
            return True
        if not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        if self.visibility == self.HIDDEN:
            return user.is_staff
        # PRIVATE
        return self.creator_id is not None and self.creator_id == user.profile.id

    @classmethod
    def visible_to(cls, user):
        """Queryset các bảng mà `user` được xem. Viết bằng Q để lọc ngay trong DB,
        không kéo hết về rồi lọc trong Python."""
        from django.db.models import Q
        public = Q(visibility__in=(cls.PUBLIC, cls.FEATURED))
        if not user.is_authenticated:
            return cls.objects.filter(public)
        if user.is_superuser:
            return cls.objects.all()
        cond = public | Q(creator=user.profile)
        if user.is_staff:
            cond |= Q(visibility=cls.HIDDEN)
        return cls.objects.filter(cond).distinct()

    @property
    def mixed_penalty_units(self):
        """True nếu các contest trong bảng dùng format khác nhau.

        Quan trọng vì cumtime KHÔNG cùng đơn vị giữa các format: ICPC tính bằng
        PHÚT (judge/contest_format/icpc.py:70), format default tính bằng GIÂY.
        Cộng thẳng qua nhiều format là cộng nhầm đơn vị, nên phải cảnh báo.
        """
        formats = {rc.contest.format_name for rc in self.contests.select_related('contest')}
        return len(formats) > 1


class RankingContest(models.Model):
    ranking = models.ForeignKey(Ranking, on_delete=models.CASCADE, related_name='contests',
                                verbose_name=_('ranking'))
    contest = models.ForeignKey(Contest, on_delete=models.CASCADE, related_name='+',
                                verbose_name=_('contest'))
    weight = models.FloatField(default=1, verbose_name=_('weight'),
                               help_text=_('The contest score is multiplied by this.'))
    setters = models.ManyToManyField(
        Profile, blank=True, related_name='+', verbose_name=_('problem setters'),
        help_text=_('Teams that set this contest. They receive the maximum score and zero penalty '
                    'for it without competing.'))
    order = models.PositiveIntegerField(default=0, db_index=True, verbose_name=_('order'))

    class Meta:
        verbose_name = _('ranking contest')
        verbose_name_plural = _('ranking contests')
        unique_together = ('ranking', 'contest')
        ordering = ['order', 'id']

    def __str__(self):
        return f'{self.contest.name} (×{self.weight:g})'

    @property
    def max_score(self):
        """Điểm tối đa có thể đạt của contest = tổng points các bài.

        Với ICPC (points=1 mỗi bài) thì bằng số bài. Dùng điểm tối đa CÓ THỂ ĐẠT
        chứ không dùng điểm của người đứng đầu: team ra đề thì không ai vượt được,
        và con số này không đổi khi có người nộp thêm bài.
        """
        total = self.contest.contest_problems.aggregate(models.Sum('points'))['points__sum']
        return float(total or 0)


class HomeSection(models.Model):
    """Một khối nội dung do root admin ghim lên cột chính của trang chủ.

    Mỗi khối trỏ tới một đối tượng có sẵn (bài blog / bảng xếp hạng / kỳ thi) hoặc
    tự chứa nội dung markdown. Dùng nhiều FK nullable + `kind` thay vì
    GenericForeignKey: Django admin không có widget cho GFK, người nhập phải tự gõ
    số ID vào ô text và không có gì kiểm tra đối tượng có tồn tại hay không.
    """
    POST = 'post'
    RANKING = 'ranking'
    CONTEST = 'contest'
    CUSTOM = 'custom'
    FEED = 'feed'
    KINDS = (
        (POST, _('Blog post')),
        (RANKING, _('Team ranking')),
        (CONTEST, _('Contest')),
        (CUSTOM, _('Custom content')),
        (FEED, _('Blog feed (the usual list of posts)')),
    )

    kind = models.CharField(max_length=16, choices=KINDS, default=CUSTOM, verbose_name=_('kind'))
    title = models.CharField(max_length=150, blank=True, verbose_name=_('title'),
                             help_text=_('Leave empty to use the name of the linked object.'))
    post = models.ForeignKey('judge.BlogPost', on_delete=models.CASCADE, null=True, blank=True,
                             related_name='+', verbose_name=_('blog post'))
    ranking = models.ForeignKey('hcmus.Ranking', on_delete=models.CASCADE, null=True, blank=True,
                                related_name='+', verbose_name=_('team ranking'))
    contest = models.ForeignKey('judge.Contest', on_delete=models.CASCADE, null=True, blank=True,
                                related_name='+', verbose_name=_('contest'))
    content = models.TextField(blank=True, verbose_name=_('content'),
                               help_text=_('Markdown, for the "Custom content" kind.'))
    limit = models.PositiveIntegerField(
        default=10, verbose_name=_('rows shown'),
        help_text=_('For a ranking: how many teams to show. 0 shows every team.'))
    is_visible = models.BooleanField(default=True, verbose_name=_('visible'))
    # SortableAdminMixin đọc tên field này từ Meta.ordering[0] để kéo thả sắp thứ tự.
    order = models.PositiveIntegerField(default=0, db_index=True, verbose_name=_('order'))

    class Meta:
        verbose_name = _('home page section')
        verbose_name_plural = _('home page sections')
        ordering = ['order']   # BẮT BUỘC: thiếu là SortableAdminMixin ném ImproperlyConfigured

    def __str__(self):
        return self.display_title or self.get_kind_display()

    @property
    def display_title(self):
        if self.title:
            return self.title
        obj = self.target
        if obj is None:
            return ''
        return getattr(obj, 'title', None) or getattr(obj, 'name', '')

    @property
    def target(self):
        return {self.POST: self.post, self.RANKING: self.ranking,
                self.CONTEST: self.contest}.get(self.kind)

    def clean(self):
        from django.core.exceptions import ValidationError
        if self.kind == self.FEED:
            return
        need = {self.POST: 'post', self.RANKING: 'ranking', self.CONTEST: 'contest'}.get(self.kind)
        if need and getattr(self, need + '_id') is None:
            raise ValidationError({need: _('Required for this kind of section.')})
        if self.kind == self.CUSTOM and not self.content.strip():
            raise ValidationError({'content': _('Required for this kind of section.')})

    def is_visible_to(self, user):
        """Ghim rồi vẫn phải tôn trọng quyền xem của đối tượng gốc: ghim nhầm một
        bảng xếp hạng riêng tư hay một kỳ thi chưa công bố thì không được lộ ra."""
        if not self.is_visible:
            return False
        if self.kind == self.FEED:
            return True
        if self.kind == self.RANKING:
            return self.ranking is not None and self.ranking.is_accessible_by(user)
        if self.kind == self.POST:
            return self.post is not None and self.post.visible
        if self.kind == self.CONTEST:
            if self.contest is None:
                return False
            from judge.models import Contest
            return Contest.get_visible_contests(user).filter(id=self.contest_id).exists()
        return True

    @classmethod
    def for_user(cls, user):
        qs = (cls.objects.filter(is_visible=True)
              .select_related('post', 'ranking', 'contest'))
        return [s for s in qs if s.is_visible_to(user)]

    @classmethod
    def managed_post_ids(cls):
        """Id các bài blog ĐÃ có khối quản lý, kể cả khối đang tắt.

        Kể cả khối tắt là có chủ ý: luồng blog của vnoj vẫn hiện bài đó, nên nếu chỉ
        loại khối đang bật thì tắt khối lại làm bài hiện trở lại ở dưới — ngược hẳn
        ý người dùng. Ở đây tắt khối = bài biến mất khỏi trang chủ."""
        return list(cls.objects.filter(kind=cls.POST, post__isnull=False)
                    .values_list('post_id', flat=True))

    @classmethod
    def feed_is_managed(cls):
        """Có khối 'dòng bài blog' không. Nếu có thì template của hcmus lo việc
        render luồng bài (đúng vị trí đã sắp), lõi phải nhường."""
        return cls.objects.filter(kind=cls.FEED).exists()


# ---------------------------------------------------------------------------
# Sửa contest/trọng số/team ra đề thì phải làm mới cache của bảng xếp hạng.
# compute_cached() khoá theo Ranking.modified, mà RankingContest là model KHÁC nên
# sửa nó không đụng tới modified — thiếu signal này thì trang chủ giữ số cũ tới 5
# phút và người dùng tưởng thao tác của mình không ăn.
# ---------------------------------------------------------------------------


def _touch_ranking(ranking_id):
    if ranking_id:
        Ranking.objects.filter(id=ranking_id).update(modified=timezone.now())


@receiver([post_save, post_delete], sender=RankingContest)
def _rc_changed(sender, instance, **kwargs):
    _touch_ranking(instance.ranking_id)


@receiver(m2m_changed, sender=RankingContest.setters.through)
def _rc_setters_changed(sender, instance, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        _touch_ranking(instance.ranking_id)


@receiver(m2m_changed, sender=Ranking.teams.through)
def _teams_changed(sender, instance, action, **kwargs):
    if action in ('post_add', 'post_remove', 'post_clear'):
        _touch_ranking(instance.id)


class PermSet(models.Model):
    """Tập quyền, lồng nhau được — đại số tập hợp cho phân quyền.

    Django Group không có quan hệ cha con, chỉ có một danh sách quyền phẳng. Model
    này bù chỗ đó: một tập gồm các quyền lẻ CỘNG các tập khác (`includes`). Khi lưu,
    hệ thống "nở" ra rồi ghi kết quả phẳng vào Django Group tương ứng, nên phần còn
    lại của Django vẫn chạy y như cũ.

    Hai tầng dùng cho dễ quản lý:
      - khối nguyên tử: is_role=False, chỉ là vật liệu lắp ghép, không tạo Group
      - vai trò:        is_role=True, tạo Group thật, gán cho người dùng được

    Chỉ superuser sửa được. Lý do: ai sửa được tập quyền thì tự cấp cho mình bất kỳ
    quyền nào, tức tương đương superuser. Đây không phải thứ để giao qua nhóm.
    """
    name = models.SlugField(max_length=48, unique=True, verbose_name=_('identifier'),
                            help_text=_('Short name, e.g. ra-de'))
    label = models.CharField(max_length=100, blank=True, verbose_name=_('display name'))
    note = models.TextField(blank=True, verbose_name=_('note'),
                            help_text=_('Why this set exists, what it is for.'))
    is_role = models.BooleanField(
        default=False, verbose_name=_('is a role'),
        help_text=_('Roles become real groups you can assign to people. Leave off for building '
                    'blocks that only exist to be combined into roles.'))
    permissions = models.ManyToManyField('auth.Permission', blank=True,
                                         verbose_name=_('permissions'))
    includes = models.ManyToManyField('self', blank=True, symmetrical=False,
                                      related_name='included_by', verbose_name=_('includes sets'),
                                      help_text=_('Everything in these sets is added to this one.'))
    order = models.PositiveIntegerField(default=0, db_index=True, verbose_name=_('order'))

    class Meta:
        verbose_name = _('permission set')
        verbose_name_plural = _('permission sets')
        ordering = ['order', 'name']

    def __str__(self):
        return self.label or self.name

    # -- đại số --------------------------------------------------------------

    def resolve(self, _seen=None):
        """Nở thành tập Permission phẳng. Ném ValueError nếu có tham chiếu vòng."""
        _seen = _seen or []
        if self.pk in _seen:
            raise ValueError('tham chiếu vòng qua tập "%s"' % self.name)
        out = set(self.permissions.all())
        for child in self.includes.all():
            out |= child.resolve(_seen + [self.pk])
        return out

    def has_cycle(self):
        try:
            self.resolve()
            return False
        except ValueError:
            return True

    def resolved_count(self):
        """Số quyền sau khi nở. Trả -1 nếu có tham chiếu vòng.

        Trong một lượt hiển thị danh sách, dùng bản đồ đã tính sẵn (xem
        batch_resolve) thay vì đệ quy lại — nếu không thì mỗi dòng lại nã một loạt
        truy vấn và trang danh sách 20 dòng ngốn 90 truy vấn.
        """
        cached = getattr(_local, 'resolve_map', None)
        if cached is not None:
            hit = cached.get(self.pk, ())
            return -1 if hit is None else len(hit)
        try:
            return len(self.resolve())
        except ValueError:
            return -1

    @classmethod
    def resolve_map(cls):
        """Nở TOÀN BỘ trong 2 truy vấn rồi tính trong bộ nhớ.
        Trả {pk: set(permission_id)}; giá trị None nghĩa là tập đó có vòng."""
        sets = list(cls.objects.prefetch_related('permissions', 'includes'))
        direct = {s.pk: {p.pk for p in s.permissions.all()} for s in sets}
        kids = {s.pk: [c.pk for c in s.includes.all()] for s in sets}
        memo = {}

        def walk(pk, stack):
            if pk in memo:
                return memo[pk]
            if pk in stack:
                return None            # vòng
            out = set(direct.get(pk, ()))
            for c in kids.get(pk, ()):
                sub = walk(c, stack | {pk})
                if sub is None:
                    memo[pk] = None
                    return None
                out |= sub
            memo[pk] = out
            return out

        return {s.pk: walk(s.pk, set()) for s in sets}

    @classmethod
    @contextlib.contextmanager
    def batch_resolve(cls):
        """Trong khối này, resolved_count() dùng bản đồ tính sẵn.
        Dùng threading.local chứ không gán lên ModelAdmin: ModelAdmin là một
        instance dùng chung cho mọi request."""
        _local.resolve_map = cls.resolve_map()
        try:
            yield
        finally:
            _local.resolve_map = None

    def group_name(self):
        return self.name

    def materialize(self):
        """Ghi kết quả đã nở vào Django Group cùng tên. Trả về (group, số quyền)."""
        from django.contrib.auth.models import Group
        if not self.is_role:
            Group.objects.filter(name=self.group_name()).delete()
            return None, 0
        perms = self.resolve()
        g, _ = Group.objects.get_or_create(name=self.group_name())
        g.permissions.set(perms)
        return g, len(perms)

    @classmethod
    def materialize_all(cls):
        """Nở lại toàn bộ. Gọi sau mỗi lần sửa — một tập được sửa thì mọi vai trò
        chứa nó (kể cả gián tiếp) đều đổi theo, nên tính lại tất cho chắc."""
        done = []
        for s in cls.objects.filter(is_role=True):
            try:
                g, n = s.materialize()
                if g:
                    done.append((s.name, n))
            except ValueError:
                pass   # tập có vòng thì bỏ qua, form đã chặn từ trước
        return done


@receiver([post_save, post_delete], sender=PermSet)
def _permset_changed(sender, instance, **kwargs):
    PermSet.materialize_all()


@receiver(m2m_changed, sender=PermSet.permissions.through)
def _permset_perms_changed(sender, instance, action, **kwargs):
    if action.startswith('post_'):
        PermSet.materialize_all()


@receiver(m2m_changed, sender=PermSet.includes.through)
def _permset_includes_changed(sender, instance, action, **kwargs):
    if action.startswith('post_'):
        PermSet.materialize_all()


class JudgeSwitch(models.Model):
    """Công tắc bật/tắt từng máy chấm, không cần SSH.

    Web KHÔNG tự chạy docker. Nó chỉ ghi ý muốn ra một file spool; tiến trình
    health_collector chạy dưới root đọc file đó rồi start/stop container tương ứng.
    Cho tiến trình web quyền chạy docker (hoặc sudo) là biến một lỗ trong Django
    thành lỗ root, nên ranh giới đặt ở đây: web nói MUỐN GÌ, root quyết định LÀM GÌ,
    và root chỉ chấp nhận tên khớp ^judge[0-9]+$ nằm trong danh sách container nó tự
    liệt kê được.
    """
    SPOOL = '/var/lib/oj-judges/desired.json'

    name = models.CharField(max_length=50, unique=True, verbose_name=_('judge name'),
                            help_text=_('Must match the container name, e.g. judge3'))
    enabled = models.BooleanField(default=True, verbose_name=_('enabled'))
    note = models.CharField(max_length=200, blank=True, verbose_name=_('note'))
    changed_by = models.ForeignKey(Profile, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='+', verbose_name=_('last changed by'))
    modified = models.DateTimeField(auto_now=True, verbose_name=_('last changed'))

    class Meta:
        verbose_name = _('judge switch')
        verbose_name_plural = _('judge switches')
        ordering = ['name']
        permissions = (('control_judges', _('Turn judges on and off')),)

    def __str__(self):
        return f'{self.name} ({"bật" if self.enabled else "tắt"})'

    @classmethod
    def write_spool(cls):
        """Ghi ý muốn ra file cho tiến trình root đọc. Ghi tạm rồi đổi tên để
        tiến trình kia không đọc phải file dở dang."""
        import json
        import os
        import tempfile
        data = {'desired': {s.name: s.enabled for s in cls.objects.all()},
                'ts': int(timezone.now().timestamp())}
        d = os.path.dirname(cls.SPOOL)
        try:
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump(data, f)
            os.chmod(tmp, 0o644)
            os.replace(tmp, cls.SPOOL)
            return True
        except OSError:
            return False

    @classmethod
    def sync_from_containers(cls, names):
        """Tạo bản ghi cho container mới thấy lần đầu, mặc định là bật."""
        have = set(cls.objects.values_list('name', flat=True))
        new = [cls(name=n) for n in names if n not in have]
        if new:
            cls.objects.bulk_create(new)
            cls.write_spool()
        return len(new)


@receiver([post_save, post_delete], sender=JudgeSwitch)
def _judgeswitch_changed(sender, instance, **kwargs):
    JudgeSwitch.write_spool()
