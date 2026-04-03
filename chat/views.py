import json
import hashlib
import io
import logging
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote, urlencode

# chat/views.py
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from PIL import Image, ImageOps
from django import forms
from django.conf import settings
from django.shortcuts import render, redirect
from django.utils import timezone
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.password_validation import password_validators_help_text_html
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_POST
from django.views.decorators.cache import never_cache
from django.utils.safestring import mark_safe
from django.utils.http import url_has_allowed_host_and_scheme
from django.http import JsonResponse
from django.urls import reverse
from django.views.decorators.clickjacking import xframe_options_sameorigin
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.contrib.sessions.models import Session
from django.contrib import messages
from django.contrib.auth.models import User
from django.db import IntegrityError
from django.db.utils import OperationalError, ProgrammingError
from django.db.models import Q
from .forms import (
    AdminUserPasswordForm,
    ProfilePasswordChangeForm,
    RegistrationForm,
    SiteConfigurationForm,
    validate_username_value,
)
from .models import (
    DirectConversation,
    DirectConversationState,
    DirectMessage,
    FriendRequest,
    Friendship,
    Message,
    MobileDevice,
    Room,
    RoomInvitation,
    RoomJoinRequest,
    RoomMembership,
    SiteConfiguration,
    UserEmoji,
    RoomVisitState,
    UserChatProfile,
    UsernameAlias,
    UserSession,
)
from .presets import CHAT_BUBBLE_STYLES, CHAT_COLOR_THEMES, DEFAULT_CHAT_STYLE, DEFAULT_CHAT_THEME
from .services.geoip_service import GeoIPService

logger = logging.getLogger(__name__)


DEFAULT_ROOM_AVATARS = ['💬', '🐱', '🐶', '🐻', '🎮', '📚', '☕', '🌙', '🎵', '🍀']
MAX_AVATAR_BYTES = 1024 * 1024
MAX_AVATAR_DIMENSION = 720
MAX_CHAT_ATTACHMENT_BYTES = getattr(settings, 'CHAT_ATTACHMENT_MAX_BYTES', 50 * 1024 * 1024)
MAX_CHAT_ATTACHMENT_IMAGE_DIMENSION = 1920
MAX_ROOM_ADMIN_COUNT = 10
BUILTIN_EMOJIS = ['😀', '😂', '🥹', '😎', '🥳', '🤔', '😭', '😡', '🥰', '👍', '🙏', '🎉']
QUOTED_MESSAGE_PATTERN = re.compile(r'^\[\[quote\|([^|\]]*)\|([^|\]]*)\|([^|\]]*)\]\]\n?([\s\S]*)$')


def build_room_group_name(room_name):
    return f"chat_{hashlib.sha256(room_name.encode('utf-8')).hexdigest()[:32]}"


def get_chat_attachment_limit_bytes():
    site_config = SiteConfiguration.get_solo()
    if site_config is not None:
        return site_config.chat_attachment_max_bytes
    return MAX_CHAT_ATTACHMENT_BYTES


