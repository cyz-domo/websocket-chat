import logging
from pathlib import Path

from django.conf import settings
from django.apps import apps


logger = logging.getLogger(__name__)


class PushNotificationService:
    INVALID_TOKEN_ERRORS = {
        'InvalidArgumentError',
        'SenderIdMismatchError',
        'UnregisteredError',
    }

    def __init__(self):
        self._firebase_app = None

    def is_enabled(self):
        return bool(getattr(settings, 'MOBILE_PUSH_NOTIFICATIONS_ENABLED', True))

    def notify_direct_message(self, direct_message):
        if not self.is_enabled():
            return

        conversation = direct_message.conversation
        recipient = conversation.other_user(direct_message.sender)
        if not self._should_notify_user(recipient):
            return

        sender_profile = self._get_or_create_profile(direct_message.sender)
        sender_name = sender_profile.get_display_name() if sender_profile else direct_message.sender.username
        preview = self._build_preview_text(
            direct_message.content,
            direct_message.attachment_type,
            direct_message.attachment_name,
        )

        self._send_to_users(
            [recipient],
            title=sender_name,
            body=preview or '你收到一条新消息',
            data={
                'kind': 'direct',
                'conversation_id': str(conversation.id),
                'sender_id': str(direct_message.sender_id),
                'sender_username': direct_message.sender.username,
                'public_id': getattr(sender_profile, 'public_id', ''),
            },
        )

    def notify_room_message(self, room_message):
        if not self.is_enabled():
            return
        if room_message.message_type != 'chat':
            return

        sender = room_message.user
        sender_id = getattr(sender, 'id', None)
        sender_profile = self._get_or_create_profile(sender) if sender else None
        sender_name = sender_profile.get_display_name() if sender_profile else room_message.username
        preview = self._build_preview_text(
            room_message.message,
            room_message.attachment_type,
            room_message.attachment_name,
        )

        recipient_ids = list(
            room_message.room.memberships.filter(is_active=True)
            .exclude(user_id=sender_id)
            .values_list('user_id', flat=True)
        )
        if room_message.room.created_by_id and room_message.room.created_by_id != sender_id:
            recipient_ids.append(room_message.room.created_by_id)
        recipient_ids = list(dict.fromkeys(recipient_ids))
        if not recipient_ids:
            return

        User = apps.get_model('auth', 'User')
        recipients = User.objects.filter(id__in=recipient_ids)
        self._send_to_users(
            recipients,
            title=room_message.room.name,
            body=f'{sender_name}: {preview}' if preview else f'{sender_name} 发来了一条新消息',
            data={
                'kind': 'room',
                'room_id': str(room_message.room_id),
                'room_name': room_message.room.name,
                'sender_id': str(sender_id or ''),
                'sender_username': room_message.username or '',
            },
        )

    def _should_notify_user(self, user):
        if not user or not getattr(user, 'is_active', False):
            return False
        if getattr(settings, 'PUSH_NOTIFY_ONLINE_USERS', False):
            return True
        return not user.sessions.exists()

    def _send_to_users(self, users, title, body, data):
        app = self._get_firebase_app()
        if app is None:
            return

        from chat.models import MobileDevice

        user_ids = []
        for user in users:
            if not self._should_notify_user(user):
                continue
            user_ids.append(user.id)

        if not user_ids:
            logger.info('Push notification skipped because all recipients are considered online or inactive.')
            return

        devices = MobileDevice.objects.filter(
            user_id__in=user_ids,
            notifications_enabled=True,
        ).select_related('user')
        if not devices.exists():
            logger.info('Push notification skipped because no enabled mobile devices were found for users: %s', user_ids)
            return

        for device in devices:
            self._send_to_device(app, device, title=title, body=body, data=data)

    def _send_to_device(self, app, device, title, body, data):
        try:
            from firebase_admin import messaging

            message = messaging.Message(
                token=device.token,
                notification=messaging.Notification(title=title[:120], body=body[:240]),
                data={key: str(value) for key, value in data.items() if value is not None},
            )
            messaging.send(message, app=app)
            logger.info('Push notification sent to device %s for user %s', device.pk, device.user_id)
        except Exception as exc:
            logger.warning('Push send failed for device %s: %s', device.pk, exc)
            if exc.__class__.__name__ in self.INVALID_TOKEN_ERRORS:
                device.notifications_enabled = False
                device.save(update_fields=['notifications_enabled', 'last_seen_at'])

    @staticmethod
    def _get_or_create_profile(user):
        if not user:
            return None

        try:
            return user.chat_profile
        except Exception:
            from chat.models import UserChatProfile

            profile, _ = UserChatProfile.objects.get_or_create(
                user=user,
                defaults={
                    'public_id': UserChatProfile.generate_unique_public_id(exclude_user_id=user.id),
                    'display_name': user.username,
                    'friend_id': UserChatProfile.generate_unique_friend_id(user.username, exclude_user_id=user.id),
                    'avatar_label': '',
                },
            )
            return profile

    def _get_firebase_app(self):
        if self._firebase_app is not None:
            return self._firebase_app

        credentials_file = getattr(settings, 'FIREBASE_CREDENTIALS_FILE', '')
        if not credentials_file:
            logger.info('Push notifications are enabled but FIREBASE_CREDENTIALS_FILE is not configured.')
            return None

        credentials_path = Path(credentials_file)
        if not credentials_path.exists():
            logger.warning('Firebase credentials file does not exist: %s', credentials_path)
            return None

        try:
            import firebase_admin
            from firebase_admin import credentials

            try:
                self._firebase_app = firebase_admin.get_app()
            except ValueError:
                options = {}
                project_id = getattr(settings, 'FIREBASE_PROJECT_ID', '')
                if project_id:
                    options['projectId'] = project_id
                self._firebase_app = firebase_admin.initialize_app(
                    credentials.Certificate(str(credentials_path)),
                    options or None,
                )
            return self._firebase_app
        except ImportError:
            logger.warning('firebase-admin is not installed; mobile push notifications are disabled.')
            return None
        except Exception as exc:
            logger.warning('Failed to initialize Firebase app: %s', exc)
            return None

    @staticmethod
    def _build_preview_text(text='', attachment_type='', attachment_name=''):
        raw_text = (text or '').strip()
        if raw_text:
            return raw_text[:120]

        if attachment_type == 'image':
            return f'[图片] {attachment_name}'.strip()
        if attachment_type == 'video':
            return f'[视频] {attachment_name}'.strip()
        if attachment_type == 'file':
            return f'[文件] {attachment_name}'.strip()
        return (attachment_name or '').strip()[:120]
