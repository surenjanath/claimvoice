"""Pull AssemblyAI session metadata into the conversation log."""

from datetime import datetime, timezone as dt_timezone

from .agent_api import api
from .models import AgentProfile, Conversation


def parse_time(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def sync_sessions(limit=50, prune=False):
    profile = AgentProfile.load()
    listing = api("/sessions")
    sessions = (listing.get("sessions") or [])[:limit]
    created = updated = 0

    for session in sessions:
        if profile.agent_id and session.get("agent_id") != profile.agent_id:
            continue
        started = parse_time(session.get("created_at"))
        conversation, is_new = Conversation.objects.get_or_create(
            session_id=session["id"],
            defaults={
                "channel": Conversation.Channel.PHONE,
                "agent_id": session.get("agent_id", ""),
                "started_at": started or datetime.now(dt_timezone.utc),
            },
        )
        fields = []
        duration = session.get("duration_seconds") or 0
        if duration and not conversation.duration_seconds:
            conversation.duration_seconds = duration
            fields.append("duration_seconds")
        reason = session.get("public_close_reason") or ""
        if reason and not conversation.close_reason:
            conversation.close_reason = reason[:64]
            fields.append("close_reason")
        ended = parse_time(session.get("ended_at"))
        if ended and not conversation.ended_at:
            conversation.ended_at = ended
            fields.append("ended_at")
        if fields:
            conversation.save(update_fields=fields)
        created += is_new
        updated += bool(fields) and not is_new

    removed = 0
    if prune:
        for conversation in Conversation.objects.filter(claims__isnull=True):
            if conversation.turns or conversation.tool_calls or conversation.recording:
                continue
            conversation.delete()
            removed += 1

    return {
        "sessions": len(sessions),
        "created": created,
        "updated": updated,
        "pruned": removed,
    }
