from django import forms
from django.contrib.auth.forms import PasswordChangeForm, SetPasswordForm, UserCreationForm
from django.contrib.auth.models import User
from django.contrib.auth.validators import UnicodeUsernameValidator

from .models import SiteConfiguration, UsernameAlias


username_validator = UnicodeUsernameValidator()


def validate_username_value(username, *, exclude_user_id=None):
    value = (username or '').strip()
    if not value:
        raise forms.ValidationError('用户名不能为空')
    if len(value) > User._meta.get_field('username').max_length:
        raise forms.ValidationError('用户名长度不能超过 150 个字符')
    username_validator(value)

    queryset = User.objects.all()
    if exclude_user_id is not None:
        queryset = queryset.exclude(pk=exclude_user_id)
    if queryset.filter(username__iexact=value).exists():
        raise forms.ValidationError('这个用户名已经被使用了')

    alias_queryset = UsernameAlias.objects.all()
    if exclude_user_id is not None:
        alias_queryset = alias_queryset.exclude(user_id=exclude_user_id)
    if alias_queryset.filter(username__iexact=value).exists():
        raise forms.ValidationError('这个用户名已经被保留为历史链接，请换一个用户名')
    return value


class RegistrationForm(UserCreationForm):
    friend_id = forms.CharField(
        max_length=11,
        min_length=8,
        required=False,
        label='好友 ID',
        help_text='留空则自动使用用户名生成一个好友 ID',
        widget=forms.TextInput(
            attrs={
                'minlength': 8,
                'maxlength': 11,
                'placeholder': '8-11 位，留空则自动生成',
            }
        ),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('username', 'friend_id', 'password1', 'password2')

    def clean_username(self):
        return validate_username_value(self.cleaned_data.get('username'))

    def clean_friend_id(self):
        value = (self.cleaned_data.get('friend_id') or '').strip().lower()
        if not value:
            return ''
        if not all(ch.isalnum() or ch == '_' for ch in value):
            raise forms.ValidationError('好友 ID 只能包含小写字母、数字或下划线')
        if len(value) < 8 or len(value) > 11:
            raise forms.ValidationError('好友 ID 长度需要在 8 到 11 位之间')
        return value


class SiteConfigurationForm(forms.ModelForm):
    class Meta:
        model = SiteConfiguration
        fields = (
            'site_title',
            'site_favicon',
            'allowed_hosts',
            'trusted_origins',
            'cors_allowed_origins',
            'allow_all_cors',
            'chat_attachment_max_mb',
        )
        widgets = {
            'site_title': forms.TextInput(attrs={'placeholder': '例如：animal chat'}),
            'allowed_hosts': forms.Textarea(attrs={'rows': 5, 'placeholder': '每行一个 Host，例如：chat.example.com 或 .example.com'}),
            'trusted_origins': forms.Textarea(attrs={'rows': 6, 'placeholder': '每行一个来源，例如：https://chat.6143443.xyz'}),
            'cors_allowed_origins': forms.Textarea(attrs={'rows': 6, 'placeholder': '每行一个来源，例如：https://app.example.com'}),
            'chat_attachment_max_mb': forms.NumberInput(attrs={'min': 1, 'max': 1024, 'step': 1}),
        }
        labels = {
            'site_title': '网页标题',
            'site_favicon': '网页图标',
            'allowed_hosts': '允许访问 Host',
            'trusted_origins': 'CSRF 受信任来源',
            'cors_allowed_origins': 'CORS 允许来源',
            'allow_all_cors': '允许所有跨域来源',
            'chat_attachment_max_mb': '聊天附件大小上限(MB)',
        }
        help_texts = {
            'site_title': '浏览器标签页显示的标题，留空时默认使用 animal chat。',
            'site_favicon': '支持 PNG、JPG、ICO、WebP。上传后会显示在浏览器标签页。',
            'allowed_hosts': '用于 Django 的 ALLOWED_HOSTS 校验。每行一个 Host，不带协议头，例如 chat.example.com、127.0.0.1 或 .example.com。',
            'trusted_origins': '用于 Django 的 CSRF Origin 校验。需要带协议头，例如 https://example.com',
            'cors_allowed_origins': '用于响应头 Access-Control-Allow-Origin。需要带协议头，例如 https://example.com',
            'allow_all_cors': '开发调试时可以开启；生产环境建议关闭并只填写明确来源。',
            'chat_attachment_max_mb': '上传图片和文件时允许的单文件最大体积，修改后新请求立即按这个值校验。',
        }

    def clean_allowed_hosts(self):
        raw_value = self.cleaned_data.get('allowed_hosts', '')
        hosts = SiteConfiguration.parse_origin_lines(raw_value)
        for host in hosts:
            if '://' in host:
                raise forms.ValidationError('Host 不能包含 http:// 或 https:// 协议头')
            if '/' in host or '\\' in host:
                raise forms.ValidationError('Host 不能包含路径')
            if ':' in host and host.count(':') == 1:
                raise forms.ValidationError('Host 不需要带端口，例如直接填写 chat.example.com')
            if host == '*':
                raise forms.ValidationError('请填写明确的 Host，避免使用 *')
        return '\n'.join(hosts)

    def clean_trusted_origins(self):
        return self._clean_origin_block('trusted_origins')

    def clean_cors_allowed_origins(self):
        return self._clean_origin_block('cors_allowed_origins')

    def clean_chat_attachment_max_mb(self):
        value = int(self.cleaned_data.get('chat_attachment_max_mb') or 50)
        if value < 1:
            raise forms.ValidationError('附件大小上限至少要 1MB')
        if value > 1024:
            raise forms.ValidationError('附件大小上限建议不要超过 1024MB')
        return value

    def _clean_origin_block(self, field_name):
        raw_value = self.cleaned_data.get(field_name, '')
        origins = SiteConfiguration.parse_origin_lines(raw_value)
        for origin in origins:
            if not (origin.startswith('http://') or origin.startswith('https://')):
                raise forms.ValidationError('来源必须以 http:// 或 https:// 开头')
        return '\n'.join(origins)


class AdminUserPasswordForm(SetPasswordForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['new_password1'].label = '新密码'
        self.fields['new_password2'].label = '确认新密码'


class ProfilePasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['old_password'].label = '当前密码'
        self.fields['new_password1'].label = '新密码'
        self.fields['new_password2'].label = '确认新密码'
        for field in self.fields.values():
            field.widget.attrs.setdefault('class', 'form-input')
            field.widget.attrs.pop('autofocus', None)
