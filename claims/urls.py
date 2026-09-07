from django.urls import path

from . import views, views_identity, views_insights

urlpatterns = [
    path("", views.voice, name="voice"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("directory/", views_identity.directory, name="directory"),
    path("insights/", views_insights.insights, name="insights"),
    path("settings/", views.agent_settings, name="settings"),
    # The two tool webhooks. csrf exempt: AssemblyAI posts them, not a browser.
    path("api/verify/", views_identity.verify_policyholder, name="verify"),
    path("api/log-claim/", views.log_claim, name="log-claim"),
    # Read models for the dispatcher screens.
    path("api/claims/", views.claim_feed, name="claim-feed"),
    path("api/claims/<int:pk>/status/", views.claim_status, name="claim-status"),
    path("api/conversations/", views_identity.conversation_feed, name="conversation-feed"),
    path("api/conversations/ingest/", views_identity.conversation_ingest, name="conversation-ingest"),
    path("api/conversations/session/", views_identity.conversation_by_session, name="conversation-session"),
    path("api/conversations/<int:pk>/", views_identity.conversation_detail, name="conversation-detail"),
    path("api/conversations/<int:pk>/recording/", views_identity.conversation_recording, name="conversation-recording"),
    path("api/recordings/upload/", views_identity.recording_by_session, name="recording-upload"),
    path("api/policyholders/", views_identity.policyholder_feed, name="policyholder-feed"),
    path("api/token/", views.token, name="token"),
    path("api/agent/", views.agent, name="agent"),
    path("api/metrics/", views_insights.metrics, name="metrics"),
    path("api/export/calls.jsonl", views_insights.export_calls, name="export-calls"),
    path("healthz/", views.health, name="health"),
]
