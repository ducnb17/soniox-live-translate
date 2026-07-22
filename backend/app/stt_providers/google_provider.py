"""Google Cloud Speech-to-Text V2 (Chirp 3 HD) — real-time streaming provider.

The provider implements the STTProviderBase interface so it can be selected
from the frontend and used in the ``/ws/translate`` pipeline. Credentials
are stored via ``config_store.set_stt_api_key("google_v2", service_account_json)``.
"""

import asyncio
import json
import os
import threading
from typing import Any, AsyncIterator

from ..config import STT_MODEL
from ..logging_config import get_logger
from ..stt_provider import STTProviderBase, STTProviderInfo, register_provider

log = get_logger("stt.google_v2")


def _resolve_project_id(creds_json: str) -> str:
    if creds_json.strip().startswith("{"):
        try:
            info = json.loads(creds_json)
            return str(info.get("project_id", ""))
        except json.JSONDecodeError:
            pass
    pid = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if pid:
        return pid
    return ""


def _parse_credentials(api_key: str | None) -> str | None:
    """Resolve credentials from stored key or environment."""
    if not api_key:
        api_key = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if not api_key:
        return None
    key = api_key.strip()
    if key.startswith("{"):
        return key  # inline JSON
    if os.path.isfile(key):
        return key  # file path
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
        return os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    return key  # might be a project_id or other identifier


@register_provider
class GoogleSTTProvider(STTProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    async def test_connection(self) -> tuple[bool, str]:
        creds = _parse_credentials(self._api_key)
        if not creds:
            return (
                False,
                "Google Cloud credentials required. Set GOOGLE_APPLICATION_CREDENTIALS "
                "env var or paste the service-account JSON in the API key field.",
            )
        try:
            from google.cloud.speech_v2 import SpeechClient
            from google.cloud.speech_v2.types import cloud_speech

            project_id = _resolve_project_id(creds)
            if not project_id:
                return False, "Could not resolve Google Cloud project ID from credentials"

            client = self._make_client(creds)
            request = cloud_speech.ListRecognizersRequest(
                parent=f"projects/{project_id}/locations/global",
            )
            list(client.list_recognizers(request=request))
            return True, "OK"
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    async def transcribe_stream(
        self,
        audio_stream: AsyncIterator[bytes],
        **options: Any,
    ) -> AsyncIterator[dict[str, Any]]:
        """Real-time STT via Google Chirp 3 HD.  Yields Soniox-shaped token dicts.

        ``handle_stt`` in ``stt.py`` consumes these dicts unmodified.
        """
        creds = _parse_credentials(self._api_key)
        if not creds:
            raise RuntimeError(
                "Google Cloud credentials required. Set GOOGLE_APPLICATION_CREDENTIALS "
                "env var or paste the service-account JSON in the API key field."
            )

        language_codes = options.get("language_codes", options.get("language_hints", ["en-US"]))
        if isinstance(language_codes, str):
            language_codes = [language_codes]
        if not language_codes:
            language_codes = ["en-US"]

        region = str(options.get("region", "us"))
        project_id = _resolve_project_id(creds)
        if not project_id:
            raise RuntimeError("Could not resolve Google Cloud project ID from credentials")

        # ------------------------------------------------------------------ #
        # Build config
        # ------------------------------------------------------------------ #
        from google.cloud.speech_v2 import SpeechClient
        from google.cloud.speech_v2.types import cloud_speech

        recognition_config = cloud_speech.RecognitionConfig(
            auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),
            language_codes=language_codes,
            model="chirp_3",
            features=cloud_speech.RecognitionFeatures(
                enable_automatic_punctuation=True,
            ),
        )

        streaming_config = cloud_speech.StreamingRecognitionConfig(
            config=recognition_config,
            streaming_features=cloud_speech.StreamingRecognitionFeatures(
                interim_results=True,
                enable_voice_activity_events=True,
            ),
        )

        config_request = cloud_speech.StreamingRecognizeRequest(
            recognizer=f"projects/{project_id}/locations/{region}/recognizers/_",
            streaming_config=streaming_config,
        )

        # ------------------------------------------------------------------ #
        # Thread bridge: sync gRPC → async queue
        # ------------------------------------------------------------------ #
        client = self._make_client(creds, region)

        # queue of mapped token dicts + sentinel (None = done)
        token_queue: asyncio.Queue[dict[str, Any] | BaseException | None] = asyncio.Queue(maxsize=256)
        audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=256)
        done = threading.Event()
        _error: BaseException | None = None

        def _run_grpc() -> None:
            nonlocal _error
            try:
                def request_gen():
                    yield config_request
                    while True:
                        chunk = audio_queue.get()  # blocks in thread
                        if chunk is None:  # sentinel
                            break
                        yield cloud_speech.StreamingRecognizeRequest(audio=bytes(chunk))

                for response in client.streaming_recognize(requests=request_gen()):
                    try:
                        mapped = self._map_response(response)
                    except Exception:
                        continue
                    if mapped is not None:
                        try:
                            token_queue.put_nowait(mapped)
                        except asyncio.QueueFull:
                            # Drop if consumer is too slow (shouldn't happen).
                            pass
                token_queue.put_nowait(None)  # stream done
            except Exception as exc:
                _error = exc
                try:
                    token_queue.put_nowait(exc)
                except asyncio.QueueFull:
                    pass
            finally:
                done.set()

        thread = threading.Thread(target=_run_grpc, daemon=True)
        thread.start()

        # ------------------------------------------------------------------ #
        # Feed audio from the caller side
        # ------------------------------------------------------------------ #
        async def feed_audio() -> None:
            try:
                async for chunk in audio_stream:
                    if chunk:
                        await audio_queue.put(bytes(chunk))
            finally:
                await audio_queue.put(None)  # signal end of audio

        feed_task = asyncio.ensure_future(feed_audio())

        # ------------------------------------------------------------------ #
        # Yield tokens back to handle_stt
        # ------------------------------------------------------------------ #
        try:
            while True:
                item = await token_queue.get()
                if item is None:
                    break
                if isinstance(item, BaseException):
                    if done.is_set():
                        break
                    raise item
                if isinstance(item, dict):
                    yield item
        finally:
            if not feed_task.done():
                feed_task.cancel()
                with asyncio.suppress(asyncio.CancelledError, RuntimeError):
                    await feed_task
            done.set()
            thread.join(timeout=5)

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _map_response(self, response: Any) -> dict[str, Any] | None:
        """Convert a Google StreamingRecognizeResponse into a Soniox-shaped token."""
        from google.cloud.speech_v2.types.cloud_speech import StreamingRecognizeResponse

        result = None

        if response.results:
            for r in response.results:
                if not r.alternatives:
                    continue
                alt = r.alternatives[0]
                if not alt.transcript:
                    continue
                result = {
                    "text": alt.transcript.strip() or "",
                    "is_final": r.is_final,
                    "language": r.language_code or "",
                    "translation_status": "none",
                    "speaker": None,
                }

        # Voice-activity-events → insert a synthetic <end> token so
        # handle_stt sees an utterance boundary.
        # SPEECH_ACTIVITY_BEGIN / END are the enum values.
        speech_event = getattr(response, "speech_event_type", None)
        if speech_event is not None:
            from google.cloud.speech_v2.types.cloud_speech import StreamingRecognizeResponse as SRR

            if speech_event in (
                SRR.SpeechEventType.SPEECH_ACTIVITY_END,
                getattr(SRR.SpeechEventType, "SPEECH_ACTIVITY_END", 2),
            ):
                return {
                    "text": "<end>",
                    "is_final": True,
                    "language": "",
                    "translation_status": "none",
                }

        if result is None:
            return None

        return result

    def _make_client(self, creds_json: str, region: str = "us"):
        from google.api_core.client_options import ClientOptions
        from google.cloud.speech_v2 import SpeechClient
        from google.oauth2 import service_account

        if creds_json.strip().startswith("{"):
            info = json.loads(creds_json)
            creds = service_account.Credentials.from_service_account_info(info)
        else:
            creds = service_account.Credentials.from_service_account_file(creds_json)

        return SpeechClient(
            credentials=creds,
            client_options=ClientOptions(
                api_endpoint=f"{region}-speech.googleapis.com",
            ),
        )

    @property
    def info(self) -> STTProviderInfo:
        return STTProviderInfo(
            id="google_v2",
            name="Google Cloud STT V2 (Chirp 3 HD)",
            description=(
                "Real-time streaming speech recognition via Chirp 3 HD model. "
                "Requires a Google Cloud project with the Speech-to-Text API enabled "
                "and a service account JSON key."
            ),
            requires_api_key=True,
            supports_streaming=True,
            supports_realtime_translation=False,
            tier="premium",
            pricing_url="https://cloud.google.com/speech-to-text/pricing",
            approximate_cost_per_hour=0.96,
        )


