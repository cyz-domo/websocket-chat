from django.contrib.auth.models import User
from django.db import transaction
from django.db.models.signals import post_migrate, post_save
from django.db.utils import OperationalError, ProgrammingError
from django.dispatch import receiver

from .models import DirectMessage, Message
from .services import PushNotificationService


DEFAULT_ADMIN_USERNAME = 'xyadmin'
DEFAULT_ADMIN_PASSWORD = 'xyadmin123'


@receiver(post_migrate)
def ensure_default_admin(sender, **kwargs):
    if sender.name != 'chat':
        return

    try:
        admin_user, created = User.objects.get_or_create(
            username=DEFAULT_ADMIN_USERNAME,
            defaults={
                'is_staff': True,
                'is_superuser': True,
                'is_active': True,
            },
        )
    except (OperationalError, ProgrammingError):
        return

    needs_save = False
    if created or not admin_user.has_usable_password():
        admin_user.set_password(DEFAULT_ADMIN_PASSWORD)
        needs_save = True
    if not admin_user.is_staff:
        admin_user.is_staff = True
        needs_save = True
    if not admin_user.is_superuser:
        admin_user.is_superuser = True
        needs_save = True
    if not admin_user.is_active:
        admin_user.is_active = True
        needs_save = True

    if needs_save:
        admin_user.save()


@receiver(post_save, sender=Message)
def send_room_push_notification(sender, instance, created, **kwargs):
    if not created:
        return

    transaction.on_commit(lambda: PushNotificationService().notify_room_message(instance))


@receiver(post_save, sender=DirectMessage)
def send_direct_push_notification(sender, instance, created, **kwargs):
    if not created:
        return

    transaction.on_commit(lambda: PushNotificationService().notify_direct_message(instance))
