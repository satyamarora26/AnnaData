"""
Audio / image understanding via the Gemini Files API.

The browser records WebM (MediaRecorder's default), but the previous version
always wrote a .mp3 temp file. Gemini infers MIME type from the filename, so
WebM bytes were uploaded labelled as MP3. Extensions are now derived from the
upload's real content type.
"""
import os
import tempfile

from config import MEDIA_MODEL
from utils import get_client

AUDIO_EXTENSIONS = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/aac": ".aac",
    "audio/flac": ".flac",
}


def _audio_extension(filename: str | None, content_type: str | None) -> str:
    if content_type:
        base = content_type.split(";")[0].strip().lower()
        if base in AUDIO_EXTENSIONS:
            return AUDIO_EXTENSIONS[base]
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext in set(AUDIO_EXTENSIONS.values()):
            return ext
    return ".webm"  # MediaRecorder's default in Chrome and Firefox


def _image_extension(filename: str | None) -> str:
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext in {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}:
            return ext
    return ".jpg"


async def process_media(
    audio_bytes=None,
    image_bytes=None,
    extra_prompt=None,
    audio_filename=None,
    audio_content_type=None,
    image_filename=None,
):
    client = get_client()
    inputs = []

    def upload(data: bytes, suffix: str):
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            return client.files.upload(file=tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    if audio_bytes:
        inputs.append(upload(audio_bytes, _audio_extension(audio_filename, audio_content_type)))

    if image_bytes:
        inputs.append(upload(image_bytes, _image_extension(image_filename)))

    if extra_prompt:
        inputs.insert(0, extra_prompt)

    result = client.models.generate_content(model=MEDIA_MODEL, contents=inputs)
    return result.text
