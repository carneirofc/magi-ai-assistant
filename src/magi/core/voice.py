"""Voice sidecars — TTS and STT over OpenAI-compatible local servers.

Two host services join the chat backend (llama-server) rather than living in
the model: a TTS server speaking `POST {tts_base_url}/audio/speech`
(Kokoro-FastAPI-class) gives the assistant a voice, and an STT server speaking
`POST {stt_base_url}/audio/transcriptions` (whisper-class) lets clients send
recorded speech. The chat API fronts them at `/v1/tts` and `/v1/stt`
(magi/channels/api.py) so browser clients never talk to the sidecars directly.

The reply's mood (magi/agent/mood.py — designed from day one as the TTS style
input) selects a per-mood override from `tts_mood_styles`, so the voice's
delivery tracks the face: e.g. `{"wry": {"speed": 0.95}}` merged onto the
speech request.

Pure I/O: httpx only, no model or agno dependencies. Failures are typed so the
API layer can answer honestly — `VoiceUnavailable` (capability off, sidecar
unreachable) maps to 503, `VoiceUpstreamError` (sidecar answered non-200) to
502. Nothing here retries beyond the one STT format fallback; the sidecars are
local and either up or not.
"""

from dataclasses import dataclass
from typing import Any, Optional

import httpx
from agno.utils.log import log_warning


class VoiceUnavailable(RuntimeError):
    """The capability is disabled here, or its sidecar could not be reached."""


class VoiceUpstreamError(RuntimeError):
    """The sidecar was reached but answered with an error."""


# response_format -> mime, for when the sidecar omits a Content-Type.
_FORMAT_MIME = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "opus": "audio/ogg",
    "flac": "audio/flac",
    "aac": "audio/aac",
    "pcm": "audio/pcm",
}


@dataclass(frozen=True)
class Transcription:
    """What STT heard. `language`/`duration` ride along when the sidecar
    reports them (verbose_json); plain `json` servers yield text only."""

    text: str
    language: Optional[str] = None
    duration: Optional[float] = None