def get_safe_next_url(request, fallback_name='chat_index'):
    fallback_url = reverse(fallback_name)
    candidate = request.POST.get('next') or request.GET.get('next') or ''
    if candidate and url_has_allowed_host_and_scheme(
        candidate,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return candidate
    return fallback_url


def get_thread_preview_text(message_text, limit=36):
    raw = (message_text or '').strip()
    if not raw:
        return ''

    match = QUOTED_MESSAGE_PATTERN.match(raw)
    if match:
        raw = (match.group(4) or '').strip()

    if not raw:
        raw = '引用消息'

    return raw[:limit]


def get_attachment_preview_label(kind, name=''):
    if kind == 'image':
        return f'[图片] {name}'.strip()
    if kind == 'video':
        return f'[视频] {name}'.strip()
    if kind == 'file':
        return f'[文件] {name}'.strip()
    return (name or '').strip()


def get_message_preview_text(message_text='', attachment_kind='', attachment_name='', limit=36):
    raw = get_thread_preview_text(message_text, limit=limit)
    if raw:
        return raw

    fallback = get_attachment_preview_label(attachment_kind, attachment_name)
    if not fallback:
        return ''
    return fallback[:limit]


def get_room_hub_url(room_name):
    return f"{reverse('chat_index')}?{urlencode({'thread_type': 'room', 'target': room_name})}"


def get_direct_hub_url(username):
    return f"{reverse('chat_index')}?{urlencode({'thread_type': 'direct', 'target': username})}"


def resolve_user_by_public_id(public_id):
    profile = UserChatProfile.objects.filter(public_id=public_id).select_related('user').first()
    if not profile:
        raise User.DoesNotExist
    return profile.user


def resolve_user_by_username(username):
    try:
        return User.objects.get(username=username), False
    except User.DoesNotExist:
        alias = UsernameAlias.objects.filter(username__iexact=username).select_related('user').first()
        if alias:
            return alias.user, True
        raise


def build_canonical_username_redirect(request, view_name, canonical_username, **kwargs):
    route_kwargs = {'username': canonical_username}
    route_kwargs.update(kwargs)
    target_url = reverse(view_name, kwargs=route_kwargs)
    query_string = request.META.get('QUERY_STRING', '')
    if query_string:
        target_url = f'{target_url}?{query_string}'
    return redirect(target_url)


def build_canonical_public_id_redirect(request, view_name, canonical_public_id, **kwargs):
    route_kwargs = {'public_id': canonical_public_id}
    route_kwargs.update(kwargs)
    target_url = reverse(view_name, kwargs=route_kwargs)
    query_string = request.META.get('QUERY_STRING', '')
    if query_string:
        target_url = f'{target_url}?{query_string}'
    return redirect(target_url)


def get_user_profile_url(user):
    profile = get_or_create_chat_profile(user)
    return reverse('user_profile', kwargs={'public_id': profile.public_id})


def get_direct_chat_url(user):
    profile = get_or_create_chat_profile(user)
    return reverse('direct_chat', kwargs={'public_id': profile.public_id})


def get_direct_attachment_url(user):
    profile = get_or_create_chat_profile(user)
    return reverse('upload_direct_attachment', kwargs={'public_id': profile.public_id})


def get_direct_read_url(user):
    profile = get_or_create_chat_profile(user)
    return reverse('mark_direct_read', kwargs={'public_id': profile.public_id})


def get_direct_delete_url(user):
    profile = get_or_create_chat_profile(user)
    return reverse('delete_direct_conversation', kwargs={'public_id': profile.public_id})


def get_direct_emoji_send_url(user, emoji_id):
    profile = get_or_create_chat_profile(user)
    return reverse('send_direct_emoji', kwargs={'public_id': profile.public_id, 'emoji_id': emoji_id})


def get_direct_emoji_favorite_url(user, message_id):
    profile = get_or_create_chat_profile(user)
    return reverse('favorite_direct_image_emoji', kwargs={'public_id': profile.public_id, 'message_id': message_id})


def get_json_request_data(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode('utf-8'))
    except (TypeError, ValueError, UnicodeDecodeError):
        return None


def get_remove_friend_url(user):
    profile = get_or_create_chat_profile(user)
    return reverse('remove_friend', kwargs={'public_id': profile.public_id})


ADMIN_PAGE_SIZE_OPTIONS = (10, 20, 50, 100)


def get_admin_page_size(request, key, default=10):
    raw_value = request.GET.get(key, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = default
    if value not in ADMIN_PAGE_SIZE_OPTIONS:
        return default
    return value


def build_admin_list_redirect_url(view_name, request, page_key='page', page_size_key='page_size', default_page_size='10', extra_params=None):
    params = {
        page_key: request.POST.get(page_key) or request.GET.get(page_key) or '1',
        page_size_key: request.POST.get(page_size_key) or request.GET.get(page_size_key) or default_page_size,
    }
    if extra_params:
        params.update(extra_params)
    return f"{reverse(view_name)}?{urlencode(params)}"


def notify_user_presence_changed(user):
    if not user or not user.is_authenticated:
        return

    channel_layer = get_channel_layer()
    if channel_layer is None:
        return

    room_names = Room.objects.filter(
        Q(created_by=user) | Q(memberships__user=user, memberships__is_active=True)
    ).values_list('name', flat=True).distinct()

    for room_name in room_names:
        async_to_sync(channel_layer.group_send)(
            build_room_group_name(room_name),
            {
                'type': 'presence_refresh',
            }
        )


def get_or_create_chat_profile(user):
    profile, _ = UserChatProfile.objects.get_or_create(
        user=user,
        defaults={
            'public_id': UserChatProfile.generate_unique_public_id(exclude_user_id=user.id),
            'display_name': user.username,
            'friend_id': UserChatProfile.generate_unique_friend_id(user.username, exclude_user_id=user.id),
            'avatar_label': '',
            'color_theme': DEFAULT_CHAT_THEME,
            'bubble_style': DEFAULT_CHAT_STYLE,
            'show_location': True,
        },
    )
    update_fields = []
    if not profile.public_id:
        profile.public_id = UserChatProfile.generate_unique_public_id(exclude_user_id=user.id)
        update_fields.append('public_id')
    if not profile.display_name:
        profile.display_name = user.username
        update_fields.append('display_name')
    if not profile.friend_id:
        profile.friend_id = UserChatProfile.generate_unique_friend_id(user.username, exclude_user_id=user.id)
        update_fields.append('friend_id')
    if update_fields:
        profile.save(update_fields=update_fields)
    return profile


def compress_image_upload(uploaded_file, base_name, upload_dir):
    try:
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
    except Exception:
        raise ValueError('无法识别这张图片，请重新选择 JPG、PNG 或 WebP 图片')

    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')
    elif image.mode == 'L':
        image = image.convert('RGB')

    image.thumbnail((MAX_AVATAR_DIMENSION, MAX_AVATAR_DIMENSION), Image.Resampling.LANCZOS)
    quality = 88
    working_image = image

    while True:
        buffer = io.BytesIO()
        working_image.save(buffer, format='JPEG', quality=quality, optimize=True, progressive=True)
        content = buffer.getvalue()
        if len(content) <= MAX_AVATAR_BYTES or quality <= 42:
            if len(content) <= MAX_AVATAR_BYTES:
                break

            resized_width = max(160, int(working_image.width * 0.88))
            resized_height = max(160, int(working_image.height * 0.88))
            if resized_width == working_image.width and resized_height == working_image.height:
                break
            working_image = working_image.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
            quality = min(quality + 6, 88)
            continue
        quality -= 8

    normalized_base = base_name or 'file'
    safe_name = ''.join(ch for ch in normalized_base.lower() if ch.isalnum() or ch == '_') or 'file'
    hashed_suffix = hashlib.sha1(normalized_base.encode('utf-8')).hexdigest()[:10]
    # ImageField.save() already prepends the model field's upload_to path.
    # Returning only the filename avoids duplicated paths like avatars/avatars/*.
    return ContentFile(content, name=f'{safe_name}_{hashed_suffix}.jpg')


def compress_avatar_upload(uploaded_file, username):
    return compress_image_upload(uploaded_file, f'{username}_avatar', 'avatars')


def compress_room_avatar_upload(uploaded_file, room_name):
    return compress_image_upload(uploaded_file, f'{room_name}_room_avatar', 'room_avatars')


def optimize_chat_image_upload(uploaded_file, base_name):
    try:
        image = Image.open(uploaded_file)
        image = ImageOps.exif_transpose(image)
    except Exception:
        raise ValueError('无法识别这张图片，请重新选择 JPG、PNG 或 WebP 图片')

    if image.mode not in ('RGB', 'L'):
        image = image.convert('RGB')
    elif image.mode == 'L':
        image = image.convert('RGB')

    image.thumbnail((MAX_CHAT_ATTACHMENT_IMAGE_DIMENSION, MAX_CHAT_ATTACHMENT_IMAGE_DIMENSION), Image.Resampling.LANCZOS)
    quality = 90
    working_image = image

    while True:
        buffer = io.BytesIO()
        working_image.save(buffer, format='JPEG', quality=quality, optimize=True, progressive=True)
        content = buffer.getvalue()
        attachment_limit_bytes = get_chat_attachment_limit_bytes()
        if len(content) <= attachment_limit_bytes or quality <= 48:
            if len(content) <= attachment_limit_bytes:
                break

            resized_width = max(320, int(working_image.width * 0.9))
            resized_height = max(320, int(working_image.height * 0.9))
            if resized_width == working_image.width and resized_height == working_image.height:
                break
            working_image = working_image.resize((resized_width, resized_height), Image.Resampling.LANCZOS)
            quality = min(quality + 4, 90)
            continue
        quality -= 6

    normalized_base = base_name or 'image'
    safe_name = ''.join(ch for ch in normalized_base.lower() if ch.isalnum() or ch == '_') or 'image'
    hashed_suffix = hashlib.sha1(normalized_base.encode('utf-8')).hexdigest()[:10]
    return ContentFile(content, name=f'{safe_name}_{hashed_suffix}.jpg')


def build_attachment_name(base_name, fallback='file'):
    raw_name = (base_name or fallback).strip()
    stem, ext = os.path.splitext(raw_name)
    safe_stem = ''.join(ch for ch in (stem or fallback).lower() if ch.isalnum() or ch in {'_', '-'}) or fallback
    safe_ext = ''.join(ch for ch in ext.lower() if ch.isalnum() or ch == '.')[:12]
    hashed_suffix = hashlib.sha1(raw_name.encode('utf-8')).hexdigest()[:10]
    return f'{safe_stem[:48]}_{hashed_suffix}{safe_ext}'


def prepare_chat_attachment(uploaded_file, base_name):
    content_type = (getattr(uploaded_file, 'content_type', '') or '').lower()
    original_name = (getattr(uploaded_file, 'name', '') or base_name or 'file').strip() or 'file'
    size = getattr(uploaded_file, 'size', 0) or 0
    normalized_extension = os.path.splitext(original_name)[1].lower()

    attachment_limit_bytes = get_chat_attachment_limit_bytes()
    if size > attachment_limit_bytes:
        raise ValueError(f'附件不能超过 {max(1, attachment_limit_bytes // (1024 * 1024))}MB')

    if content_type == 'image/gif' or normalized_extension == '.gif':
        uploaded_file.name = build_attachment_name(original_name, fallback='image')
        return {
            'file': uploaded_file,
            'attachment_type': 'image',
            'attachment_name': original_name,
            'attachment_mime': 'image/gif',
            'attachment_size': size,
        }

    if content_type.startswith('image/'):
        optimized = optimize_chat_image_upload(uploaded_file, base_name)
        return {
            'file': optimized,
            'attachment_type': 'image',
            'attachment_name': original_name,
            'attachment_mime': 'image/jpeg',
            'attachment_size': optimized.size,
        }

    if content_type.startswith('video/'):
        uploaded_file.name = build_attachment_name(original_name, fallback='video')
        return {
            'file': uploaded_file,
            'attachment_type': 'video',
            'attachment_name': original_name,
            'attachment_mime': content_type,
            'attachment_size': size,
        }

    uploaded_file.name = build_attachment_name(original_name)
    guessed_type = content_type or mimetypes.guess_type(original_name)[0] or 'application/octet-stream'
    return {
        'file': uploaded_file,
        'attachment_type': 'file',
        'attachment_name': original_name,
        'attachment_mime': guessed_type,
        'attachment_size': size,
    }


def build_attachment_payload(message_obj):
    if not getattr(message_obj, 'attachment', None):
        return None
    try:
        attachment_url = message_obj.attachment.url
    except ValueError:
        return None
    thumbnail_url = ''
    thumbnail_field = getattr(message_obj, 'attachment_thumbnail', None)
    if thumbnail_field:
        try:
            thumbnail_url = thumbnail_field.url
        except ValueError:
            thumbnail_url = ''
    return {
        'url': attachment_url,
        'name': getattr(message_obj, 'attachment_name', '') or os.path.basename(message_obj.attachment.name),
        'mime': getattr(message_obj, 'attachment_mime', '') or '',
        'size': getattr(message_obj, 'attachment_size', 0) or 0,
        'kind': getattr(message_obj, 'attachment_type', 'file') or 'file',
        'thumbnail_url': thumbnail_url,
    }


def try_generate_video_thumbnail(uploaded_field_file, fallback_name='video'):
    if not uploaded_field_file:
        return None
    ffmpeg_path = shutil.which('ffmpeg')
    if not ffmpeg_path:
        return None

    source_path = getattr(uploaded_field_file, 'path', '')
    if not source_path or not os.path.exists(source_path):
        return None

    temp_fd, temp_output = tempfile.mkstemp(prefix='video_thumb_', suffix='.jpg')
    os.close(temp_fd)
    try:
        command = [
            ffmpeg_path,
            '-y',
            '-ss',
            '00:00:00.200',
            '-i',
            source_path,
            '-frames:v',
            '1',
            '-vf',
            'scale=720:-1',
            temp_output,
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not os.path.exists(temp_output) or os.path.getsize(temp_output) == 0:
            return None
        safe_name = build_attachment_name(Path(fallback_name).stem or 'video', fallback='video_thumb')
        with open(temp_output, 'rb') as fh:
            return ContentFile(fh.read(), name=f'{Path(safe_name).stem}.jpg')
    except (OSError, subprocess.SubprocessError):
        return None
    finally:
        try:
            os.remove(temp_output)
        except OSError:
            pass


def create_user_emoji_from_upload(user, uploaded_file, title=''):
    attachment_data = prepare_chat_attachment(uploaded_file, f'{user.username}_emoji')
    if attachment_data['attachment_type'] != 'image':
        raise ValueError('表情只支持图片格式')

    emoji = UserEmoji(
        user=user,
        title=(title or attachment_data['attachment_name'] or '图片表情')[:60],
        last_used_at=timezone.now(),
    )
    emoji.image.save(attachment_data['file'].name, attachment_data['file'], save=False)
    emoji.save()
    return emoji


def clone_attachment_as_emoji(user, source_file, title=''):
    if not source_file:
        raise ValueError('没有可收藏的图片')
    with source_file.open('rb') as fp:
        copied = ContentFile(fp.read(), name=build_attachment_name(os.path.basename(source_file.name), fallback='emoji'))
    emoji = UserEmoji(
        user=user,
        title=(title or os.path.basename(source_file.name) or '图片表情')[:60],
        last_used_at=timezone.now(),
    )
    emoji.image.save(copied.name, copied, save=False)
    emoji.save()
    return emoji


def serialize_room_message_payload(message_obj, appearance):
    return {
        'id': message_obj.id,
        'message': message_obj.message,
        'user': message_obj.username,
        'public_id': appearance.get('public_id', ''),
        'display_name': appearance.get('display_name', message_obj.username),
        'type': message_obj.message_type,
        'timestamp': message_obj.timestamp.isoformat() if message_obj.timestamp else None,
        'location': message_obj.location_label,
        'appearance': appearance,
        'avatar_label': appearance.get('avatar_label', ''),
        'avatar_url': appearance.get('avatar_url', ''),
        'friend_id': appearance.get('friend_id', ''),
        'attachment': build_attachment_payload(message_obj),
    }


def serialize_direct_message_payload(message_obj, appearance):
    return {
        'id': message_obj.id,
        'type': getattr(message_obj, 'message_type', 'chat') or 'chat',
        'message': message_obj.content,
        'user': message_obj.sender.username,
        'public_id': appearance.get('public_id', ''),
        'display_name': appearance.get('display_name', message_obj.sender.username),
        'timestamp': message_obj.created_at.isoformat() if message_obj.created_at else None,
        'avatar_label': appearance.get('avatar_label', ''),
        'avatar_url': appearance.get('avatar_url', ''),
        'appearance': appearance,
        'attachment': build_attachment_payload(message_obj),
    }


def can_recall_message(created_at):
    if not created_at:
        return False
    return created_at >= timezone.now() - timedelta(minutes=5)


def build_room_message_delete_payload(message_id):
    return {
        'type': 'message_deleted',
        'id': message_id,
    }


def build_direct_message_delete_payload(message_id):
    return {
        'type': 'message_deleted',
        'id': message_id,
    }


def notify_room_message_event(room_name, payload):
    from .consumers import ChatConsumer

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        ChatConsumer.build_group_name(room_name),
        {'type': 'chat_message', 'payload': payload},
    )


def notify_direct_message_event(user_a_id, user_b_id, payload):
    from .consumers import DirectChatConsumer

    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        DirectChatConsumer.build_group_name(user_a_id, user_b_id),
        {'type': 'direct_message_event', 'payload': payload},
    )


def build_history_entry(item, text_attr):
    attachment = build_attachment_payload(item)
    message_text = getattr(item, text_attr, '')
    sender = getattr(item, 'user', None) or getattr(item, 'sender', None)
    sender_profile = getattr(sender, 'chat_profile', None) if sender else None
    sender_name = ''
    sender_public_id = ''
    if sender_profile:
        sender_name = sender_profile.get_display_name()
        sender_public_id = sender_profile.public_id
    elif sender is not None:
        sender_name = getattr(sender, 'username', '') or getattr(item, 'username', '')
    else:
        sender_name = getattr(item, 'username', '')
    preview = get_message_preview_text(
        message_text,
        getattr(item, 'attachment_type', ''),
        getattr(item, 'attachment_name', ''),
        limit=80,
    )
    return {
        'preview': preview,
        'message': message_text,
        'attachment': attachment,
        'id': item.id,
        'sender_name': sender_name or '用户',
        'sender_public_id': sender_public_id,
        'created_at': getattr(item, 'timestamp', None) or getattr(item, 'created_at', None),
    }


def serialize_user_emoji(item):
    return {
        'id': item.id,
        'title': item.title or '图片表情',
        'url': item.image.url,
    }


def serialize_history_browser_items(history_info, history_images, history_files):
    items = []
    for entry in history_info:
        items.append({
            'category': 'messages',
            'title': entry.get('preview') or '空消息',
            'subtitle': entry.get('sender_name') or '用户',
            'timestamp': timezone.localtime(entry['created_at']).strftime('%m-%d %H:%M') if entry.get('created_at') else '',
            'url': '',
            'thumbnail_url': '',
            'search_text': ' '.join(filter(None, [entry.get('sender_name', ''), entry.get('preview', ''), entry.get('message', '')])),
        })
    for entry in history_images:
        attachment = entry.get('attachment') or {}
        items.append({
            'category': 'images',
            'title': attachment.get('name') or '聊天图片',
            'subtitle': entry.get('sender_name') or '用户',
            'timestamp': timezone.localtime(entry['created_at']).strftime('%m-%d %H:%M') if entry.get('created_at') else '',
            'url': attachment.get('url') or '',
            'thumbnail_url': attachment.get('url') or '',
            'search_text': ' '.join(filter(None, [entry.get('sender_name', ''), attachment.get('name', ''), entry.get('message', '')])),
        })
    for entry in history_files:
        attachment = entry.get('attachment') or {}
        items.append({
            'category': 'files',
            'title': attachment.get('name') or '聊天文件',
            'subtitle': entry.get('sender_name') or '用户',
            'timestamp': timezone.localtime(entry['created_at']).strftime('%m-%d %H:%M') if entry.get('created_at') else '',
            'url': attachment.get('url') or '',
            'thumbnail_url': '',
            'search_text': ' '.join(filter(None, [entry.get('sender_name', ''), attachment.get('name', ''), entry.get('message', '')])),
        })
    return items


def build_room_history_page_url(room):
    return reverse('room_history', kwargs={'room_name': room.name})


def build_direct_history_page_url(user):
    profile = get_or_create_chat_profile(user)
    return reverse('direct_history', kwargs={'public_id': profile.public_id})


def get_user_emoji_queryset(user):
    return UserEmoji.objects.filter(user=user).order_by('-last_used_at', '-created_at')[:24]


def mark_user_emoji_used(item):
    item.last_used_at = timezone.now()
    item.save(update_fields=['last_used_at'])


def are_friends(user, other_user):
    return Friendship.objects.filter(user=user, friend=other_user).exists()


def get_or_create_direct_conversation(user, other_user):
    ordered_users = sorted([user, other_user], key=lambda item: item.id)
    conversation, _ = DirectConversation.objects.get_or_create(user1=ordered_users[0], user2=ordered_users[1])
    DirectConversationState.objects.get_or_create(conversation=conversation, user=user)
    DirectConversationState.objects.get_or_create(conversation=conversation, user=other_user)
    return conversation


def get_or_create_room_visit_state(user, room):
    state, _ = RoomVisitState.objects.get_or_create(room=room, user=user)
    return state


def get_or_create_room_membership(room, user):
    try:
        membership, created = RoomMembership.objects.get_or_create(
            room=room,
            user=user,
            defaults={
                'is_active': True,
                'removed_at': None,
            },
        )
        if room.created_by_id == user.id and not membership.is_active:
            membership.is_active = True
            membership.removed_at = None
            membership.save(update_fields=['is_active', 'removed_at'])
        return membership, created
    except (OperationalError, ProgrammingError):
        return None, False


def get_room_membership(room, user):
    if not room or not user or not user.is_authenticated:
        return None
    return RoomMembership.objects.filter(room=room, user=user).first()


def can_manage_room_avatar(room, user, membership=None):
    if not room or not user or not user.is_authenticated:
        return False
    if room.created_by_id == user.id:
        return True
    membership = membership or get_room_membership(room, user)
    return bool(membership and membership.is_active and membership.is_admin)


def get_accessible_rooms_queryset(user):
    if not user or not user.is_authenticated:
        return Room.objects.none()
    return Room.objects.filter(
        Q(created_by=user) | Q(memberships__user=user, memberships__is_active=True)
    ).distinct()


def get_pending_room_invites_queryset(user):
    if not user or not user.is_authenticated:
        return RoomInvitation.objects.none()
    return RoomInvitation.objects.filter(
        invited_user=user,
        status=RoomInvitation.STATUS_PENDING,
    ).select_related('room', 'invited_by', 'invited_by__chat_profile')


def get_manageable_rooms_queryset(user):
    if not user or not user.is_authenticated:
        return Room.objects.none()
    return Room.objects.filter(
        Q(created_by=user) | Q(memberships__user=user, memberships__is_active=True, memberships__is_admin=True)
    ).distinct()


def can_manage_room_members(room, user, membership=None):
    return can_manage_room_avatar(room, user, membership=membership)


def get_direct_visibility_cutoff(state):
    cutoffs = [value for value in [state.cleared_at, state.deleted_at] if value]
    return max(cutoffs) if cutoffs else None


def get_visible_direct_messages(conversation, state):
    messages_qs = conversation.messages.select_related('sender', 'sender__chat_profile')
    cutoff = get_direct_visibility_cutoff(state)
    if cutoff:
        messages_qs = messages_qs.filter(created_at__gt=cutoff)
    return messages_qs


def build_room_threads(user):
    threads = []
    rooms = get_accessible_rooms_queryset(user).prefetch_related('messages')
    embed_version = '20260322n'
    for room in rooms:
        state = get_or_create_room_visit_state(user, room)
        visible_messages = room.messages.all()
        if state.deleted_at:
            visible_messages = visible_messages.filter(timestamp__gt=state.deleted_at)
        latest_message = visible_messages.order_by('-timestamp').first()
        if not latest_message and state.deleted_at:
            continue

        unread_qs = visible_messages.exclude(user=user)
        if state.last_read_at:
            unread_qs = unread_qs.filter(timestamp__gt=state.last_read_at)

        if latest_message:
            last_message_preview = get_message_preview_text(
                latest_message.message,
                latest_message.attachment_type,
                latest_message.attachment_name,
            )
            last_message_at = latest_message.timestamp
        else:
            last_message_preview = room.description or '新群聊已创建，来发第一条消息吧'
            last_message_at = room.created_at

        threads.append({
            'type': 'room',
            'name': room.name,
            'avatar_label': room.avatar,
            'avatar_url': room.avatar_url,
            'url': reverse('chat_room', args=[room.name]),
            'embed_url': f"{reverse('chat_room', args=[room.name])}?embed=1&v={embed_version}",
            'inbox_url': f"{reverse('inbox')}?thread_type=room&target={quote(room.name)}",
            'delete_url': reverse('delete_room_conversation', args=[room.name]),
            'unread_count': unread_qs.count(),
            'last_message_preview': last_message_preview,
            'last_message_at': last_message_at,
        })

    return sorted(threads, key=lambda item: item['last_message_at'], reverse=True)


def build_direct_threads(user):
    threads = []
    embed_version = '20260322n'
    conversations = DirectConversation.objects.filter(
        Q(user1=user) | Q(user2=user)
    ).select_related('user1', 'user2', 'user1__chat_profile', 'user2__chat_profile')

    for conversation in conversations:
        state, _ = DirectConversationState.objects.get_or_create(conversation=conversation, user=user)
        visible_messages = get_visible_direct_messages(conversation, state)
        latest_message = visible_messages.order_by('-created_at').first()
        if not latest_message and state.deleted_at:
            continue

        other_user = conversation.other_user(user)
        unread_qs = visible_messages.exclude(sender=user)
        if state.last_read_at:
            unread_qs = unread_qs.filter(created_at__gt=state.last_read_at)

        if latest_message:
            last_message_preview = get_message_preview_text(
                latest_message.content,
                latest_message.attachment_type,
                latest_message.attachment_name,
            )
            last_message_at = latest_message.created_at
        else:
            last_message_preview = '还没有私聊消息，发一句试试看。'
            last_message_at = conversation.created_at

        threads.append({
            'type': 'direct',
            'name': other_user.username,
            'target': getattr(getattr(other_user, 'chat_profile', None), 'public_id', ''),
            'public_id': getattr(getattr(other_user, 'chat_profile', None), 'public_id', ''),
            'display_name': getattr(getattr(other_user, 'chat_profile', None), 'get_display_name', lambda: other_user.username)(),
            'friend_id': getattr(getattr(other_user, 'chat_profile', None), 'friend_id', ''),
            'avatar_label': getattr(getattr(other_user, 'chat_profile', None), 'get_avatar_label', lambda: other_user.username[:2].upper())(),
            'avatar_url': getattr(getattr(other_user, 'chat_profile', None), 'avatar_url', ''),
            'url': get_direct_chat_url(other_user),
            'embed_url': f"{get_direct_chat_url(other_user)}?embed=1&v={embed_version}",
            'inbox_url': f"{reverse('inbox')}?thread_type=direct&target={quote(getattr(getattr(other_user, 'chat_profile', None), 'public_id', ''))}",
            'delete_url': get_direct_delete_url(other_user),
            'unread_count': unread_qs.count(),
            'last_message_preview': last_message_preview,
            'last_message_at': last_message_at,
        })

    return sorted(threads, key=lambda item: item['last_message_at'], reverse=True)


def build_room_placeholder_thread(user, room_name):
    if not user or not user.is_authenticated or not room_name:
        return None

    room = get_accessible_rooms_queryset(user).filter(name=room_name).first()
    if not room:
        return None

    embed_version = '20260322n'
    return {
        'type': 'room',
        'name': room.name,
        'avatar_label': room.avatar,
        'avatar_url': room.avatar_url,
        'url': reverse('chat_room', args=[room.name]),
        'embed_url': f"{reverse('chat_room', args=[room.name])}?embed=1&v={embed_version}",
        'inbox_url': f"{reverse('inbox')}?thread_type=room&target={quote(room.name)}",
        'delete_url': reverse('delete_room_conversation', args=[room.name]),
        'unread_count': 0,
        'last_message_preview': room.description or '新群聊已创建，来发第一条消息吧',
        'last_message_at': room.created_at,
    }


def build_direct_placeholder_thread(user, target):
    if not user or not user.is_authenticated or not target:
        return None

    friendship = Friendship.objects.filter(
        user=user,
        friend__chat_profile__public_id=target,
    ).select_related('friend', 'friend__chat_profile').first()
    if not friendship:
        return None

    other_user = friendship.friend
    profile = getattr(other_user, 'chat_profile', None)
    embed_version = '20260322n'
    return {
        'type': 'direct',
        'name': other_user.username,
        'target': getattr(profile, 'public_id', ''),
        'public_id': getattr(profile, 'public_id', ''),
        'display_name': getattr(profile, 'get_display_name', lambda: other_user.username)(),
        'friend_id': getattr(profile, 'friend_id', ''),
        'avatar_label': getattr(profile, 'get_avatar_label', lambda: other_user.username[:2].upper())(),
        'avatar_url': getattr(profile, 'avatar_url', ''),
        'url': get_direct_chat_url(other_user),
        'embed_url': f"{get_direct_chat_url(other_user)}?embed=1&v={embed_version}",
        'inbox_url': f"{reverse('inbox')}?thread_type=direct&target={quote(getattr(profile, 'public_id', ''))}",
        'delete_url': get_direct_delete_url(other_user),
        'unread_count': 0,
        'last_message_preview': '还没有私聊消息，发一句试试看。',
        'last_message_at': None,
    }


def get_inbox_context(user):
    pending_requests = FriendRequest.objects.filter(
        recipient=user,
        status=FriendRequest.STATUS_PENDING,
    ).select_related('sender', 'sender__chat_profile')
    pending_room_invites = get_pending_room_invites_queryset(user)
    pending_room_join_requests = RoomJoinRequest.objects.filter(
        room__in=get_manageable_rooms_queryset(user),
        status=RoomJoinRequest.STATUS_PENDING,
    ).select_related('room', 'requester', 'requester__chat_profile').distinct()
    rejected_room_join_requests = RoomJoinRequest.objects.filter(
        requester=user,
        status=RoomJoinRequest.STATUS_REJECTED,
    ).select_related('room')[:12]
    room_threads = build_room_threads(user)
    direct_threads = build_direct_threads(user)
    conversation_threads = sorted(room_threads + direct_threads, key=lambda item: item['last_message_at'], reverse=True)

    return {
        'pending_requests': pending_requests,
        'pending_friend_requests_count': pending_requests.count(),
        'pending_room_invites': pending_room_invites,
        'pending_room_invites_count': pending_room_invites.count(),
        'pending_room_join_requests': pending_room_join_requests,
        'pending_room_join_requests_count': pending_room_join_requests.count(),
        'rejected_room_join_requests': rejected_room_join_requests,
        'room_threads': room_threads,
        'direct_threads': direct_threads,
        'conversation_threads': conversation_threads,
        'total_unread_count': sum(item['unread_count'] for item in room_threads + direct_threads),
        'friend_requests_history': FriendRequest.objects.filter(
            recipient=user,
        ).exclude(status=FriendRequest.STATUS_PENDING).select_related('sender', 'sender__chat_profile')[:12],
    }


def build_room_member_records(room, current_user):
    member_records = []
    try:
        active_memberships = room.memberships.filter(is_active=True).select_related('user', 'user__chat_profile')
        online_user_ids = set(UserSession.objects.values_list('user_id', flat=True))

        if not active_memberships.exists():
            fallback_usernames = set(room.messages.exclude(username='').values_list('username', flat=True))
            if room.created_by:
                fallback_usernames.add(room.created_by.username)
            fallback_users = User.objects.filter(username__in=fallback_usernames)
            for linked_user in fallback_users:
                get_or_create_room_membership(room, linked_user)
            active_memberships = room.memberships.filter(is_active=True).select_related('user', 'user__chat_profile')

        for membership in active_memberships:
            linked_user = membership.user
            profile = get_or_create_chat_profile(linked_user)
            member_records.append({
                'username': linked_user.username,
                'display_name': profile.get_display_name(),
                'public_id': profile.public_id,
                'avatar_label': profile.get_avatar_label(),
                'avatar_url': profile.avatar_url,
                'friend_id': profile.friend_id,
                'is_owner': bool(room.created_by and room.created_by_id == linked_user.id),
                'is_admin': bool(membership.is_admin),
                'is_self': bool(current_user and current_user.id == linked_user.id),
                'is_online': linked_user.id in online_user_ids,
            })
    except (OperationalError, ProgrammingError):
        usernames = set(room.messages.exclude(username='').values_list('username', flat=True))
        if room.created_by:
            usernames.add(room.created_by.username)
        if current_user and current_user.is_authenticated:
            usernames.add(current_user.username)

        users_by_username = {
            item.username: item
            for item in User.objects.filter(username__in=usernames).select_related('chat_profile')
        }
        for username in sorted(usernames, key=lambda item: ((item or '').lower(), item)):
            linked_user = users_by_username.get(username)
            profile = get_or_create_chat_profile(linked_user) if linked_user else None
            member_records.append({
                'username': username,
                'display_name': profile.get_display_name() if profile else username,
                'public_id': profile.public_id if profile else '',
                'avatar_label': profile.get_avatar_label() if profile else username[:2],
                'avatar_url': profile.avatar_url if profile else '',
                'friend_id': profile.friend_id if profile else '',
                'is_owner': bool(room.created_by and room.created_by.username == username),
                'is_admin': False,
                'is_self': bool(current_user and current_user.username == username),
                'is_online': bool(linked_user and linked_user.sessions.exists()),
            })

    member_records.sort(key=lambda item: (
        not item['is_owner'],
        not item['is_self'],
        not item['is_admin'],
        item['username'].lower(),
    ))
    return member_records


@never_cache
@ensure_csrf_cookie
@csrf_protect
def login_view(request):
    """登录页面"""
    if request.user.is_authenticated:
        return redirect('chat_index')
    
    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            
            # 先登录，确保会话正确创建
            login(request, user)
            
            # 强制保存会话，确保会话键存在
            request.session.save()
            
            # 获取当前会话键
            current_session_key = request.session.session_key
            if not current_session_key:
                # 如果仍然没有会话键，手动创建
                request.session.create()
                request.session.save()
                current_session_key = request.session.session_key
            
            # 删除该用户的所有现有会话记录
            UserSession.objects.filter(user=user).delete()
            # 创建新的会话记录
            try:
                UserSession.objects.create(user=user, session_key=current_session_key)
            except Exception as e:
                # 如果创建失败，记录错误但不阻止登录
                print(f"创建 UserSession 记录时出错: {e}")
            notify_user_presence_changed(user)
            
            # 获取并保存用户地理位置信息
            try:
                ip_address = GeoIPService.get_client_ip(request)
                GeoIPService.save_user_location(user, ip_address)
            except Exception as e:
                # 如果地理位置获取失败，记录错误但不阻止登录
                print(f"获取用户地理位置时出错: {e}")
            
            # 再次保存会话，确保所有更改都被保存
            request.session.save()
            
            next_url = request.GET.get('next', 'chat_index')
            return redirect(next_url)
    else:
        form = AuthenticationForm()
    
    return render(request, 'chat/login.html', {'form': form})


@never_cache
@ensure_csrf_cookie
def register_view(request):
    """注册页面"""
    if request.user.is_authenticated:
        return redirect('chat_index')
    
    if request.method == 'POST':
        form = RegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            profile = get_or_create_chat_profile(user)
            requested_friend_id = form.cleaned_data.get('friend_id', '').strip().lower()
            if requested_friend_id:
                if UserChatProfile.objects.exclude(user=user).filter(friend_id=requested_friend_id).exists():
                    form.add_error('friend_id', '这个好友 ID 已经被使用了')
                    user.delete()
                    return render(request, 'chat/register.html', {'form': form})
                profile.friend_id = requested_friend_id
                profile.save(update_fields=['friend_id'])
            login(request, user)
            
            # 强制保存会话，确保会话键存在
            request.session.save()
            
            # 获取当前会话键
            current_session_key = request.session.session_key
            if not current_session_key:
                # 如果仍然没有会话键，手动创建
                request.session.create()
                request.session.save()
                current_session_key = request.session.session_key
            
            # 创建 UserSession 记录
            try:
                UserSession.objects.create(user=user, session_key=current_session_key)
            except Exception as e:
                # 如果创建失败，记录错误但不阻止登录
                print(f"创建 UserSession 记录时出错: {e}")
            notify_user_presence_changed(user)
            
            # 获取并保存用户地理位置信息
            try:
                ip_address = GeoIPService.get_client_ip(request)
                GeoIPService.save_user_location(user, ip_address)
            except Exception as e:
                # 如果地理位置获取失败，记录错误但不阻止登录
                print(f"获取用户地理位置时出错: {e}")
            
            # 再次保存会话，确保所有更改都被保存
            request.session.save()
            
            return redirect('chat_index')
    else:
        form = RegistrationForm()
    
    return render(request, 'chat/register.html', {'form': form})


@login_required
def index(request):
    """聊天室首页"""
    get_or_create_chat_profile(request.user)

    if request.method == 'POST':
        room_name = request.POST.get('room_name', '').strip()
        room_avatar = request.POST.get('room_avatar', '💬').strip() or '💬'
        room_description = request.POST.get('room_description', '').strip()

        if room_avatar not in DEFAULT_ROOM_AVATARS:
            room_avatar = '💬'
        if not room_description:
            room_description = '一起聊聊吧'

        if room_name:
            if Room.objects.filter(name=room_name).exists():
                messages.error(request, '房间已存在')
            else:
                Room.objects.create(
                    name=room_name,
                    avatar=room_avatar,
                    description=room_description[:120],
                    created_by=request.user,
                )
                room = Room.objects.get(name=room_name)
                get_or_create_room_membership(room, request.user)
                messages.success(request, f'房间 "{room_name}" 创建成功')
        return redirect('chat_index')
    
    rooms = get_accessible_rooms_queryset(request.user)
    profile = get_or_create_chat_profile(request.user)
    incoming_friend_requests = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).select_related('sender', 'sender__chat_profile')[:5]
    inbox_context = get_inbox_context(request.user)
    room_unread_map = {item['name']: item['unread_count'] for item in inbox_context['room_threads']}
    direct_unread_map = {item.get('target') or item['name']: item['unread_count'] for item in inbox_context['direct_threads']}
    room_items = [
        {
            'room': room,
            'unread_count': room_unread_map.get(room.name, 0),
            'inbox_url': next((item['inbox_url'] for item in inbox_context['room_threads'] if item['name'] == room.name), reverse('inbox')),
        }
        for room in rooms
    ]
    friends = Friendship.objects.filter(user=request.user).select_related('friend', 'friend__chat_profile')
    friend_items = [
        {
            'friendship': item,
            'unread_count': direct_unread_map.get(item.friend.chat_profile.public_id, 0),
            'inbox_url': next((thread['inbox_url'] for thread in inbox_context['direct_threads'] if (thread.get('target') or thread['name']) == item.friend.chat_profile.public_id), reverse('inbox')),
        }
        for item in friends
    ]
    active_thread = None
    active_type = request.GET.get('thread_type', '').strip()
    active_target = request.GET.get('target', '').strip()
    if active_type and active_target:
        active_thread = next(
            (
                item for item in inbox_context['conversation_threads']
                if item['type'] == active_type and (item.get('target') or item['name']) == active_target
            ),
            None,
        )
        if not active_thread:
            if active_type == 'direct':
                active_thread = build_direct_placeholder_thread(request.user, active_target)
            elif active_type == 'room':
                active_thread = build_room_placeholder_thread(request.user, active_target)

    return render(request, 'chat/index.html', {
        'rooms': rooms,
        'room_items': room_items,
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'user': request.user,
        'chat_profile': profile,
        'pending_friend_requests_count': inbox_context['pending_friend_requests_count'],
        'pending_room_invites_count': inbox_context['pending_room_invites_count'],
        'pending_room_join_requests_count': inbox_context['pending_room_join_requests_count'],
        'inbox_badge_count': inbox_context['pending_friend_requests_count'] + inbox_context['pending_room_invites_count'] + inbox_context['pending_room_join_requests_count'],
        'incoming_friend_requests': incoming_friend_requests,
        'friends': friends,
        'friend_items': friend_items,
        'friends_count': friends.count(),
        'conversation_threads': inbox_context['conversation_threads'],
        'active_thread': active_thread,
        'active_target': active_target,
    })


