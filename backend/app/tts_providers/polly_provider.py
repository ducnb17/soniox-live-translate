"""Amazon Polly TTS provider.

Neural TTS voices via AWS Polly REST API. One of the cheapest options for large volume.
Requires boto3 or REST calls with AWS SigV4 signing.
Pricing: verify at https://aws.amazon.com/polly/pricing/
"""

import hashlib
import hmac
import urllib.parse
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from ..tts_provider import TTSProviderBase, Voice, TTSProviderInfo, register_provider
from ..logging_config import get_logger
from ..provider_connection import test_get

log = get_logger("polly_tts")

POLLY_URL = "https://polly.us-east-1.amazonaws.com/v1/speech"
POLLY_VOICES = [
    "Joanna", "Matthew", "Salli", "Kimberly", "Kendra", "Justin", "Joey", "Ivy",
    "Ruth", "Stephen", "Kevin", "Amy", "Brian", "Arthur", "Aria", "Ayanda",
    "Camila", "Daniel", "Elena", "Emma", "Geraint", "Gregory", "Hala",
    "Hannah", "Hiujin", "Ines", "Kajal", "Laura", "Lea", "Lucia", "Maja",
    "Marlene", "Nicole", "Olivia", "Pedro", "Raveena", "Ricardo", "Seoyeon",
    "Takumi", "Tomoko", "Vicki", "Vitoria", "Zayd", "Zeina", "Zhiyu",
]


@register_provider
class PollyProvider(TTSProviderBase):
    def __init__(self, api_key: str | None = None) -> None:
        # For Polly, api_key is "access_key_id:secret_access_key"
        self._api_key = api_key
        self._region = "us-east-1"

    async def test_connection(self) -> tuple[bool, str]:
        if not self._api_key or ":" not in self._api_key:
            return False, "Amazon Polly requires access_key_id:secret_access_key format"
        access_key, secret_key = self._api_key.split(":", 1)
        now = datetime.now(timezone.utc)
        timestamp = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        host = f"polly.{self._region}.amazonaws.com"
        endpoint = f"https://{host}/v1/voices"
        headers = {"Host": host, "X-Amz-Date": timestamp}
        signed_headers = self._sign_v4(
            method="GET", endpoint=endpoint, region=self._region,
            service="polly", headers=headers, payload=b"",
            access_key=access_key, secret_key=secret_key,
            timestamp=timestamp, datestamp=datestamp,
        )
        return await test_get(endpoint, headers=signed_headers)

    async def list_voices(self, lang: str | None = None) -> list[Voice]:
        return [
            Voice(id=v, name=v, language=lang or "en", gender="neutral", provider_id="polly")
            for v in POLLY_VOICES
        ]

    async def synthesize_stream(self, text: str, voice_id: str, lang: str) -> AsyncIterator[bytes]:
        if not self._api_key or ":" not in self._api_key:
            raise ValueError("Amazon Polly requires access_key_id:secret_access_key format")

        access_key, secret_key = self._api_key.split(":", 1)

        payload = urllib.parse.urlencode({
            "Text": text,
            "VoiceId": voice_id,
            "OutputFormat": "pcm",
            "SampleRate": "24000",
            "Engine": "neural",
            "TextType": "text",
        }).encode()

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        datestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        service = "polly"
        host = f"polly.{self._region}.amazonaws.com"
        endpoint = f"https://{host}/v1/speech"
        content_type = "application/x-www-form-urlencoded"

        headers = {
            "Content-Type": content_type,
            "Host": host,
            "X-Amz-Date": timestamp,
        }

        signed_headers = self._sign_v4(
            method="POST", endpoint=endpoint, region=self._region,
            service=service, headers=headers, payload=payload,
            access_key=access_key, secret_key=secret_key,
            timestamp=timestamp, datestamp=datestamp,
        )

        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            resp = await client.post(endpoint, content=payload, headers=signed_headers)
            resp.raise_for_status()
            yield resp.content

    def _sign_v4(self, method, endpoint, region, service, headers, payload,
                 access_key, secret_key, timestamp, datestamp):
        parsed = urllib.parse.urlparse(endpoint)
        canonical_uri = parsed.path or "/"
        canonical_querystring = parsed.query or ""

        sorted_header_keys = sorted(headers.keys(), key=str.lower)
        canonical_headers = "".join(
            f"{k.lower()}:{headers[k].strip()}\n" for k in sorted_header_keys
        )
        signed_headers_str = ";".join(k.lower() for k in sorted_header_keys)

        payload_hash = hashlib.sha256(payload).hexdigest()

        canonical_request = (
            f"{method}\n{canonical_uri}\n{canonical_querystring}\n"
            f"{canonical_headers}\n{signed_headers_str}\n{payload_hash}"
        )

        algorithm = "AWS4-HMAC-SHA256"
        credential_scope = f"{datestamp}/{region}/{service}/aws4_request"
        string_to_sign = (
            f"{algorithm}\n{timestamp}\n{credential_scope}\n"
            f"{hashlib.sha256(canonical_request.encode()).hexdigest()}"
        )

        def sign(key, data):
            return hmac.new(key, data.encode(), hashlib.sha256).digest()

        k_date = sign(("AWS4" + secret_key).encode(), datestamp)
        k_region = sign(k_date, region)
        k_service = sign(k_region, service)
        k_signing = sign(k_service, "aws4_request")

        signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()

        auth_header = (
            f"{algorithm} Credential={access_key}/{credential_scope}, "
            f"SignedHeaders={signed_headers_str}, Signature={signature}"
        )
        result = dict(headers)
        result["Authorization"] = auth_header
        return result

    def estimate_cost(self, char_count: int) -> float:
        # Neural: ~$16/million chars (cheapest neural option at scale)
        # Verify at https://aws.amazon.com/polly/pricing/
        return char_count * 0.000016

    @property
    def info(self) -> TTSProviderInfo:
        return TTSProviderInfo(
            id="polly",
            name="Amazon Polly (Neural)",
            description="AWS neural TTS, 40+ voices. Cheap at scale. No true streaming.",
            requires_api_key=True,
            supports_streaming=False,
            tier="cheap",
            pricing_url="https://aws.amazon.com/polly/pricing/",
            approximate_cost_per_1m_chars=16.0,
        )
