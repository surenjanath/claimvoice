"""Push agent.json to AssemblyAI and remember the id it comes back with.

    python manage.py publish_agent
    python manage.py publish_agent --public-url https://claimvoice.onrender.com

The first run creates the agent and writes AGENT_ID to .env; later runs update
that same agent, so a browser tab pointed at it picks up the change on the next
call.
"""

import json
import os
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from claims.agent_api import AgentApiError, publish_agent
from claims.models import AgentProfile


def write_env(key, value, path):
    """Set KEY=value in .env, replacing the line if it is already there."""
    text = path.read_text() if path.exists() else ""
    line = f"{key}={value}"
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=.*$", re.MULTILINE)
    if pattern.search(text):
        text = pattern.sub(line, text)
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
    path.write_text(text)


class Command(BaseCommand):
    help = "Publish agent.json to AssemblyAI and save the agent id to .env"

    def add_arguments(self, parser):
        parser.add_argument(
            "--public-url",
            default=None,
            help=(
                "Public https origin of this deployment. Set it and log_claim "
                "becomes a server-side HTTP tool AssemblyAI calls itself."
            ),
        )
        parser.add_argument(
            "--new",
            action="store_true",
            help="Create a new agent instead of updating the stored AGENT_ID.",
        )
        parser.add_argument(
            "--vendor",
            action="store_true",
            help="Publish vendor_agent.json — the agent that rings recovery operators.",
        )
        parser.add_argument(
            "--from-file",
            action="store_true",
            help="Discard the saved profile and republish agent.json as it stands.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print the config that would be published and stop.",
        )

    def handle(self, *args, **options):
        if not settings.ASSEMBLYAI_API_KEY:
            raise CommandError(
                "ASSEMBLYAI_API_KEY is not set. Add it to .env "
                "(https://www.assemblyai.com/dashboard/api-keys)."
            )

        if options["vendor"]:
            return self.publish_vendor_agent()

        profile = AgentProfile.load()
        if options["from_file"]:
            profile = AgentProfile.seed(profile)
        public_url = options["public_url"]
        if public_url is not None:
            profile.public_base_url = public_url.rstrip("/")
            profile.save(update_fields=["public_base_url"])

        config = profile.to_agent_config()
        if options["dry_run"]:
            self.stdout.write(json.dumps(config, indent=2))
            return

        agent_id = "" if options["new"] else profile.agent_id
        try:
            agent_id, created = publish_agent(config, agent_id)
        except AgentApiError as exc:
            raise CommandError(str(exc)) from exc

        profile.agent_id = agent_id
        profile.published_at = timezone.now()
        profile.save(update_fields=["agent_id", "published_at"])

        # Mirrored into .env so a fresh process and the CLI agree before the
        # database is read.
        env_path = Path(settings.BASE_DIR) / ".env"
        write_env("AGENT_ID", agent_id, env_path)
        if profile.public_base_url:
            write_env("PUBLIC_BASE_URL", profile.public_base_url, env_path)

        verb = "Created" if created else "Updated"
        self.stdout.write(
            self.style.SUCCESS(f'{verb} "{config["name"]}"  AGENT_ID={agent_id}')
        )
        if config["tools"]:
            for tool in config["tools"]:
                self.stdout.write(
                    f"  {tool['name']} posts to {tool['http']['url']}"
                )
        else:
            self.stdout.write(
                self.style.WARNING(
                    "  No public URL, so the agent was published with no tools:\n"
                    "  AssemblyAI stores HTTP tools only and drops client-side ones.\n"
                    "  Pass --public-url <https origin> to wire up log_claim."
                )
            )
        self.stdout.write("  Saved AGENT_ID to .env")

    def publish_vendor_agent(self):
        """The outbound agent is a fixed script, not something the settings page
        tunes, so it is published straight from its file."""
        import json as _json
        from pathlib import Path as _Path

        from claims.agent_api import publish_agent as _publish

        config = _json.loads((_Path(settings.BASE_DIR) / "vendor_agent.json").read_text())
        base = AgentProfile.load().base_url
        if not base:
            raise CommandError(
                "No public base URL. Rae's tools have to be reachable, so publish the "
                "main agent with --public-url first."
            )
        for tool in config["tools"]:
            path = "/api/vendor-eta/" if tool["name"] == "record_eta" else "/api/end-call/"
            tool["http"] = {"url": base + path, "http_method": "POST"}
            if settings.CLAIM_WEBHOOK_SECRET:
                tool["http"]["headers"] = [
                    {"name": "X-Claim-Secret", "value": settings.CLAIM_WEBHOOK_SECRET}
                ]

        agent_id, created = _publish(config, os.environ.get("VENDOR_AGENT_ID", ""))
        write_env("VENDOR_AGENT_ID", agent_id, _Path(settings.BASE_DIR) / ".env")
        self.stdout.write(
            self.style.SUCCESS(
                f'{"Created" if created else "Updated"} "{config["name"]}"  '
                f"VENDOR_AGENT_ID={agent_id}"
            )
        )
        for tool in config["tools"]:
            self.stdout.write(f"  {tool['name']} posts to {tool['http']['url']}")