@login_required
def delete_room(request, room_name):
    """删除房间"""
    try:
        room = Room.objects.get(name=room_name)
        if room.created_by == request.user:
            room.delete()
            messages.success(request, f'房间 "{room_name}" 已删除')
        else:
            messages.error(request, '只有房主才能删除房间')
    except Room.DoesNotExist:
        messages.error(request, '房间不存在')
    
    return redirect('chat_index')


@login_required
@xframe_options_sameorigin
def room(request, room_name):
    """具体聊天室页面"""
    embed_mode = request.GET.get('embed') == '1'
    room_hub_url = get_room_hub_url(room_name)
    user_profile_next_url = room_hub_url

    def redirect_to_room(target_room_name):
        target_url = reverse('chat_room', args=[target_room_name])
        if embed_mode:
            target_url = f'{target_url}?embed=1'
        return redirect(target_url)

    try:
        room = Room.objects.get(name=room_name)
        room_membership = get_room_membership(room, request.user)
        is_owner = room.created_by == request.user
        is_admin = bool(room_membership and room_membership.is_active and room_membership.is_admin)
    except Room.DoesNotExist:
        is_owner = False
        is_admin = False
        room_membership = None
        room = None

    if not room:
        messages.error(request, '房间不存在')
        return redirect('chat_index')

    has_membership_record = bool(room_membership)
    if not is_owner and not has_membership_record:
        messages.error(request, '你还不是这个群聊的成员，暂时不能查看群内容')
        return redirect('chat_index')

    try:
        GeoIPService.refresh_user_location_if_needed(request.user)
    except Exception as e:
        print(f"刷新用户地理位置时出错: {e}")

    visit_state = get_or_create_room_visit_state(request.user, room)
    if visit_state.deleted_at is not None:
        visit_state.deleted_at = None
        visit_state.save(update_fields=['deleted_at'])

    if request.method == 'POST':
        action = request.POST.get('action', 'room_settings')

        if action == 'room_avatar':
            if not can_manage_room_avatar(room, request.user, room_membership):
                messages.error(request, '只有房主或群管理员才能修改群头像')
                return redirect_to_room(room.name)

            next_avatar = request.POST.get('room_avatar', room.avatar).strip() or room.avatar
            if next_avatar not in DEFAULT_ROOM_AVATARS:
                next_avatar = '💬'

            remove_room_avatar = request.POST.get('remove_room_avatar') == 'on'
            uploaded_room_avatar = request.FILES.get('room_avatar_image')
            update_fields = ['avatar']
            room.avatar = next_avatar

            if remove_room_avatar and room.avatar_image:
                room.delete_avatar_image_file()
                room.avatar_image = None
                update_fields.append('avatar_image')

            if uploaded_room_avatar:
                try:
                    optimized_room_avatar = compress_room_avatar_upload(uploaded_room_avatar, room.name)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect_to_room(room.name)

                if room.avatar_image:
                    room.delete_avatar_image_file()
                room.avatar_image.save(optimized_room_avatar.name, optimized_room_avatar, save=False)
                if 'avatar_image' not in update_fields:
                    update_fields.append('avatar_image')

            room.save(update_fields=update_fields)
            messages.success(request, '群头像已更新')
            return redirect_to_room(room.name)

        if action == 'set_admin':
            if not is_owner:
                messages.error(request, '只有房主才能设置群管理员')
                return redirect_to_room(room.name)

            target_username = request.POST.get('target_username', '').strip()
            try:
                target_user = User.objects.get(username=target_username)
                target_membership, _ = RoomMembership.objects.get_or_create(
                    room=room,
                    user=target_user,
                    defaults={'is_active': True, 'removed_at': None},
                )
            except User.DoesNotExist:
                messages.error(request, '目标成员不存在')
                return redirect_to_room(room.name)

            if room.created_by_id == target_user.id:
                messages.error(request, '房主不需要设置为管理员')
                return redirect_to_room(room.name)
            if not target_membership.is_active:
                messages.error(request, '只能设置仍在群内的成员为管理员')
                return redirect_to_room(room.name)
            if target_membership.is_admin:
                messages.info(request, f'{target_username} 已经是群管理员')
                return redirect_to_room(room.name)

            admin_count = room.memberships.filter(is_active=True, is_admin=True).count()
            if admin_count >= MAX_ROOM_ADMIN_COUNT:
                messages.error(request, f'群管理员最多只能设置 {MAX_ROOM_ADMIN_COUNT} 个')
                return redirect_to_room(room.name)

            target_membership.is_admin = True
            target_membership.save(update_fields=['is_admin'])
            messages.success(request, f'已将 {target_username} 设为群管理员')
            return redirect_to_room(room.name)

        if action == 'revoke_admin':
            if not is_owner:
                messages.error(request, '只有房主才能取消群管理员')
                return redirect_to_room(room.name)

            target_username = request.POST.get('target_username', '').strip()
            try:
                target_user = User.objects.get(username=target_username)
                target_membership = RoomMembership.objects.get(room=room, user=target_user)
            except (User.DoesNotExist, RoomMembership.DoesNotExist):
                messages.error(request, '目标管理员不存在')
                return redirect_to_room(room.name)

            if not target_membership.is_admin:
                messages.info(request, f'{target_username} 目前不是群管理员')
                return redirect_to_room(room.name)

            target_membership.is_admin = False
            target_membership.save(update_fields=['is_admin'])
            messages.success(request, f'已取消 {target_username} 的群管理员身份')
            return redirect_to_room(room.name)

        if not is_owner:
            messages.error(request, '只有房主才能编辑房间资料')
            return redirect_to_room(room.name)

        new_room_name = request.POST.get('room_name', room.name).strip() or room.name
        new_join_policy = request.POST.get('join_policy', room.join_policy).strip() or room.join_policy
        if new_join_policy not in dict(Room.JOIN_POLICY_CHOICES):
            new_join_policy = room.join_policy
        room.description = request.POST.get('room_description', room.description).strip()[:120] or '一起聊聊吧'
        room.name = new_room_name
        room.join_policy = new_join_policy

        try:
            room.save()
        except IntegrityError:
            messages.error(request, '这个房间名已经被用了')
            return redirect_to_room(room_name)
        messages.success(request, '房间资料已更新')
        return redirect_to_room(room.name)

    chat_profile = get_or_create_chat_profile(request.user)
    room_membership = room_membership or get_room_membership(room, request.user)
    room_member_records = build_room_member_records(room, request.user)
    visible_room_messages = room.messages.select_related('user', 'user__chat_profile').order_by('-timestamp')[:30]
    room_history_info = [build_history_entry(item, 'message') for item in visible_room_messages]
    room_history_images = [entry for entry in room_history_info if entry['attachment'] and entry['attachment']['kind'] == 'image']
    room_history_files = [entry for entry in room_history_info if entry['attachment'] and entry['attachment']['kind'] == 'file']
    visit_state = get_or_create_room_visit_state(request.user, room)
    visit_state.last_read_at = timezone.now()
    visit_state.save(update_fields=['last_read_at'])
    pending_friend_requests_count = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).count()
    inviteable_friends = Friendship.objects.filter(user=request.user).exclude(
        friend__room_memberships__room=room,
        friend__room_memberships__is_active=True,
    ).select_related('friend', 'friend__chat_profile').distinct()
    return render(request, 'chat/room.html', {
        'room': room,
        'room_hub_url': room_hub_url,
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'room_admin_count': room.memberships.filter(is_active=True, is_admin=True).count(),
        'room_name': room.name,
        'room_name_json': mark_safe(json.dumps(room.name)),
        'room_total_members': room.total_members,
        'room_online_members': sum(1 for item in room_member_records if item.get('is_online')),
        'room_members_json': mark_safe(json.dumps(room_member_records)),
        'is_removed_from_room': bool(room_membership and (not room_membership.is_active) and room.created_by_id != request.user.id),
        'is_owner': is_owner,
        'is_admin': is_admin,
        'can_manage_room_avatar': can_manage_room_avatar(room, request.user, room_membership),
        'max_room_admin_count': MAX_ROOM_ADMIN_COUNT,
        'chat_profile': chat_profile,
        'chat_profile_payload_json': mark_safe(json.dumps(chat_profile.to_payload())),
        'chat_theme_choices': CHAT_COLOR_THEMES.items(),
        'chat_style_choices': CHAT_BUBBLE_STYLES.items(),
        'inviteable_friends': inviteable_friends,
        'room_history_info': room_history_info,
        'room_history_images': room_history_images,
        'room_history_files': room_history_files,
        'room_history_browser_json': mark_safe(json.dumps(
            serialize_history_browser_items(room_history_info, room_history_images, room_history_files)
        )),
        'builtin_emojis': BUILTIN_EMOJIS,
        'user_emojis': list(get_user_emoji_queryset(request.user)),
        'pending_join_requests': RoomJoinRequest.objects.filter(room=room, status=RoomJoinRequest.STATUS_PENDING).select_related('requester', 'requester__chat_profile'),
        'pending_friend_requests_count': pending_friend_requests_count,
        'inbox_badge_count': pending_friend_requests_count + get_pending_room_invites_queryset(request.user).count(),
        'embed_mode': embed_mode,
        'user_profile_next_url': user_profile_next_url,
        'room_history_url': build_room_history_page_url(room),
    })


