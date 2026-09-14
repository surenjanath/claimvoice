from django.contrib import admin

from .models import Claim, DeskAction, Dispatcher, Handoff, Shift


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


@admin.register(Dispatcher)
class DispatcherAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "phone", "role", "order", "is_active", "last_seen_at")
    list_filter = ("role", "is_active")
    search_fields = ("name", "email", "phone")
    # A password field here would write a raw string into the hash column.
    # Passwords are set by `create_dispatcher`, which hashes them.
    exclude = ("password",)
    inlines = []


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("dispatcher", "weekday", "starts", "ends", "overnight")
    list_filter = ("weekday", "dispatcher")


@admin.register(Handoff)
class HandoffAdmin(admin.ModelAdmin):
    list_display = ("id", "created_at", "status", "dispatcher", "caller_number", "reason")
    list_filter = ("status", "simulated")
    readonly_fields = ("token", "provider_call_id", "created_at")


@admin.register(DeskAction)
class DeskActionAdmin(admin.ModelAdmin):
    list_display = ("created_at", "who", "action", "subject", "detail")
    list_filter = ("action",)
    search_fields = ("who", "subject", "detail")
    # The audit trail is a record, not a workspace.
    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request):
        return False
