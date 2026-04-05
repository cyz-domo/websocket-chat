from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("chat", "0028_message_attachment_thumbnail_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="siteconfiguration",
            name="allowed_hosts",
            field=models.TextField(blank=True, default="", verbose_name="允许访问 Host"),
        ),
    ]