@login_required
@xframe_options_sameorigin
def room_history(request, room_name):
    embed_mode = request.GET.get('embed') == '1'
    try:
        room = Room.objects.get(name=room_name)
        room_membership = get_room_membership(room, request.user)
        is_owner = room.created_by == request.user
    except Room.DoesNotExist:
        room = None
        room_membership = None
        is_owner = False

    if not room:
        messages.error(request, '房间不存在')
        return redirect('chat_index')

    if not is_owner and not room_membership:
        messages.error(request, '你还不是这个群聊的成员，暂时不能查看群内容')
        return redirect('chat_index')

    chat_profile = get_or_create_chat_profile(request.user)
    visible_room_messages = list(
        room.messages.select_related('user', 'user__chat_profile').order_by('-timestamp')
    )
    history_info = [build_history_entry(item, 'message') for item in visible_room_messages]
    history_images = [entry for entry in history_info if entry['attachment'] and entry['attachment']['kind'] == 'image']
    history_files = [entry for entry in history_info if entry['attachment'] and entry['attachment']['kind'] == 'file']
    next_url = get_safe_next_url(request, fallback_name='chat_index')
    if next_url == reverse('chat_index'):
        next_url = reverse('chat_room', args=[room.name])

    return render(request, 'chat/history_browser.html', {
        'chat_profile': chat_profile,
        'history_title': f'{room.name} 的聊天记录',
        'history_description': '群聊消息、图片和文件都会集中在这里，支持搜索、分页和看图浏览。',
        'history_scope_label': '群聊记录',
        'history_target_name': room.name,
        'history_items_json': mark_safe(json.dumps(
            serialize_history_browser_items(history_info, history_images, history_files)
        )),
        'history_back_url': next_url,
        'history_back_label': '返回群聊',
        'history_avatar_label': room.avatar,
        'history_avatar_url': room.avatar_url,
        'history_meta': f'群 ID {room.room_id}',
        'embed_mode': embed_mode,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
    })


