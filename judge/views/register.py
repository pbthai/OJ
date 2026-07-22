# coding=utf-8
import re

from django import forms
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.auth.password_validation import get_default_password_validators
from django.db import transaction
from django.forms import ChoiceField, ModelChoiceField
from django.shortcuts import render
from django.utils.translation import gettext, gettext_lazy as _, ngettext
from registration.backends.default.views import (ActivationView as OldActivationView,
                                                 RegistrationView as OldRegistrationView)
from registration.forms import RegistrationForm
from sortedm2m.forms import SortedMultipleChoiceField

from judge.forms import SocialAuthMixin
from judge.models import Language, Organization, Profile, TIMEZONE
from judge.utils.recaptcha import ReCaptchaField, ReCaptchaWidget
from judge.utils.subscription import Subscription, newsletter_id
from judge.widgets import Select2MultipleWidget, Select2Widget

bad_mail_regex = list(map(re.compile, settings.BAD_MAIL_PROVIDER_REGEX))


# Tên miền chắc chắn sống, dùng làm "canary" để phân biệt "tên miền người dùng gõ
# bị hỏng" với "DNS của server đang chết". Chỉ cần một cái phân giải được là biết
# resolver còn chạy.
_MAIL_DNS_CANARIES = ('gmail.com', 'hcmus.edu.vn')


def _domain_mail_lookup(domain):
    """Tra DNS THÔ cho một tên miền. Trả True (nhận thư được), False (chắc chắn
    không), None (không tra cứu được: timeout/SERVFAIL/không có dnspython)."""
    try:
        import dns.exception
        import dns.resolver
    except ImportError:
        # Không có dnspython: lùi về getaddrinfo (chỉ thấy A/AAAA). Bỏ sót tên miền
        # chỉ-MX (hiếm) nên chỉ dùng để bắt tên miền không phân giải ra gì cả.
        import socket
        try:
            socket.getaddrinfo(domain, None)
            return True
        except socket.gaierror:
            return False
        except OSError:
            return None

    resolver = dns.resolver.Resolver()
    resolver.timeout = resolver.lifetime = 5.0

    def query(rdtype):
        try:
            return bool(len(resolver.resolve(domain, rdtype)))
        except dns.resolver.NoAnswer:
            return False               # tên miền có thật nhưng không có bản ghi này
        except dns.resolver.NXDOMAIN:
            return 'nxdomain'          # tên miền không tồn tại
        except (dns.resolver.NoNameservers, dns.exception.DNSException):
            return None                # SERVFAIL/timeout -> chưa kết luận được

    mx = query('MX')
    if mx is True:
        return True
    if mx == 'nxdomain':
        return False
    if mx is None:
        return None
    # Không có MX: RFC 5321 cho phép chuyển thư tới A/AAAA (implicit MX).
    for rdtype in ('A', 'AAAA'):
        got = query(rdtype)
        if got is True:
            return True
        if got is None:
            return None
    return False


def email_domain_accepts_mail(domain):
    """Tên miền của email có nhận được thư không?

    Trả về True (nhận được), False (chắc chắn không -> bắt gõ lại), hoặc None
    (không xác định được vì DNS của server đang hỏng -> phía gọi nên fail-open).

    Đây là lý do phần lớn email sai lọt lưới: cú pháp đúng nhưng tên miền gõ nhầm
    (gmail.con) hoặc là địa chỉ dùng-một-lần đã chết. Ví dụ thực tế toaik.com trong
    log bounce trả SERVFAIL (nameserver hỏng) nên thư không bao giờ tới — trường hợp
    này KHÔNG để lọt: nếu canary còn phân giải được thì kết luận chính tên miền kia
    hỏng và chặn; chỉ khi đến canary cũng tra không ra mới coi là DNS server chết và
    cho qua.
    """
    domain = (domain or '').strip().rstrip('.')
    if not domain:
        return False
    verdict = _domain_mail_lookup(domain)
    if verdict is not None:
        return verdict
    # Không xác định được cho tên miền này. Kiểm resolver có còn sống không.
    if any(_domain_mail_lookup(c) is True for c in _MAIL_DNS_CANARIES):
        return False   # resolver chạy tốt -> chính tên miền người dùng nhập hỏng
    return None        # canary cũng hỏng -> DNS server chết -> fail-open, đừng chặn


