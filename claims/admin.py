from django.contrib import admin

from .models import Claim


@admin.register(Claim)
class ClaimAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "policy_number",
        "incident_type",
        "location",
        "is_drivable",
        "risk_score",
        "status",
        "created_at",
    )
    list_filter = ("incident_type", "status", "is_drivable", "tow_required")
    search_fields = ("policy_number", "location", "caller_name", "description")
    readonly_fields = ("raw_payload", "created_at")