@login_required
def profile_settings(request):
    """个人聊天设置"""
    try:
        GeoIPService.refresh_user_location_if_needed(request.user)
    except Exception as e:
        print(f"刷新用户地理位置时出错: {e}")

    chat_profile = get_or_create_chat_profile(request.user)
    password_form = ProfilePasswordChangeForm(request.user)
    profile_settings_url = reverse('profile_settings')

    if request.method == 'POST':
        form_type = request.POST.get('form_type', 'profile')
        if form_type == 'password':
            password_form = ProfilePasswordChangeForm(request.user, request.POST)
            if password_form.is_valid():
                updated_user = password_form.save()
                update_session_auth_hash(request, updated_user)
                messages.success(request, '密码已更新')
                return redirect('profile_settings')
            messages.error(request, '密码修改失败，请检查输入内容')
        else:
            try:
                normalized_username = validate_username_value(
                    request.POST.get('username', ''),
                    exclude_user_id=request.user.id,
                )
            except forms.ValidationError as exc:
                messages.error(request, exc.messages[0])
                return redirect(profile_settings_url)

            requested_friend_id = request.POST.get('friend_id', '').strip().lower()
            if requested_friend_id and UserChatProfile.objects.exclude(user=request.user).filter(friend_id=requested_friend_id).exists():
                messages.error(request, '这个好友 ID 已经被别人使用了')
                return redirect(profile_settings_url)

            if requested_friend_id and (len(requested_friend_id) < 8 or len(requested_friend_id) > 11):
                messages.error(request, '好友 ID 长度需要在 8 到 11 位之间')
                return redirect(profile_settings_url)
            if requested_friend_id and not all(ch.isalnum() or ch == '_' for ch in requested_friend_id):
                messages.error(request, '好友 ID 只能包含小写字母、数字或下划线')
                return redirect(profile_settings_url)

            old_username = request.user.username
            username_changed = normalized_username != old_username

            request.user.username = normalized_username
            request.user.save(update_fields=['username'])

            if username_changed:
                UsernameAlias.objects.update_or_create(
                    user=request.user,
                    username=old_username,
                )
                Message.objects.filter(user=request.user).update(username=normalized_username)

            chat_profile.friend_id = requested_friend_id or UserChatProfile.generate_unique_friend_id(
                normalized_username,
                exclude_user_id=request.user.id,
            )
            chat_profile.display_name = request.POST.get('display_name', '').strip()[:40]
            chat_profile.avatar_label = request.POST.get('avatar_label', '').strip()[:24]
            chat_profile.bio = request.POST.get('bio', '').strip()[:160]
            color_theme = request.POST.get('color_theme', DEFAULT_CHAT_THEME).strip()
            bubble_style = request.POST.get('bubble_style', DEFAULT_CHAT_STYLE).strip()
            chat_profile.color_theme = color_theme if color_theme in CHAT_COLOR_THEMES else DEFAULT_CHAT_THEME
            chat_profile.bubble_style = bubble_style if bubble_style in CHAT_BUBBLE_STYLES else DEFAULT_CHAT_STYLE
            chat_profile.show_location = request.POST.get('show_location') == 'on'
            remove_avatar_image = request.POST.get('remove_avatar_image') == 'on'
            uploaded_avatar = request.FILES.get('avatar_image')
            update_fields = ['friend_id', 'display_name', 'avatar_label', 'bio', 'color_theme', 'bubble_style', 'show_location']

            if remove_avatar_image and chat_profile.avatar_image:
                chat_profile.delete_avatar_image_file()
                chat_profile.avatar_image = None
                update_fields.append('avatar_image')

            if uploaded_avatar:
                try:
                    optimized_avatar = compress_avatar_upload(uploaded_avatar, normalized_username)
                except ValueError as exc:
                    messages.error(request, str(exc))
                    return redirect(profile_settings_url)

                if chat_profile.avatar_image:
                    chat_profile.delete_avatar_image_file()
                chat_profile.avatar_image.save(optimized_avatar.name, optimized_avatar, save=False)
                if 'avatar_image' not in update_fields:
                    update_fields.append('avatar_image')

            chat_profile.save(update_fields=update_fields)
            if username_changed:
                messages.success(request, '个人聊天设置已更新，用户名也已同步修改')
            else:
                messages.success(request, '个人聊天设置已更新')
            return redirect('profile_settings')

    return render(request, 'chat/profile.html', {
        'chat_profile': chat_profile,
        'chat_theme_choices': CHAT_COLOR_THEMES.items(),
        'chat_style_choices': CHAT_BUBBLE_STYLES.items(),
        'chat_theme_choices_json': mark_safe(json.dumps(CHAT_COLOR_THEMES)),
        'chat_style_choices_json': mark_safe(json.dumps(CHAT_BUBBLE_STYLES)),
        'default_chat_theme': DEFAULT_CHAT_THEME,
        'default_chat_style': DEFAULT_CHAT_STYLE,
        'current_location': getattr(request.user, 'location', None),
        'user': request.user,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
        'friendships': Friendship.objects.filter(user=request.user).select_related('friend', 'friend__chat_profile'),
        'password_form': password_form,
        'password_help_html': mark_safe(password_validators_help_text_html()),
    })


@login_required
@require_POST
def update_precise_location(request):
    """通过浏览器经纬度更新用户更精确的位置"""
    try:
        payload = json.loads(request.body.decode('utf-8'))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

    latitude = payload.get('latitude')
    longitude = payload.get('longitude')
    if latitude is None or longitude is None:
        return JsonResponse({'ok': False, 'error': 'missing_coordinates'}, status=400)

    try:
        latitude = float(latitude)
        longitude = float(longitude)
    except (TypeError, ValueError):
        return JsonResponse({'ok': False, 'error': 'invalid_coordinates'}, status=400)

    ip_address = GeoIPService.get_client_ip(request) or ''
    success = GeoIPService.save_precise_user_location(request.user, latitude, longitude, ip_address=ip_address)
    if not success:
        return JsonResponse({'ok': False, 'error': 'reverse_geocode_failed'}, status=502)

    current_location = getattr(request.user, 'location', None)
    return JsonResponse({
        'ok': True,
        'location': current_location.display_label if current_location else '',
    })


@login_required
def inbox(request):
    context = get_inbox_context(request.user)
    active_thread = None
    active_type = request.GET.get('thread_type', '').strip()
    active_target = request.GET.get('target', '').strip()
    if active_type and active_target:
        active_thread = next(
            (item for item in context['conversation_threads'] if item['type'] == active_type and item['name'] == active_target),
            None,
        )
    if not active_thread and context['conversation_threads']:
        active_thread = context['conversation_threads'][0]
    context.update({
        'chat_profile': get_or_create_chat_profile(request.user),
        'friends': Friendship.objects.filter(user=request.user).select_related('friend', 'friend__chat_profile'),
        'active_thread': active_thread,
    })
    return render(request, 'chat/inbox.html', context)


@login_required
def inbox_summary(request):
    context = get_inbox_context(request.user)
    return JsonResponse({
        'pending_friend_requests_count': context['pending_friend_requests_count'],
        'pending_room_invites_count': context['pending_room_invites_count'],
        'pending_room_join_requests_count': context['pending_room_join_requests_count'],
        'total_unread_count': context['total_unread_count'],
        'room_threads': [
            {
                'type': item['type'],
                'name': item['name'],
                'url': item['url'],
                'embed_url': item['embed_url'],
                'delete_url': item['delete_url'],
                'avatar_label': item['avatar_label'],
                'avatar_url': item['avatar_url'],
                'unread_count': item['unread_count'],
                'preview': item['last_message_preview'],
                'timestamp': timezone.localtime(item['last_message_at']).strftime('%m-%d %H:%M') if item['last_message_at'] else '',
            }
            for item in context['room_threads']
        ],
        'direct_threads': [
            {
                'type': item['type'],
                'name': item['name'],
                'target': item.get('target') or item['name'],
                'public_id': item.get('public_id', ''),
                'display_name': item.get('display_name', item['name']),
                'url': item['url'],
                'embed_url': item['embed_url'],
                'delete_url': item['delete_url'],
                'friend_id': item['friend_id'],
                'avatar_label': item['avatar_label'],
                'avatar_url': item['avatar_url'],
                'unread_count': item['unread_count'],
                'preview': item['last_message_preview'],
                'timestamp': timezone.localtime(item['last_message_at']).strftime('%m-%d %H:%M') if item['last_message_at'] else '',
            }
            for item in context['direct_threads']
        ],
    })


@login_required
@require_POST
def mark_room_read(request, room_name):
    try:
        room = Room.objects.get(name=room_name)
    except Room.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'room_not_found'}, status=404)

    state = get_or_create_room_visit_state(request.user, room)
    state.last_read_at = timezone.now()
    state.deleted_at = None
    state.save(update_fields=['last_read_at', 'deleted_at'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def delete_room_conversation(request, room_name):
    try:
        room = Room.objects.get(name=room_name)
    except Room.DoesNotExist:
        messages.error(request, '群聊不存在')
        next_url = request.POST.get('next')
        if next_url:
            return redirect(next_url)
        return redirect('chat_index')

    state = get_or_create_room_visit_state(request.user, room)
    now = timezone.now()
    state.deleted_at = now
    state.last_read_at = now
    state.save(update_fields=['deleted_at', 'last_read_at'])
    messages.success(request, f'已从你的会话列表移除群聊「{room.name}」')
    next_url = request.POST.get('next')
    if next_url:
        return redirect(next_url)
    return redirect('chat_index')


@login_required
@require_POST
def mark_direct_read(request, public_id):
    try:
        other_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)

    conversation = get_or_create_direct_conversation(request.user, other_user)
    state, _ = DirectConversationState.objects.get_or_create(conversation=conversation, user=request.user)
    state.deleted_at = None
    state.last_read_at = timezone.now()
    state.save(update_fields=['deleted_at', 'last_read_at'])
    return JsonResponse({'ok': True})


@login_required
@require_POST
def register_mobile_device(request):
    payload = get_json_request_data(request)
    if payload is None:
        logger.warning('Mobile device registration failed: invalid JSON payload for user %s', request.user.id)
        return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

    token = (payload.get('token') or '').strip()
    platform = (payload.get('platform') or MobileDevice.PLATFORM_ANDROID).strip().lower()
    device_id = (payload.get('device_id') or '').strip()
    device_name = (payload.get('device_name') or '').strip()
    app_version = (payload.get('app_version') or '').strip()

    if not token:
        logger.warning('Mobile device registration failed: missing token for user %s', request.user.id)
        return JsonResponse({'ok': False, 'error': 'missing_token'}, status=400)
    if platform not in {choice[0] for choice in MobileDevice.PLATFORM_CHOICES}:
        logger.warning('Mobile device registration failed: invalid platform %s for user %s', platform, request.user.id)
        return JsonResponse({'ok': False, 'error': 'invalid_platform'}, status=400)

    device, _ = MobileDevice.objects.update_or_create(
        token=token,
        defaults={
            'user': request.user,
            'platform': platform,
            'device_id': device_id[:128],
            'device_name': device_name[:120],
            'app_version': app_version[:40],
            'notifications_enabled': True,
        },
    )
    logger.info(
        'Registered mobile device %s for user %s on platform %s',
        device.id,
        request.user.id,
        device.platform,
    )
    return JsonResponse({
        'ok': True,
        'device': {
            'id': device.id,
            'platform': device.platform,
            'device_name': device.device_name,
            'app_version': device.app_version,
            'notifications_enabled': device.notifications_enabled,
        },
    })


@login_required
@require_POST
def unregister_mobile_device(request):
    payload = get_json_request_data(request)
    if payload is None:
        logger.warning('Mobile device unregister failed: invalid JSON payload for user %s', request.user.id)
        return JsonResponse({'ok': False, 'error': 'invalid_json'}, status=400)

    token = (payload.get('token') or '').strip()
    if not token:
        logger.warning('Mobile device unregister failed: missing token for user %s', request.user.id)
        return JsonResponse({'ok': False, 'error': 'missing_token'}, status=400)

    updated = MobileDevice.objects.filter(user=request.user, token=token).update(notifications_enabled=False)
    if not updated:
        logger.warning('Mobile device unregister failed: token not found for user %s', request.user.id)
        return JsonResponse({'ok': False, 'error': 'device_not_found'}, status=404)
    logger.info('Disabled mobile device token for user %s', request.user.id)
    return JsonResponse({'ok': True})


@login_required
@require_POST
def upload_room_attachment(request, room_name):
    try:
        room = Room.objects.get(name=room_name)
    except Room.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'room_not_found'}, status=404)

    membership = get_room_membership(room, request.user)
    if room.created_by != request.user and not (membership and membership.is_active):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'ok': False, 'error': 'missing_file'}, status=400)

    try:
        attachment_data = prepare_chat_attachment(uploaded_file, f'{room.name}_{request.user.username}')
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    profile = get_or_create_chat_profile(request.user)
    location_label = ''
    if profile.show_location and hasattr(request.user, 'location'):
        location_label = request.user.location.display_label

    message = Message(
        room=room,
        user=request.user,
        username=request.user.username,
        message='',
        message_type='chat',
        location_label=location_label,
        attachment_type=attachment_data['attachment_type'],
        attachment_name=attachment_data['attachment_name'],
        attachment_mime=attachment_data['attachment_mime'],
        attachment_size=attachment_data['attachment_size'],
    )
    message.attachment.save(attachment_data['file'].name, attachment_data['file'], save=False)
    if attachment_data['attachment_type'] == 'video':
        thumbnail_file = try_generate_video_thumbnail(message.attachment, attachment_data['attachment_name'])
        if thumbnail_file:
            message.attachment_thumbnail.save(thumbnail_file.name, thumbnail_file, save=False)
    message.save()

    payload = serialize_room_message_payload(message, profile.to_payload())
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        build_room_group_name(room.name),
        {
            'type': 'chat_message',
            'payload': payload,
        }
    )
    return JsonResponse({'ok': True, 'message': payload})


@login_required
@require_POST
def upload_direct_attachment(request, public_id):
    try:
        other_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)

    if other_user == request.user or not are_friends(request.user, other_user):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'ok': False, 'error': 'missing_file'}, status=400)

    try:
        attachment_data = prepare_chat_attachment(uploaded_file, f'{request.user.username}_{other_user.username}')
    except ValueError as exc:
        return JsonResponse({'ok': False, 'error': str(exc)}, status=400)

    conversation = get_or_create_direct_conversation(request.user, other_user)
    DirectConversationState.objects.get_or_create(conversation=conversation, user=request.user)
    DirectConversationState.objects.get_or_create(conversation=conversation, user=other_user)

    message = DirectMessage(
        conversation=conversation,
        sender=request.user,
        content='',
        attachment_type=attachment_data['attachment_type'],
        attachment_name=attachment_data['attachment_name'],
        attachment_mime=attachment_data['attachment_mime'],
        attachment_size=attachment_data['attachment_size'],
    )
    message.attachment.save(attachment_data['file'].name, attachment_data['file'], save=False)
    if attachment_data['attachment_type'] == 'video':
        thumbnail_file = try_generate_video_thumbnail(message.attachment, attachment_data['attachment_name'])
        if thumbnail_file:
            message.attachment_thumbnail.save(thumbnail_file.name, thumbnail_file, save=False)
    message.save()

    profile = get_or_create_chat_profile(request.user)
    payload = serialize_direct_message_payload(message, profile.to_payload())
    channel_layer = get_channel_layer()
    from .consumers import DirectChatConsumer

    async_to_sync(channel_layer.group_send)(
        DirectChatConsumer.build_group_name(request.user.id, other_user.id),
        {
            'type': 'direct_message_event',
            'payload': payload,
        }
    )
    return JsonResponse({'ok': True, 'message': payload})


@login_required
@require_POST
def recall_room_message(request, room_name, message_id):
    try:
        room = Room.objects.get(name=room_name)
        message = Message.objects.select_related('user').get(pk=message_id, room=room)
    except (Room.DoesNotExist, Message.DoesNotExist):
        return JsonResponse({'ok': False, 'error': 'message_not_found'}, status=404)

    membership = get_room_membership(room, request.user)
    if room.created_by != request.user and not (membership and membership.is_active):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    if message.user_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    if not can_recall_message(message.timestamp):
        return JsonResponse({'ok': False, 'error': 'recall_window_expired'}, status=400)

    message.delete_attachment_files()
    message.message = '撤回了一条消息'
    message.message_type = 'chat'
    message.attachment = None
    message.attachment_thumbnail = None
    message.attachment_type = 'text'
    message.attachment_name = ''
    message.attachment_mime = ''
    message.attachment_size = 0
    message.save(update_fields=['message', 'message_type', 'attachment', 'attachment_thumbnail', 'attachment_type', 'attachment_name', 'attachment_mime', 'attachment_size'])

    appearance = get_or_create_chat_profile(request.user).to_payload()
    payload = serialize_room_message_payload(message, appearance)
    payload['type'] = 'message_updated'
    notify_room_message_event(room.name, payload)
    return JsonResponse({'ok': True, 'message': payload})