class CustomRegistrationForm(RegistrationForm):
    username = forms.RegexField(regex=re.compile(r'^\w+$', re.ASCII), max_length=30, label=_('Username'),
                                error_messages={'invalid': _('A username must contain letters, '
                                                             'numbers, or underscores.')})
    full_name = forms.CharField(max_length=30, label=_('Full name'), required=False)
    timezone = ChoiceField(label=_('Timezone'), choices=TIMEZONE,
                           widget=Select2Widget(attrs={'style': 'width:100%'}))
    language = ModelChoiceField(queryset=Language.objects.all(), label=_('Preferred language'), empty_label=None,
                                widget=Select2Widget(attrs={'style': 'width:100%'}))
    organizations = SortedMultipleChoiceField(queryset=Organization.objects.filter(is_open=True),
                                              label=_('Organizations'), required=False,
                                              widget=Select2MultipleWidget(attrs={'style': 'width:100%'}))

    if newsletter_id is not None:
        newsletter = forms.BooleanField(label=_('Subscribe to newsletter?'), initial=True, required=False)

    if ReCaptchaField is not None:
        captcha = ReCaptchaField(widget=ReCaptchaWidget())

    def clean_email(self):
        if User.objects.filter(email=self.cleaned_data['email']).exists():
            raise forms.ValidationError(gettext('The email address "%s" is already taken. Only one registration '
                                                'is allowed per address.') % self.cleaned_data['email'])
        if '@' in self.cleaned_data['email']:
            domain = self.cleaned_data['email'].split('@')[-1].lower()
            if (domain in settings.BAD_MAIL_PROVIDERS or
                    any(regex.match(domain) for regex in bad_mail_regex)):
                raise forms.ValidationError(gettext('Your email provider is not allowed due to history of abuse. '
                                                    'Please use a reputable email provider.'))
            # Chặn email không gửi tới được (tên miền gõ nhầm hoặc đã chết): nếu
            # tên miền không có chỗ nhận thư thì bắt nhập lại ngay, thay vì để link
            # kích hoạt bị bounce và tài khoản treo. Chỉ chặn khi CHẮC CHẮN sai
            # (False); tra cứu hỏng (None) thì cho qua để DNS chập chờn không cản
            # người dùng thật. Tắt được qua settings nếu server thiếu DNS ra ngoài.
            if getattr(settings, 'REGISTRATION_VALIDATE_EMAIL_DELIVERABILITY', True):
                if email_domain_accepts_mail(domain) is False:
                    raise forms.ValidationError(
                        gettext('We could not find a mail server for “%s”. Please check the '
                                'address for typos and enter an email that can receive messages.')
                        % domain)
        return self.cleaned_data['email']

    def clean_organizations(self):
        organizations = self.cleaned_data.get('organizations') or []
        max_orgs = settings.DMOJ_USER_MAX_ORGANIZATION_COUNT
        if len(organizations) > max_orgs:
            raise forms.ValidationError(ngettext('You may not be part of more than {count} public organization.',
                                                 'You may not be part of more than {count} public organizations.',
                                                 max_orgs).format(count=max_orgs))
        return self.cleaned_data['organizations']


class RegistrationView(OldRegistrationView):
    title = _('Register')
    form_class = CustomRegistrationForm
    social_auth = SocialAuthMixin()
    template_name = 'registration/registration_form.html'

    def get_context_data(self, **kwargs):
        if 'title' not in kwargs:
            kwargs['title'] = self.title
        kwargs['TIMEZONE_MAP'] = settings.TIMEZONE_MAP
        kwargs['password_validators'] = get_default_password_validators()
        kwargs['tos_url'] = settings.TERMS_OF_SERVICE_URL
        kwargs['oauth_only'] = settings.OAUTH_ONLY
        kwargs['oauth'] = self.social_auth
        return super(RegistrationView, self).get_context_data(**kwargs)

    @transaction.atomic
    def register(self, form):
        # We put the entire function inside a transaction
        # This makes sure that `user` and `profile` are created in the same transaction
        # It also delays the sending of emails until the transaction is committed
        # (sending emails can cause error, resulting in an unclean database)
        # See https://github.com/macropin/django-registration/blob/v2.9/registration/models.py#L188-L193

        user = super(RegistrationView, self).register(form)
        profile, _ = Profile.objects.get_or_create(user=user, defaults={
            'language': Language.get_default_language(),
        })

        cleaned_data = form.cleaned_data
        user.first_name = cleaned_data['full_name']
        profile.timezone = cleaned_data['timezone']
        profile.language = cleaned_data['language']
        profile.organizations.add(*cleaned_data['organizations'])

        user.save()
        profile.save()

        if newsletter_id is not None and cleaned_data['newsletter']:
            Subscription(user=user, newsletter_id=newsletter_id, subscribed=True).save()
        return user

    def get_initial(self, *args, **kwargs):
        initial = super(RegistrationView, self).get_initial(*args, **kwargs)
        initial['timezone'] = settings.DEFAULT_USER_TIME_ZONE
        initial['language'] = Language.objects.get(key=settings.DEFAULT_USER_LANGUAGE)
        return initial


class ActivationView(OldActivationView):
    title = _('Activation Key Invalid')
    template_name = 'registration/activate.html'

    def get_context_data(self, **kwargs):
        if 'title' not in kwargs:
            kwargs['title'] = self.title
        return super(ActivationView, self).get_context_data(**kwargs)


def social_auth_error(request):
    return render(request, 'generic-message.html', {
        'title': gettext('Authentication failure'),
        'message': request.GET.get('message'),
    })
