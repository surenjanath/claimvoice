"""The one form in the app: what the agent is and how it listens."""

from django import forms

from .models import AgentProfile


class AgentProfileForm(forms.ModelForm):
    keyterms_text = forms.CharField(
        required=False,
        label="Key terms",
        help_text="One per line. Words the transcriber should expect — policy formats, jargon, place names.",
        widget=forms.Textarea(attrs={"rows": 6, "spellcheck": "false"}),
    )

    class Meta:
        model = AgentProfile
        fields = [
            "name",
            "voice_id",
            "greeting",
            "system_prompt",
            "volume",
            "vad_threshold",
            "min_silence",
            "max_silence",
            "interrupt_response",
            "execution_mode",
            "timeout_seconds",
            "public_base_url",
        ]
        widgets = {
            "system_prompt": forms.Textarea(attrs={"rows": 18, "spellcheck": "false"}),
            "greeting": forms.Textarea(attrs={"rows": 2}),
            "voice_id": forms.RadioSelect,
            "vad_threshold": forms.NumberInput(
                attrs={"type": "range", "min": "0", "max": "1", "step": "0.05"}
            ),
            "volume": forms.NumberInput(
                attrs={"type": "range", "min": "0", "max": "100", "step": "5"}
            ),
            "public_base_url": forms.URLInput(
                attrs={"placeholder": "https://your-tunnel-or-deploy.example.com"}
            ),
        }
        labels = {
            "name": "Agent name",
            "voice_id": "Voice",
            "greeting": "Greeting",
            "system_prompt": "System prompt",
            "volume": "Output volume",
            "vad_threshold": "Voice activity threshold",
            "min_silence": "Min silence (ms)",
            "max_silence": "Max silence (ms)",
            "interrupt_response": "Allow barge-in",
            "execution_mode": "While log_claim runs",
            "timeout_seconds": "Tool timeout (s)",
            "public_base_url": "Public base URL",
        }
        help_texts = {
            "voice_id": "Immutable once a session starts, so a change applies to the next call.",
            "vad_threshold": "Lower is more sensitive to speech. 0.45 suits a noisy roadside.",
            "min_silence": "Silence before a confident end of turn.",
            "max_silence": "Silence before the turn ends regardless.",
            "public_base_url": (
                "Where AssemblyAI posts log_claim. Without it the tool cannot be "
                "published at all — the API stores HTTP tools only."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["keyterms_text"].initial = "\n".join(self.instance.keyterms or [])

    def voice_cards(self):
        """Name + region for the card picker, plus whether it is selected."""
        selected = self["voice_id"].value()
        cards = []
        for value, label in self.fields["voice_id"].choices:
            if not value:
                continue
            name, _, region = label.partition(" — ")
            cards.append(
                {
                    "value": value,
                    "name": name,
                    "region": region,
                    "selected": value == selected,
                }
            )
        return cards

    def clean_public_base_url(self):
        return (self.cleaned_data.get("public_base_url") or "").rstrip("/")

    def clean(self):
        data = super().clean()
        low, high = data.get("min_silence"), data.get("max_silence")
        if low and high and low >= high:
            self.add_error("max_silence", "Max silence must be longer than min silence.")
        return data

    def save(self, commit=True):
        profile = super().save(commit=False)
        raw = self.cleaned_data.get("keyterms_text", "")
        profile.keyterms = [line.strip() for line in raw.splitlines() if line.strip()]
        if commit:
            profile.save()
        return profile