@login_required
@require_POST
def delete_room_message(request, room_name, message_id):
    try:
        room = Room.objects.get(name=room_name)
        message = Message.objects.select_related('user').get(pk=message_id, room=room)
    except (Room.DoesNotExist, Message.DoesNotExist):
        return JsonResponse({'ok': False, 'error': 'message_not_found'}, status=404)

    membership = get_room_membership(room, request.user)
    if room.created_by != request.user and not (membership and membership.is_active):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    if message.user_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    message.delete_attachment_files()
    deleted_id = message.id
    message.delete()
    payload = build_room_message_delete_payload(deleted_id)
    notify_room_message_event(room.name, payload)
    return JsonResponse({'ok': True, 'id': deleted_id})


@login_required
@require_POST
def recall_direct_message(request, public_id, message_id):
    try:
        other_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)
    if other_user == request.user or not are_friends(request.user, other_user):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    conversation = get_or_create_direct_conversation(request.user, other_user)
    try:
        message = DirectMessage.objects.select_related('sender').get(pk=message_id, conversation=conversation)
    except DirectMessage.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'message_not_found'}, status=404)
    if message.sender_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    if not can_recall_message(message.created_at):
        return JsonResponse({'ok': False, 'error': 'recall_window_expired'}, status=400)

    message.delete_attachment_files()
    message.content = '撤回了一条消息'
    message.attachment = None
    message.attachment_thumbnail = None
    message.attachment_type = 'text'
    message.attachment_name = ''
    message.attachment_mime = ''
    message.attachment_size = 0
    message.save(update_fields=['content', 'attachment', 'attachment_thumbnail', 'attachment_type', 'attachment_name', 'attachment_mime', 'attachment_size'])

    appearance = get_or_create_chat_profile(request.user).to_payload()
    payload = serialize_direct_message_payload(message, appearance)
    payload['type'] = 'message_updated'
    notify_direct_message_event(request.user.id, other_user.id, payload)
    return JsonResponse({'ok': True, 'message': payload})


@login_required
@require_POST
def delete_direct_message(request, public_id, message_id):
    try:
        other_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)
    if other_user == request.user or not are_friends(request.user, other_user):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    conversation = get_or_create_direct_conversation(request.user, other_user)
    try:
        message = DirectMessage.objects.select_related('sender').get(pk=message_id, conversation=conversation)
    except DirectMessage.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'message_not_found'}, status=404)
    if message.sender_id != request.user.id:
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    message.delete_attachment_files()
    deleted_id = message.id
    message.delete()
    payload = build_direct_message_delete_payload(deleted_id)
    notify_direct_message_event(request.user.id, other_user.id, payload)
    return JsonResponse({'ok': True, 'id': deleted_id})


@login_required
@require_POST
def upload_user_emoji(request):
    uploaded_files = request.FILES.getlist('files') or request.FILES.getlist('file')
    if not uploaded_files:
        single_file = request.FILES.get('file')
        if single_file:
            uploaded_files = [single_file]
    if not uploaded_files:
        return JsonResponse({'ok': False, 'error': 'missing_file'}, status=400)
    emojis = []
    for uploaded_file in uploaded_files:
        try:
            emoji = create_user_emoji_from_upload(request.user, uploaded_file, request.POST.get('title', ''))
        except ValueError as exc:
            return JsonResponse({'ok': False, 'error': str(exc)}, status=400)
        emojis.append(serialize_user_emoji(emoji))
    response_payload = {
        'ok': True,
        'emojis': emojis,
    }
    if emojis:
        response_payload['emoji'] = emojis[0]
    return JsonResponse(response_payload)


@login_required
@require_POST
def favorite_room_image_emoji(request, room_name, message_id):
    try:
        room = Room.objects.get(name=room_name)
        message = Message.objects.get(pk=message_id, room=room)
    except (Room.DoesNotExist, Message.DoesNotExist):
        return JsonResponse({'ok': False, 'error': 'message_not_found'}, status=404)

    membership = get_room_membership(room, request.user)
    if room.created_by != request.user and not (membership and membership.is_active):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)
    if message.attachment_type != 'image' or not message.attachment:
        return JsonResponse({'ok': False, 'error': 'image_only'}, status=400)

    emoji = clone_attachment_as_emoji(request.user, message.attachment, message.attachment_name)
    return JsonResponse({'ok': True, 'emoji': serialize_user_emoji(emoji)})


@login_required
@require_POST
def favorite_direct_image_emoji(request, public_id, message_id):
    try:
        other_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)
    if other_user == request.user or not are_friends(request.user, other_user):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    conversation = get_or_create_direct_conversation(request.user, other_user)
    try:
        message = DirectMessage.objects.get(pk=message_id, conversation=conversation)
    except DirectMessage.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'message_not_found'}, status=404)

    if message.attachment_type != 'image' or not message.attachment:
        return JsonResponse({'ok': False, 'error': 'image_only'}, status=400)

    emoji = clone_attachment_as_emoji(request.user, message.attachment, message.attachment_name)
    return JsonResponse({'ok': True, 'emoji': serialize_user_emoji(emoji)})


@login_required
@require_POST
def send_room_emoji(request, room_name, emoji_id):
    try:
        room = Room.objects.get(name=room_name)
        emoji = UserEmoji.objects.get(pk=emoji_id, user=request.user)
    except (Room.DoesNotExist, UserEmoji.DoesNotExist):
        return JsonResponse({'ok': False, 'error': 'emoji_not_found'}, status=404)

    membership = get_room_membership(room, request.user)
    if room.created_by != request.user and not (membership and membership.is_active):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    mark_user_emoji_used(emoji)

    profile = get_or_create_chat_profile(request.user)
    location_label = request.user.location.display_label if profile.show_location and hasattr(request.user, 'location') else ''
    message = Message(
        room=room,
        user=request.user,
        username=request.user.username,
        message='',
        message_type='chat',
        location_label=location_label,
        attachment_type='image',
        attachment_name=emoji.title or os.path.basename(emoji.image.name),
        attachment_mime='image/jpeg',
        attachment_size=emoji.image.size or 0,
    )
    with emoji.image.open('rb') as fp:
        copied = ContentFile(fp.read(), name=build_attachment_name(os.path.basename(emoji.image.name), fallback='emoji'))
    message.attachment.save(copied.name, copied, save=False)
    message.save()

    payload = serialize_room_message_payload(message, profile.to_payload())
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        build_room_group_name(room.name),
        {'type': 'chat_message', 'payload': payload}
    )
    return JsonResponse({'ok': True, 'message': payload})


@login_required
@require_POST
def send_direct_emoji(request, public_id, emoji_id):
    try:
        other_user = resolve_user_by_public_id(public_id)
        emoji = UserEmoji.objects.get(pk=emoji_id, user=request.user)
    except (User.DoesNotExist, UserEmoji.DoesNotExist):
        return JsonResponse({'ok': False, 'error': 'emoji_not_found'}, status=404)
    if other_user == request.user or not are_friends(request.user, other_user):
        return JsonResponse({'ok': False, 'error': 'forbidden'}, status=403)

    mark_user_emoji_used(emoji)

    conversation = get_or_create_direct_conversation(request.user, other_user)
    message = DirectMessage(
        conversation=conversation,
        sender=request.user,
        content='',
        attachment_type='image',
        attachment_name=emoji.title or os.path.basename(emoji.image.name),
        attachment_mime='image/jpeg',
        attachment_size=emoji.image.size or 0,
    )
    with emoji.image.open('rb') as fp:
        copied = ContentFile(fp.read(), name=build_attachment_name(os.path.basename(emoji.image.name), fallback='emoji'))
    message.attachment.save(copied.name, copied, save=False)
    message.save()

    profile = get_or_create_chat_profile(request.user)
    payload = serialize_direct_message_payload(message, profile.to_payload())
    from .consumers import DirectChatConsumer
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        DirectChatConsumer.build_group_name(request.user.id, other_user.id),
        {'type': 'direct_message_event', 'payload': payload}
    )
    return JsonResponse({'ok': True, 'message': payload})


@login_required
def friends_view(request):
    profile = get_or_create_chat_profile(request.user)
    friends = Friendship.objects.filter(user=request.user).select_related('friend', 'friend__chat_profile')
    selected_friend = friends.first()
    recent_requests = FriendRequest.objects.filter(
        recipient=request.user,
    ).exclude(status=FriendRequest.STATUS_PENDING).select_related('sender', 'sender__chat_profile')[:12]
    pending_count = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).count()
    return render(request, 'chat/friends.html', {
        'chat_profile': profile,
        'friends': friends,
        'selected_friend': selected_friend.friend if selected_friend else None,
        'recent_requests': recent_requests,
        'pending_friend_requests_count': pending_count,
    })


@login_required
def moments_view(request):
    profile = get_or_create_chat_profile(request.user)
    pending_count = FriendRequest.objects.filter(
        recipient=request.user,
        status=FriendRequest.STATUS_PENDING,
    ).count()
    friends_count = Friendship.objects.filter(user=request.user).count()
    return render(request, 'chat/moments.html', {
        'chat_profile': profile,
        'pending_friend_requests_count': pending_count,
        'friends_count': friends_count,
    })


@login_required
@require_POST
def remove_friend(request, public_id):
    try:
        other_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('friends')

    deleted_count = 0
    deleted_count += Friendship.objects.filter(user=request.user, friend=other_user).delete()[0]
    deleted_count += Friendship.objects.filter(user=other_user, friend=request.user).delete()[0]

    if deleted_count:
        messages.success(request, f'已将 {other_user.username} 从好友列表中移除')
    else:
        messages.info(request, '你们当前不是好友关系')

    next_url = request.POST.get('next')
    if next_url:
        return redirect(next_url)
    return redirect('friends')


@login_required
def user_profile(request, public_id):
    try:
        target_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('chat_index')

    target_profile = get_or_create_chat_profile(target_user)
    own_profile = get_or_create_chat_profile(request.user)
    is_self = target_user == request.user
    is_friend = are_friends(request.user, target_user) if not is_self else False
    outgoing_request = None
    incoming_request = None
    if not is_self and not is_friend:
        outgoing_request = FriendRequest.objects.filter(
            sender=request.user,
            recipient=target_user,
            status=FriendRequest.STATUS_PENDING,
        ).first()
        incoming_request = FriendRequest.objects.filter(
            sender=target_user,
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).first()
    next_url = get_safe_next_url(request)

    return render(request, 'chat/user_profile.html', {
        'chat_profile': own_profile,
        'target_user': target_user,
        'target_profile': target_profile,
        'is_self': is_self,
        'is_friend': is_friend,
        'outgoing_request': outgoing_request,
        'incoming_request': incoming_request,
        'current_location': getattr(target_user, 'location', None),
        'next_url': next_url,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
    })


@login_required
@xframe_options_sameorigin
def direct_chat(request, public_id):
    try:
        other_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('chat_index')

    if other_user == request.user:
        messages.info(request, '不能和自己发起私聊')
        return redirect('chat_index')

    if not are_friends(request.user, other_user):
        messages.error(request, '你们还不是好友，暂时不能私聊')
        return redirect(get_user_profile_url(other_user))

    conversation = get_or_create_direct_conversation(request.user, other_user)
    state, _ = DirectConversationState.objects.get_or_create(conversation=conversation, user=request.user)
    state.deleted_at = None
    state.last_read_at = timezone.now()
    state.save(update_fields=['deleted_at', 'last_read_at'])
    own_profile = get_or_create_chat_profile(request.user)
    other_profile = get_or_create_chat_profile(other_user)
    direct_hub_url = get_direct_hub_url(other_user.username)
    next_url = get_safe_next_url(request, fallback_name='chat_index')
    if next_url == reverse('chat_index'):
        next_url = direct_hub_url

    if request.method == 'POST':
        action = request.POST.get('action', 'send')
        if action == 'clear_history':
            state.cleared_at = timezone.now()
            state.save(update_fields=['cleared_at'])
            messages.success(request, '已清空你这边看到的私聊历史')
            return redirect(f"{get_direct_chat_url(other_user)}?next={quote(next_url)}")

        content = request.POST.get('content', '').strip()
        if content:
            DirectMessage.objects.create(conversation=conversation, sender=request.user, content=content)
            return redirect(f"{get_direct_chat_url(other_user)}?next={quote(next_url)}")

    messages_qs = get_visible_direct_messages(conversation, state)
    messages_list = list(messages_qs)
    direct_history_info = [build_history_entry(item, 'content') for item in reversed(messages_list[-30:])]
    direct_history_images = [entry for entry in direct_history_info if entry['attachment'] and entry['attachment']['kind'] == 'image']
    direct_history_files = [entry for entry in direct_history_info if entry['attachment'] and entry['attachment']['kind'] == 'file']
    for item in messages_list:
        if item.sender_id and not hasattr(item.sender, 'chat_profile'):
            get_or_create_chat_profile(item.sender)

    inbox_context = get_inbox_context(request.user)
    embed_mode = request.GET.get('embed') == '1'

    return render(request, 'chat/direct_chat.html', {
        'chat_profile': own_profile,
        'chat_profile_payload_json': mark_safe(json.dumps(own_profile.to_payload())),
        'other_user': other_user,
        'other_profile': other_profile,
        'other_profile_payload_json': mark_safe(json.dumps(other_profile.to_payload())),
        'conversation': conversation,
        'messages_list': messages_list,
        'direct_history_info': direct_history_info,
        'direct_history_images': direct_history_images,
        'direct_history_files': direct_history_files,
        'direct_history_browser_json': mark_safe(json.dumps(
            serialize_history_browser_items(direct_history_info, direct_history_images, direct_history_files)
        )),
        'builtin_emojis': BUILTIN_EMOJIS,
        'user_emojis': list(get_user_emoji_queryset(request.user)),
        'other_username_json': mark_safe(json.dumps(other_user.username)),
        'other_public_id': other_profile.public_id,
        'cleared_at': state.cleared_at,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
        'inbox_badge_count': inbox_context['pending_friend_requests_count'],
        'embed_mode': embed_mode,
        'next_url': next_url,
        'direct_hub_url': direct_hub_url,
        'direct_history_url': build_direct_history_page_url(other_user),
    })


