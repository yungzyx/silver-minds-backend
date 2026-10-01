"""Validación real de un audio: se inspecciona el contenido, no la extensión."""

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass

from app.core.errors import AppError, InvalidInputError

PROBE_TIMEOUT_SECONDS = 10
# Contenedores que ffprobe reconoce y que la transcripción acepta.
ALLOWED_FORMATS = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
    "flac": "audio/flac",
    "webm": "audio/webm",
    "matroska": "audio/webm",
    "mp4": "audio/mp4",
    "m4a": "audio/mp4",
    "mov": "audio/mp4",
}


class AudioValidationUnavailableError(AppError):
    status_code = 503
    code = "audio_validation_unavailable"


@dataclass(frozen=True)
class AudioInfo:
    content_type: str
    duration_seconds: float
    extension: str


def probe_audio(data: bytes) -> AudioInfo:
    """Devuelve formato y duración. Rechaza lo que no sea un audio reconocible."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        # Sin validador no se acepta nada: mejor rechazar que procesar a ciegas.
        raise AudioValidationUnavailableError("No se pueden validar audios en este momento.")
    with tempfile.NamedTemporaryFile(suffix=".audio") as handle:
        handle.write(data)
        handle.flush()
        try:
            # Lista de argumentos fija y sin shell; el archivo es temporal y propio.
            result = subprocess.run(  # noqa: S603
                [
                    ffprobe,
                    "-v", "error",
                    "-show_entries", "format=format_name,duration:stream=codec_type",
                    "-of", "json",
                    handle.name,
                ],
                capture_output=True,
                timeout=PROBE_TIMEOUT_SECONDS,
                check=False,
            )  # fmt: skip
        except subprocess.TimeoutExpired as exc:
            raise InvalidInputError("No se pudo leer el audio.") from exc
    if result.returncode != 0:
        raise InvalidInputError("El archivo no es un audio válido.")
    try:
        report = json.loads(result.stdout)
        names = report["format"]["format_name"].split(",")
        duration = float(report["format"]["duration"])
        has_audio = any(s.get("codec_type") == "audio" for s in report.get("streams", []))
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidInputError("El archivo no es un audio válido.") from exc
    known = next((name for name in names if name in ALLOWED_FORMATS), None)
    if known is None or not has_audio:
        raise InvalidInputError("Formato de audio no admitido.")
    return AudioInfo(
        content_type=ALLOWED_FORMATS[known],
        duration_seconds=duration,
        extension="webm" if known == "matroska" else ("m4a" if known == "mov" else known),
    )
