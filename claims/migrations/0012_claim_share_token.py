"""Give every claim an unguessable share token.

Added without the unique constraint, backfilled, then constrained — adding a
unique column to a table with rows would otherwise collide on the empty
default the moment there is more than one claim.
"""

import secrets

from django.db import migrations, models


def mint_tokens(apps, schema_editor):
    Claim = apps.get_model("claims", "Claim")
    for claim in Claim.objects.filter(share_token="").only("id"):
        # Not Claim.save(): historical models have no custom save().
        Claim.objects.filter(pk=claim.pk).update(share_token=secrets.token_urlsafe(24))


def drop_tokens(apps, schema_editor):
    apps.get_model("claims", "Claim").objects.update(share_token="")


class Migration(migrations.Migration):
    dependencies = [("claims", "0011_vendor_email_desk")]

    operations = [
        migrations.AddField(
            model_name="claim",
            name="share_token",
            field=models.CharField(blank=True, db_index=True, default="", max_length=43),
        ),
        migrations.RunPython(mint_tokens, drop_tokens),
        migrations.AlterField(
            model_name="claim",
            name="share_token",
            field=models.CharField(
                blank=True, db_index=True, max_length=43, unique=True
            ),
        ),
    ]