@login_required
@xframe_options_sameorigin
def direct_history(request, public_id):
    embed_mode = request.GET.get('embed') == '1'
    try:
        other_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('chat_index')

    if other_user == request.user:
        messages.info(request, '不能查看和自己的私聊历史')
        return redirect('chat_index')

    if not are_friends(request.user, other_user):
        messages.error(request, '你们还不是好友，暂时不能查看私聊历史')
        return redirect(get_user_profile_url(other_user))

    conversation = get_or_create_direct_conversation(request.user, other_user)
    state, _ = DirectConversationState.objects.get_or_create(conversation=conversation, user=request.user)
    state.deleted_at = None
    state.last_read_at = timezone.now()
    state.save(update_fields=['deleted_at', 'last_read_at'])

    own_profile = get_or_create_chat_profile(request.user)
    other_profile = get_or_create_chat_profile(other_user)
    messages_list = list(get_visible_direct_messages(conversation, state))
    for item in messages_list:
        if item.sender_id and not hasattr(item.sender, 'chat_profile'):
            get_or_create_chat_profile(item.sender)

    history_info = [build_history_entry(item, 'content') for item in reversed(messages_list)]
    history_images = [entry for entry in history_info if entry['attachment'] and entry['attachment']['kind'] == 'image']
    history_files = [entry for entry in history_info if entry['attachment'] and entry['attachment']['kind'] == 'file']
    history_browser_items = serialize_history_browser_items(history_info, history_images, history_files)
    next_url = get_safe_next_url(request, fallback_name='chat_index')
    if next_url == reverse('chat_index'):
        next_url = get_direct_chat_url(other_user)

    return render(request, 'chat/history_browser.html', {
        'chat_profile': own_profile,
        'history_title': f'与 {other_profile.get_display_name()} 的聊天记录',
        'history_description': '私聊消息、图片和文件都会集中在这里，支持搜索、分页和看图浏览。',
        'history_scope_label': '私聊记录',
        'history_target_name': other_profile.get_display_name(),
        'history_items_json': mark_safe(json.dumps(history_browser_items)),
        'history_back_url': next_url,
        'history_back_label': '返回私聊',
        'history_avatar_label': other_profile.get_avatar_label(),
        'history_avatar_url': other_profile.avatar_url,
        'history_meta': f'好友 ID {other_profile.friend_id}',
        'embed_mode': embed_mode,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
    })


@login_required
@require_POST
def delete_direct_conversation(request, public_id):
    try:
        other_user = resolve_user_by_public_id(public_id)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        next_url = request.POST.get('next')
        if next_url:
            return redirect(next_url)
        return redirect('chat_index')

    conversation = get_or_create_direct_conversation(request.user, other_user)
    state, _ = DirectConversationState.objects.get_or_create(conversation=conversation, user=request.user)
    now = timezone.now()
    state.deleted_at = now
    state.last_read_at = now
    state.save(update_fields=['deleted_at', 'last_read_at'])
    messages.success(request, f'已从你的消息列表移除与 {other_user.username} 的私聊')
    next_url = request.POST.get('next')
    if next_url:
        return redirect(next_url)
    return redirect('chat_index')


@login_required
def user_profile_legacy(request, username):
    try:
        target_user, _ = resolve_user_by_username(username)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('chat_index')
    return build_canonical_public_id_redirect(request, 'user_profile', get_or_create_chat_profile(target_user).public_id)


@login_required
@xframe_options_sameorigin
def direct_chat_legacy(request, username):
    try:
        other_user, _ = resolve_user_by_username(username)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('chat_index')
    return build_canonical_public_id_redirect(request, 'direct_chat', get_or_create_chat_profile(other_user).public_id)


@login_required
@require_POST
def mark_direct_read_legacy(request, username):
    try:
        other_user, _ = resolve_user_by_username(username)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)
    return mark_direct_read(request, get_or_create_chat_profile(other_user).public_id)


@login_required
@require_POST
def upload_direct_attachment_legacy(request, username):
    try:
        other_user, _ = resolve_user_by_username(username)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)
    return upload_direct_attachment(request, get_or_create_chat_profile(other_user).public_id)


@login_required
@require_POST
def favorite_direct_image_emoji_legacy(request, username, message_id):
    try:
        other_user, _ = resolve_user_by_username(username)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)
    return favorite_direct_image_emoji(request, get_or_create_chat_profile(other_user).public_id, message_id)


@login_required
@require_POST
def send_direct_emoji_legacy(request, username, emoji_id):
    try:
        other_user, _ = resolve_user_by_username(username)
    except User.DoesNotExist:
        return JsonResponse({'ok': False, 'error': 'user_not_found'}, status=404)
    return send_direct_emoji(request, get_or_create_chat_profile(other_user).public_id, emoji_id)


@login_required
@require_POST
def delete_direct_conversation_legacy(request, username):
    try:
        other_user, _ = resolve_user_by_username(username)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        next_url = request.POST.get('next')
        if next_url:
            return redirect(next_url)
        return redirect('chat_index')
    return delete_direct_conversation(request, get_or_create_chat_profile(other_user).public_id)


@login_required
@require_POST
def remove_friend_legacy(request, username):
    try:
        other_user, _ = resolve_user_by_username(username)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('friends')
    return remove_friend(request, get_or_create_chat_profile(other_user).public_id)


@login_required
def create_room_page(request):
    get_or_create_chat_profile(request.user)
    if request.method == 'POST':
        room_name = request.POST.get('room_name', '').strip()
        room_avatar = request.POST.get('room_avatar', '💬').strip() or '💬'
        room_description = request.POST.get('room_description', '').strip()
        join_policy = request.POST.get('join_policy', Room.JOIN_POLICY_APPROVAL).strip() or Room.JOIN_POLICY_APPROVAL

        if room_avatar not in DEFAULT_ROOM_AVATARS:
            room_avatar = '💬'
        if join_policy not in dict(Room.JOIN_POLICY_CHOICES):
            join_policy = Room.JOIN_POLICY_APPROVAL
        if not room_description:
            room_description = '一起聊聊吧'

        if room_name:
            if Room.objects.filter(name=room_name).exists():
                messages.error(request, '房间已存在')
            else:
                room = Room.objects.create(
                    name=room_name,
                    join_policy=join_policy,
                    avatar=room_avatar,
                    description=room_description[:120],
                    created_by=request.user,
                )
                get_or_create_room_membership(room, request.user)
                messages.success(request, f'房间 "{room_name}" 创建成功')
                return redirect(f"{reverse('chat_index')}?thread_type=room&target={quote(room.name)}")

    return render(request, 'chat/create_room.html', {
        'chat_profile': get_or_create_chat_profile(request.user),
        'room_avatars': DEFAULT_ROOM_AVATARS,
        'room_join_policy_choices': Room.JOIN_POLICY_CHOICES,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
    })


@login_required
def discover_rooms_page(request):
    profile = get_or_create_chat_profile(request.user)
    query = request.GET.get('q', '').strip()
    accessible_rooms = get_accessible_rooms_queryset(request.user)
    accessible_room_ids = list(accessible_rooms.values_list('id', flat=True))
    pending_request_room_ids = set(
        RoomJoinRequest.objects.filter(
            requester=request.user,
            status=RoomJoinRequest.STATUS_PENDING,
        ).values_list('room_id', flat=True)
    )
    pending_invite_room_ids = set(
        RoomInvitation.objects.filter(
            invited_user=request.user,
            status=RoomInvitation.STATUS_PENDING,
        ).values_list('room_id', flat=True)
    )

    room_results = Room.objects.order_by('-created_at')
    if query:
        room_results = room_results.filter(
            Q(name__icontains=query) | Q(room_id__icontains=query) | Q(description__icontains=query)
        )
    else:
        room_results = room_results.none()

    return render(request, 'chat/discover_rooms.html', {
        'chat_profile': profile,
        'query': query,
        'room_results': room_results[:30],
        'accessible_room_ids': accessible_room_ids,
        'pending_request_room_ids': pending_request_room_ids,
        'pending_invite_room_ids': pending_invite_room_ids,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
    })


@login_required
@require_POST
def join_room(request, room_id):
    next_url = request.POST.get('next') or reverse('discover_rooms')
    try:
        room = Room.objects.get(room_id=room_id)
    except Room.DoesNotExist:
        messages.error(request, '群聊不存在')
        return redirect(next_url)

    membership = get_room_membership(room, request.user)
    if membership and membership.is_active:
        messages.info(request, '你已经在这个群里了')
        return redirect(f"{reverse('chat_index')}?thread_type=room&target={quote(room.name)}")

    pending_invitation = RoomInvitation.objects.filter(
        room=room,
        invited_user=request.user,
        status=RoomInvitation.STATUS_PENDING,
    ).first()
    if pending_invitation:
        messages.info(request, '你已经收到了这个群聊的邀请，请去消息中心处理')
        return redirect('inbox')

    if room.join_policy == Room.JOIN_POLICY_OPEN:
        membership, _ = RoomMembership.objects.get_or_create(
            room=room,
            user=request.user,
            defaults={'is_active': True, 'removed_at': None},
        )
        membership.is_active = True
        membership.removed_at = None
        membership.save(update_fields=['is_active', 'removed_at'])
        RoomJoinRequest.objects.filter(room=room, requester=request.user).exclude(
            status=RoomJoinRequest.STATUS_ACCEPTED
        ).delete()
        messages.success(request, f'已加入群聊「{room.name}」')
        return redirect(f"{reverse('chat_index')}?thread_type=room&target={quote(room.name)}")

    join_request, created = RoomJoinRequest.objects.get_or_create(
        room=room,
        requester=request.user,
        defaults={
            'status': RoomJoinRequest.STATUS_PENDING,
            'note': request.POST.get('note', '').strip()[:160],
        },
    )
    if not created and join_request.status == RoomJoinRequest.STATUS_REJECTED:
        join_request.status = RoomJoinRequest.STATUS_PENDING
        join_request.note = request.POST.get('note', '').strip()[:160]
        join_request.responded_at = None
        join_request.save(update_fields=['status', 'note', 'responded_at'])
    elif not created and join_request.status == RoomJoinRequest.STATUS_ACCEPTED:
        join_request.status = RoomJoinRequest.STATUS_PENDING
        join_request.note = request.POST.get('note', '').strip()[:160]
        join_request.responded_at = None
        join_request.save(update_fields=['status', 'note', 'responded_at'])
    elif not created and join_request.status == RoomJoinRequest.STATUS_PENDING:
        messages.info(request, '你已经提交过入群申请了')
        return redirect(next_url)

    messages.success(request, f'已提交加入「{room.name}」的申请')
    return redirect(next_url)


@login_required
@require_POST
def invite_to_room(request):
    room_id = request.POST.get('room_id', '').strip()
    next_url = request.POST.get('next') or reverse('chat_index')
    try:
        room = Room.objects.get(room_id=room_id)
    except Room.DoesNotExist:
        messages.error(request, '群聊不存在')
        return redirect(next_url)

    membership = get_room_membership(room, request.user)
    if not can_manage_room_members(room, request.user, membership=membership):
        messages.error(request, '只有群主或群管理员才能邀请成员')
        return redirect(next_url)

    target_friend_id = request.POST.get('friend_id', '').strip().lower() or request.POST.get('manual_friend_id', '').strip().lower()
    target_username = request.POST.get('username', '').strip()
    target_user = None
    if target_friend_id:
        target_profile = UserChatProfile.objects.filter(friend_id=target_friend_id).select_related('user').first()
        target_user = target_profile.user if target_profile else None
    elif target_username:
        target_user = User.objects.filter(username=target_username).first()

    if not target_user:
        messages.error(request, '没有找到要邀请的用户')
        return redirect(next_url)
    if target_user == request.user:
        messages.error(request, '不能邀请自己')
        return redirect(next_url)
    if not are_friends(request.user, target_user):
        messages.error(request, '只能邀请你的好友入群')
        return redirect(next_url)

    target_membership = get_room_membership(room, target_user)
    if target_membership and target_membership.is_active:
        messages.info(request, '对方已经在群里了')
        return redirect(next_url)

    invitation, created = RoomInvitation.objects.get_or_create(
        room=room,
        invited_user=target_user,
        defaults={
            'invited_by': request.user,
            'status': RoomInvitation.STATUS_PENDING,
        },
    )
    if not created and invitation.status == RoomInvitation.STATUS_DECLINED:
        invitation.status = RoomInvitation.STATUS_PENDING
        invitation.invited_by = request.user
        invitation.responded_at = None
        invitation.save(update_fields=['status', 'invited_by', 'responded_at'])
    elif not created and invitation.status == RoomInvitation.STATUS_PENDING:
        messages.info(request, '已经发过邀请了')
        return redirect(next_url)

    messages.success(request, f'已邀请 {target_user.username} 加入「{room.name}」')
    return redirect(next_url)


@login_required
@require_POST
def respond_room_invitation(request, invitation_id):
    try:
        invitation = RoomInvitation.objects.select_related('room').get(id=invitation_id, invited_user=request.user)
    except RoomInvitation.DoesNotExist:
        messages.error(request, '群邀请不存在')
        return redirect('inbox')

    if invitation.status != RoomInvitation.STATUS_PENDING:
        messages.info(request, '这条群邀请已经处理过了')
        return redirect('inbox')

    action = request.POST.get('action', '').strip()
    if action == 'accept':
        membership, _ = RoomMembership.objects.get_or_create(
            room=invitation.room,
            user=request.user,
            defaults={'is_active': True, 'removed_at': None},
        )
        membership.is_active = True
        membership.removed_at = None
        membership.save(update_fields=['is_active', 'removed_at'])
        invitation.status = RoomInvitation.STATUS_ACCEPTED
        invitation.responded_at = timezone.now()
        invitation.save(update_fields=['status', 'responded_at'])
        messages.success(request, f'已加入群聊「{invitation.room.name}」')
        return redirect(f"{reverse('chat_index')}?thread_type=room&target={quote(invitation.room.name)}")

    invitation.status = RoomInvitation.STATUS_DECLINED
    invitation.responded_at = timezone.now()
    invitation.save(update_fields=['status', 'responded_at'])
    messages.success(request, '已拒绝群邀请')
    return redirect('inbox')


