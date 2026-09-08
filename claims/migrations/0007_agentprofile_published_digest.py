from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("claims", "0006_conversation_end_reason_dispatch"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentprofile",
            name="published_digest",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