# ── Stream adapter that looks like a WebSocket to ``handle_stt`` ──────


class GoogleSttStream:
    """Bidirectional adapter: sends audio into the Google V2 streaming gRPC
    pipe and receives JSON-encoded token dicts back, so the existing
    ``stt.py::handle_stt`` and ``main.py::pipe_browser_to_stt`` can consume
    it without any code change."""

    def __init__(
        self,
        provider: GoogleSTTProvider,
        language_hints: list[str] | None = None,
    ) -> None:
        self._provider = provider
        self._language_hints = language_hints
        self._audio_in: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._tokens_out: asyncio.Queue[str | None | BaseException] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def open(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        async def audio_gen() -> AsyncIterator[bytes]:
            while True:
                chunk = await self._audio_in.get()
                # b"" means end-of-audio from stream_url_to_stt
                if chunk is None or chunk == b"":
                    break
                yield chunk

        try:
            async for token_d in self._provider.transcribe_stream(
                audio_gen(),
                language_hints=self._language_hints,
            ):
                await self._tokens_out.put(json.dumps(token_d))
        except Exception as exc:
            await self._tokens_out.put(exc)
        finally:
            await self._tokens_out.put(None)  # sentinel = stream done

    async def send(self, data: str | bytes) -> None:
        if isinstance(data, bytes):
            await self._audio_in.put(data)
        # Text messages (keepalive / config) are silently dropped — Google
        # handles keepalive at the gRPC layer.

    async def recv(self) -> str:
        item = await self._tokens_out.get()
        if item is None:
            raise ConnectionError("Google STT stream ended")
        if isinstance(item, BaseException):
            raise item
        return item

    async def close(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with asyncio.suppress(asyncio.CancelledError):
                await self._task
        # Drain queues so no task is blocked.
        while not self._audio_in.empty():
            self._audio_in.get_nowait()
            self._audio_in.task_done()
        try:
            await self._audio_in.put(None)
        except Exception:
            pass
