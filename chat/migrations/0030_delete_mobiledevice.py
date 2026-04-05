from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0029_mobiledevice"),
    ]

    operations = [
        migrations.DeleteModel(
            name="MobileDevice",
        ),
    ]