@login_required
@require_POST
def respond_room_join_request(request, request_id):
    try:
        join_request = RoomJoinRequest.objects.select_related('room', 'requester').get(id=request_id)
    except RoomJoinRequest.DoesNotExist:
        messages.error(request, '入群申请不存在')
        return redirect('inbox')

    membership = get_room_membership(join_request.room, request.user)
    if not can_manage_room_members(join_request.room, request.user, membership=membership):
        messages.error(request, '只有群主或群管理员才能处理入群申请')
        return redirect('inbox')
    if join_request.status != RoomJoinRequest.STATUS_PENDING:
        messages.info(request, '这条入群申请已经处理过了')
        return redirect('inbox')

    action = request.POST.get('action', '').strip()
    if action == 'accept':
        requester_membership, _ = RoomMembership.objects.get_or_create(
            room=join_request.room,
            user=join_request.requester,
            defaults={'is_active': True, 'removed_at': None},
        )
        requester_membership.is_active = True
        requester_membership.removed_at = None
        requester_membership.save(update_fields=['is_active', 'removed_at'])
        join_request.status = RoomJoinRequest.STATUS_ACCEPTED
        join_request.responded_at = timezone.now()
        join_request.save(update_fields=['status', 'responded_at'])
        messages.success(request, f'已通过 {join_request.requester.username} 的入群申请')
        return redirect('inbox')

    join_request.status = RoomJoinRequest.STATUS_REJECTED
    join_request.responded_at = timezone.now()
    join_request.save(update_fields=['status', 'responded_at'])
    messages.success(request, f'已拒绝 {join_request.requester.username} 的入群申请')
    return redirect('inbox')


@login_required
def add_friend_page(request):
    profile = get_or_create_chat_profile(request.user)
    if request.method == 'POST':
        friend_id = request.POST.get('friend_id', '').strip().lower()
        if not friend_id:
            messages.error(request, '请输入好友 ID')
            return redirect('add_friend')

        if profile.friend_id == friend_id:
            messages.error(request, '不能添加自己为好友')
            return redirect('add_friend')

        try:
            recipient_profile = UserChatProfile.objects.select_related('user').get(friend_id=friend_id)
        except UserChatProfile.DoesNotExist:
            messages.error(request, '没有找到这个好友 ID')
            return redirect('add_friend')

        if Friendship.objects.filter(user=request.user, friend=recipient_profile.user).exists():
            messages.info(request, '你们已经是好友了')
            return redirect('add_friend')

        friend_request, created = FriendRequest.objects.get_or_create(
            sender=request.user,
            recipient=recipient_profile.user,
            defaults={'status': FriendRequest.STATUS_PENDING},
        )
        if not created and friend_request.status == FriendRequest.STATUS_REJECTED:
            friend_request.status = FriendRequest.STATUS_PENDING
            friend_request.responded_at = None
            friend_request.save(update_fields=['status', 'responded_at'])
        elif not created:
            messages.info(request, '好友申请已经发出，请等待对方处理')
            return redirect('add_friend')

        messages.success(request, f'已向 {recipient_profile.user.username} 发送好友申请')
        return redirect('add_friend')

    return render(request, 'chat/add_friend.html', {
        'chat_profile': profile,
        'pending_friend_requests_count': FriendRequest.objects.filter(
            recipient=request.user,
            status=FriendRequest.STATUS_PENDING,
        ).count(),
        'recent_requests': FriendRequest.objects.filter(
            sender=request.user,
        ).select_related('recipient', 'recipient__chat_profile')[:10],
    })


@login_required
@require_POST
def send_friend_request(request):
    friend_id = request.POST.get('friend_id', '').strip().lower()
    if not friend_id:
        messages.error(request, '请输入好友 ID')
        return redirect(request.POST.get('next') or 'chat_index')

    sender_profile = get_or_create_chat_profile(request.user)
    if sender_profile.friend_id == friend_id:
        messages.error(request, '不能添加自己为好友')
        return redirect(request.POST.get('next') or 'chat_index')

    try:
        recipient_profile = UserChatProfile.objects.select_related('user').get(friend_id=friend_id)
    except UserChatProfile.DoesNotExist:
        messages.error(request, '没有找到这个好友 ID')
        return redirect(request.POST.get('next') or 'chat_index')

    if Friendship.objects.filter(user=request.user, friend=recipient_profile.user).exists():
        messages.info(request, '你们已经是好友了')
        return redirect(request.POST.get('next') or 'chat_index')

    friend_request, created = FriendRequest.objects.get_or_create(
        sender=request.user,
        recipient=recipient_profile.user,
        defaults={'status': FriendRequest.STATUS_PENDING},
    )
    if not created and friend_request.status == FriendRequest.STATUS_REJECTED:
        friend_request.status = FriendRequest.STATUS_PENDING
        friend_request.responded_at = None
        friend_request.save(update_fields=['status', 'responded_at'])
    elif not created:
        messages.info(request, '好友申请已经发出，请等待对方处理')
        return redirect(request.POST.get('next') or 'chat_index')

    messages.success(request, f'已向 {recipient_profile.user.username} 发送好友申请')
    return redirect(request.POST.get('next') or 'chat_index')


@login_required
@require_POST
def respond_friend_request(request, request_id):
    action = request.POST.get('action')
    try:
        friend_request = FriendRequest.objects.get(id=request_id, recipient=request.user)
    except FriendRequest.DoesNotExist:
        messages.error(request, '好友申请不存在')
        return redirect('inbox')

    if friend_request.status != FriendRequest.STATUS_PENDING:
        messages.info(request, '这条好友申请已经处理过了')
        return redirect('inbox')

    if action == 'accept':
        friend_request.status = FriendRequest.STATUS_ACCEPTED
        friend_request.responded_at = timezone.now()
        friend_request.save(update_fields=['status', 'responded_at'])
        Friendship.objects.get_or_create(user=request.user, friend=friend_request.sender)
        Friendship.objects.get_or_create(user=friend_request.sender, friend=request.user)
        messages.success(request, f'已通过 {friend_request.sender.username} 的好友申请')
    else:
        friend_request.status = FriendRequest.STATUS_REJECTED
        friend_request.responded_at = timezone.now()
        friend_request.save(update_fields=['status', 'responded_at'])
        messages.info(request, f'已拒绝 {friend_request.sender.username} 的好友申请')
    return redirect('inbox')


def logout_view(request):
    """注销登录"""
    if request.user.is_authenticated:
        UserSession.objects.filter(user=request.user).delete()
        notify_user_presence_changed(request.user)
    logout(request)
    return redirect('login')


def not_found_page(request, exception=None):
    """统一的 404 页面，并自动返回主页"""
    home_url = reverse('chat_index') if request.user.is_authenticated else reverse('login')
    response = render(request, '404.html', {
        'home_url': home_url,
    }, status=404)
    return response


def is_admin_user(user):
    """检查用户是否为管理员"""
    return user.is_authenticated and user.is_superuser


@user_passes_test(is_admin_user)
def admin_dashboard(request):
    """管理员仪表板"""
    # 获取统计数据
    total_users = User.objects.count()
    total_rooms = Room.objects.count()
    total_sessions = UserSession.objects.count()
    
    context = {
        'total_users': total_users,
        'total_rooms': total_rooms,
        'total_sessions': total_sessions,
        'site_config': SiteConfiguration.get_solo(),
    }
    
    return render(request, 'chat/admin/dashboard.html', context)


@user_passes_test(is_admin_user)
def admin_users(request):
    """用户管理"""
    if request.method == 'POST':
        action = request.POST.get('action', '').strip()
        if action == 'bulk_toggle_active':
            user_ids = request.POST.getlist('selected_users')
            affected = 0
            for user in User.objects.filter(id__in=user_ids).exclude(id=request.user.id):
                user.is_active = not user.is_active
                user.save(update_fields=['is_active'])
                affected += 1
            messages.success(request, f'已批量切换 {affected} 个用户的激活状态')
            return redirect(build_admin_list_redirect_url('admin_users', request))

        if action == 'bulk_toggle_superuser':
            user_ids = request.POST.getlist('selected_users')
            affected = 0
            for user in User.objects.filter(id__in=user_ids).exclude(id=request.user.id):
                next_value = not user.is_superuser
                user.is_superuser = next_value
                user.is_staff = next_value
                user.save(update_fields=['is_superuser', 'is_staff'])
                affected += 1
            messages.success(request, f'已批量更新 {affected} 个用户的管理员状态')
            return redirect(build_admin_list_redirect_url('admin_users', request))

        if action == 'bulk_delete':
            user_ids = request.POST.getlist('selected_users')
            users_to_delete = User.objects.filter(id__in=user_ids).exclude(id=request.user.id)
            affected = 0
            for user in users_to_delete:
                UserSession.objects.filter(user=user).delete()
                Room.objects.filter(created_by=user).update(created_by=None)
                user.delete()
                affected += 1
            messages.success(request, f'已批量删除 {affected} 个用户')
            return redirect(build_admin_list_redirect_url('admin_users', request, extra_params={'page': '1'}))
    
    if request.method == 'POST':
        user_id = request.POST.get('user_id')
        action = request.POST.get('action')
        
        if user_id and action:
            try:
                user = User.objects.get(id=user_id)
                if action == 'delete':
                    # 删除用户及其相关数据
                    UserSession.objects.filter(user=user).delete()
                    Room.objects.filter(created_by=user).update(created_by=None)
                    user.delete()
                    messages.success(request, f'用户 {user.username} 已删除')
                elif action == 'toggle_superuser':
                    user.is_superuser = not user.is_superuser
                    user.is_staff = user.is_superuser
                    user.save()
                    messages.success(request, f'用户 {user.username} 的管理员状态已更改')
                elif action == 'toggle_active':
                    user.is_active = not user.is_active
                    user.save()
                    messages.success(request, f'用户 {user.username} 的激活状态已更改')
            except User.DoesNotExist:
                messages.error(request, '用户不存在')
        
        return redirect(build_admin_list_redirect_url('admin_users', request))

    page_size = get_admin_page_size(request, 'page_size', default=10)
    users = User.objects.order_by('-date_joined', '-id')
    paginator = Paginator(users, page_size)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'chat/admin/users.html', {
        'users_page': page_obj,
        'page_size': page_size,
        'admin_page_size_options': ADMIN_PAGE_SIZE_OPTIONS,
    })


@user_passes_test(is_admin_user)
def admin_rooms(request):
    """房间管理"""
    if request.method == 'POST' and request.POST.get('action') == 'bulk_delete':
        room_ids = request.POST.getlist('selected_rooms')
        affected = Room.objects.filter(id__in=room_ids).count()
        Room.objects.filter(id__in=room_ids).delete()
        messages.success(request, f'已批量删除 {affected} 个房间')
        return redirect(build_admin_list_redirect_url('admin_rooms', request, extra_params={'page': '1'}))

    rooms = Room.objects.select_related('created_by').all()
    
    if request.method == 'POST':
        room_name = request.POST.get('room_name')
        action = request.POST.get('action')
        
        if room_name and action:
            try:
                room = Room.objects.get(name=room_name)
                if action == 'delete':
                    room.delete()
                    messages.success(request, f'房间 {room_name} 已删除')
            except Room.DoesNotExist:
                messages.error(request, '房间不存在')
        
        return redirect(build_admin_list_redirect_url('admin_rooms', request))

    page_size = get_admin_page_size(request, 'page_size', default=10)
    paginator = Paginator(rooms.order_by('-created_at', '-id'), page_size)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'chat/admin/rooms.html', {
        'rooms_page': page_obj,
        'page_size': page_size,
        'admin_page_size_options': ADMIN_PAGE_SIZE_OPTIONS,
    })


@user_passes_test(is_admin_user)
def admin_sessions(request):
    """会话管理"""
    if request.method == 'POST' and request.POST.get('action') == 'bulk_delete':
        session_ids = request.POST.getlist('selected_sessions')
        affected = UserSession.objects.filter(id__in=session_ids).count()
        UserSession.objects.filter(id__in=session_ids).delete()
        messages.success(request, f'已批量删除 {affected} 个会话')
        return redirect(build_admin_list_redirect_url('admin_sessions', request, extra_params={'page': '1'}))

    sessions = UserSession.objects.select_related('user').all()
    
    if request.method == 'POST':
        session_id = request.POST.get('session_id')
        action = request.POST.get('action')
        
        if session_id and action:
            try:
                session = UserSession.objects.get(id=session_id)
                if action == 'delete':
                    session.delete()
                    messages.success(request, f'会话 {session.session_key[:8]}... 已删除')
            except UserSession.DoesNotExist:
                messages.error(request, '会话不存在')
        
        return redirect(build_admin_list_redirect_url('admin_sessions', request))

    page_size = get_admin_page_size(request, 'page_size', default=10)
    paginator = Paginator(sessions.order_by('-created_at', '-id'), page_size)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    return render(request, 'chat/admin/sessions.html', {
        'sessions_page': page_obj,
        'page_size': page_size,
        'admin_page_size_options': ADMIN_PAGE_SIZE_OPTIONS,
    })


@user_passes_test(is_admin_user)
def admin_user_password(request, user_id):
    """管理员修改用户密码"""
    try:
        managed_user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, '用户不存在')
        return redirect('admin_users')

    form = AdminUserPasswordForm(managed_user, request.POST or None)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'用户 {managed_user.username} 的密码已更新')
        return redirect('admin_users')

    return render(request, 'chat/admin/user_password.html', {
        'managed_user': managed_user,
        'form': form,
        'password_help_html': mark_safe(password_validators_help_text_html()),
    })


@user_passes_test(is_admin_user)
def admin_site_settings(request):
    """站点设置"""
    site_config = SiteConfiguration.get_solo()
    if site_config is None:
        messages.error(request, '当前数据库尚未完成站点配置初始化，请先执行 migrate')
        return redirect('admin_dashboard')

    form = SiteConfigurationForm(request.POST or None, request.FILES or None, instance=site_config)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, '站点设置已更新，新的标题、图标和来源配置会在后续请求中生效')
        return redirect('admin_site_settings')

    return render(request, 'chat/admin/site_settings.html', {
        'form': form,
        'site_config': site_config,
        'default_admin_username': 'xyadmin',
        'default_admin_password': 'xyadmin123',
    })