class VoiceService:
    """Thin client over the two sidecars. Either side may be absent — a
    deployment can speak without hearing and vice versa; the properties say
    which capabilities are live so callers can 503 the other."""

    def __init__(
        self,
        *,
        tts_base_url: Optional[str] = None,
        tts_api_key: Optional[str] = None,
        tts_model: str = "tts-1",
        tts_voice: str = "af_heart",
        tts_format: str = "mp3",
        tts_mood_styles: Optional[dict[str, dict[str, Any]]] = None,
        stt_base_url: Optional[str] = None,
        stt_api_key: Optional[str] = None,
        stt_model: str = "whisper-1",
        stt_language: Optional[str] = None,
        timeout: float = 60.0,
    ) -> None:
        self._tts_base_url = tts_base_url.rstrip("/") if tts_base_url else None
        self._tts_api_key = tts_api_key
        self._tts_model = tts_model
        self._tts_voice = tts_voice
        self._tts_format = tts_format
        self._tts_mood_styles = tts_mood_styles or {}
        self._stt_base_url = stt_base_url.rstrip("/") if stt_base_url else None
        self._stt_api_key = stt_api_key
        self._stt_model = stt_model
        self._stt_language = stt_language
        self._timeout = timeout

    @property
    def tts_enabled(self) -> bool:
        return self._tts_base_url is not None

    @property
    def stt_enabled(self) -> bool:
        return self._stt_base_url is not None

    @staticmethod
    def _headers(api_key: Optional[str]) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    async def synthesize(self, text: str, mood: Optional[str] = None) -> tuple[bytes, str]:
        """Render `text` to audio bytes; returns (audio, mime).

        `mood` picks the style override from `tts_mood_styles` — its keys are
        merged onto the request payload (voice, speed, response_format, …), so
        a mood can reshape anything but the text itself. Unknown moods fall
        back to the base voice, mirroring how clients fall back to neutral art.
        """
        if self._tts_base_url is None:
            raise VoiceUnavailable("tts is not enabled in this deployment")

        payload: dict[str, Any] = {
            "model": self._tts_model,
            "voice": self._tts_voice,
            "input": text,
            "response_format": self._tts_format,
        }
        style = self._tts_mood_styles.get(mood) if mood else None
        if style:
            payload.update({k: v for k, v in style.items() if k not in ("input", "model")})

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._tts_base_url}/audio/speech",
                    json=payload,
                    headers=self._headers(self._tts_api_key),
                )
        except httpx.HTTPError as exc:
            raise VoiceUnavailable(f"tts sidecar unreachable: {exc}") from exc
        if resp.status_code != 200:
            raise VoiceUpstreamError(f"tts sidecar answered {resp.status_code}: {resp.text[:200]}")

        mime = (resp.headers.get("content-type") or "").split(";")[0].strip()
        if not mime or mime == "application/octet-stream":
            mime = _FORMAT_MIME.get(str(payload["response_format"]), "application/octet-stream")
        return resp.content, mime

    async def transcribe(
        self,
        data: bytes,
        *,
        filename: str = "audio.webm",
        mime: str = "audio/webm",
        language: Optional[str] = None,
    ) -> Transcription:
        """Transcribe recorded audio to text.

        Asks for `verbose_json` first (language + duration ride along on
        OpenAI-compatible whisper servers) and falls back once to plain `json`
        for sidecars that only know the minimal shape.
        """
        if self._stt_base_url is None:
            raise VoiceUnavailable("stt is not enabled in this deployment")

        lang = language or self._stt_language
        form: dict[str, Any] = {"model": self._stt_model, "response_format": "verbose_json"}
        if lang:
            form["language"] = lang

        resp = await self._post_transcription(data, filename, mime, form)
        if resp.status_code >= 400 and resp.status_code < 500:
            # Minimal servers reject verbose_json; retry once with the plain shape.
            log_warning(
                f"stt: verbose_json rejected ({resp.status_code}), retrying with response_format=json"
            )
            form["response_format"] = "json"
            resp = await self._post_transcription(data, filename, mime, form)
        if resp.status_code != 200:
            raise VoiceUpstreamError(f"stt sidecar answered {resp.status_code}: {resp.text[:200]}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise VoiceUpstreamError(f"stt sidecar returned non-JSON: {resp.text[:200]}") from exc
        text = body.get("text")
        if not isinstance(text, str):
            raise VoiceUpstreamError("stt sidecar returned no text field")
        duration = body.get("duration")
        return Transcription(
            text=text.strip(),
            language=body.get("language") or None,
            duration=float(duration) if isinstance(duration, (int, float)) else None,
        )

    async def _post_transcription(
        self, data: bytes, filename: str, mime: str, form: dict[str, Any]
    ) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                return await client.post(
                    f"{self._stt_base_url}/audio/transcriptions",
                    files={"file": (filename, data, mime)},
                    data=form,
                    headers=self._headers(self._stt_api_key),
                )
        except httpx.HTTPError as exc:
            raise VoiceUnavailable(f"stt sidecar unreachable: {exc}") from exc


def build_voice_service() -> Optional[VoiceService]:
    """Composition helper: the `VoiceService` this deployment configured, or
    None when both sides are off (the API then 503s /v1/tts and /v1/stt)."""
    from magi.core.config import config

    if not (config.tts_enabled or config.stt_enabled):
        return None
    return VoiceService(
        tts_base_url=config.tts_base_url if config.tts_enabled else None,
        tts_api_key=config.tts_api_key,
        tts_model=config.tts_model,
        tts_voice=config.tts_voice,
        tts_format=config.tts_format,
        tts_mood_styles=config.tts_mood_styles,
        stt_base_url=config.stt_base_url if config.stt_enabled else None,
        stt_api_key=config.stt_api_key,
        stt_model=config.stt_model,
        stt_language=config.stt_language,
        timeout=config.voice_timeout_seconds,
    )
