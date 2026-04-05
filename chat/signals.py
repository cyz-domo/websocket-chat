from django.contrib.auth.models import User
from django.db.models.signals import post_migrate
from django.db.utils import OperationalError, ProgrammingError
from django.dispatch import receiver

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
