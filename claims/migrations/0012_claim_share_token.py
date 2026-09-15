"""Give every claim an unguessable share token.

Added without the unique constraint, backfilled, then constrained — adding a
unique column to a table with rows would otherwise collide on the empty
default the moment there is more than one claim.

The schema half is written to be safe to retry on Postgres. A deploy that
fails between this migration's DDL landing and Django recording it applied —
the build step after `migrate` failing, the process being killed mid-deploy —
leaves the column and its indexes sitting in the database with no record of
having run, so the next `migrate` tries to create them again and dies on
"already exists". `sqlmigrate claims 0012` against Postgres names exactly
what gets created; the SQL below is that same output with an existence guard
in front of each statement, so a retry against a partially-finished database
finishes the job instead of repeating it, and a first-ever run does exactly
what the plain Django operations would have done. SQLite (local dev, tests)
never sees any of this — it has no persistent shared database to leave
partially migrated — so it keeps the ordinary Django operations unchanged.
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


ADD_COLUMN_SQL = """
ALTER TABLE claims_claim ADD COLUMN IF NOT EXISTS share_token varchar(43) NOT NULL DEFAULT '';
"""
REMOVE_COLUMN_SQL = "ALTER TABLE claims_claim DROP COLUMN IF EXISTS share_token;"

ADD_UNIQUE_SQL = """
DROP INDEX IF EXISTS claims_claim_share_token_4c97fda6;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'claims_claim_share_token_4c97fda6_uniq'
    ) THEN
        ALTER TABLE claims_claim
            ADD CONSTRAINT claims_claim_share_token_4c97fda6_uniq UNIQUE (share_token);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS claims_claim_share_token_4c97fda6_like
    ON claims_claim (share_token varchar_pattern_ops);
"""
REMOVE_UNIQUE_SQL = """
ALTER TABLE claims_claim DROP CONSTRAINT IF EXISTS claims_claim_share_token_4c97fda6_uniq;
DROP INDEX IF EXISTS claims_claim_share_token_4c97fda6_like;
CREATE INDEX IF NOT EXISTS claims_claim_share_token_4c97fda6
    ON claims_claim (share_token);
"""


def add_share_token_column(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        Claim = apps.get_model("claims", "Claim")
        field = models.CharField(blank=True, db_index=True, default="", max_length=43)
        field.set_attributes_from_name("share_token")
        schema_editor.add_field(Claim, field)
        return
    schema_editor.execute(ADD_COLUMN_SQL)
    # Django would also index the plain column here, but that index only
    # survives until make_share_token_unique below drops and replaces it, so
    # a from-scratch run and a resumed one both skip straight to that state.


def remove_share_token_column(apps, schema_editor):
    Claim = apps.get_model("claims", "Claim")
    field = models.CharField(blank=True, db_index=True, default="", max_length=43)
    field.set_attributes_from_name("share_token")
    schema_editor.remove_field(Claim, field)


def make_share_token_unique(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        Claim = apps.get_model("claims", "Claim")
        old_field = models.CharField(blank=True, db_index=True, default="", max_length=43)
        old_field.set_attributes_from_name("share_token")
        new_field = models.CharField(blank=True, db_index=True, max_length=43, unique=True)
        new_field.set_attributes_from_name("share_token")
        schema_editor.alter_field(Claim, old_field, new_field)
        return
    schema_editor.execute(ADD_UNIQUE_SQL)


def make_share_token_non_unique(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        Claim = apps.get_model("claims", "Claim")
        old_field = models.CharField(blank=True, db_index=True, max_length=43, unique=True)
        old_field.set_attributes_from_name("share_token")
        new_field = models.CharField(blank=True, db_index=True, default="", max_length=43)
        new_field.set_attributes_from_name("share_token")
        schema_editor.alter_field(Claim, old_field, new_field)
        return
    schema_editor.execute(REMOVE_UNIQUE_SQL)


class Migration(migrations.Migration):
    dependencies = [("claims", "0011_vendor_email_desk")]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(add_share_token_column, remove_share_token_column)
            ],
            state_operations=[
                migrations.AddField(
                    model_name="claim",
                    name="share_token",
                    field=models.CharField(blank=True, db_index=True, default="", max_length=43),
                ),
            ],
        ),
        migrations.RunPython(mint_tokens, drop_tokens),
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunPython(make_share_token_unique, make_share_token_non_unique)
            ],
            state_operations=[
                migrations.AlterField(
                    model_name="claim",
                    name="share_token",
                    field=models.CharField(blank=True, db_index=True, max_length=43, unique=True),
                ),
            ],
        ),
    ]
