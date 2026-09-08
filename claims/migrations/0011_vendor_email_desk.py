from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("claims", "0010_claim_assignment_notes"),
    ]

    operations = [
        migrations.AddField(
            model_name="claim",
            name="email_status",
            field=models.CharField(blank=True, max_length=16),
        ),
        migrations.AddField(
            model_name="dispatch",
            name="vendor_phone",
            field=models.CharField(blank=True, max_length=24),
        ),
    ]
