import os
import uuid
import shutil
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, Form
from faster_whisper import WhisperModel

WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_CPU_THREADS = int(os.getenv("WHISPER_CPU_THREADS", "4"))

TMP_DIR = Path("/tmp/whisper")
TMP_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="Internal Faster Whisper API")

model = WhisperModel(
    WHISPER_MODEL,
    device=WHISPER_DEVICE,
    compute_type=WHISPER_COMPUTE_TYPE,
    cpu_threads=WHISPER_CPU_THREADS,
)


@app.get("/health")
def health():
    return {
        "ok": True,
        "model": WHISPER_MODEL,
        "device": WHISPER_DEVICE,
        "compute_type": WHISPER_COMPUTE_TYPE,
        "cpu_threads": WHISPER_CPU_THREADS,
    }


@app.post("/transcribe")
async def transcribe(
    file: UploadFile = File(...),
    language: str | None = Form(default=None),
    task: str = Form(default="transcribe")
):
    suffix = Path(file.filename or "audio").suffix or ".mp3"
    temp_path = TMP_DIR / f"{uuid.uuid4()}{suffix}"

    try:
        with temp_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        segments, info = model.transcribe(
            str(temp_path),
            language=language,
            task=task,
            beam_size=5,
            vad_filter=True,
        )

        result_segments = []
        full_text_parts = []

        for segment in segments:
            text = segment.text.strip()
            full_text_parts.append(text)

            result_segments.append({
                "start": segment.start,
                "end": segment.end,
                "text": text,
            })

        return {
            "ok": True,
            "filename": file.filename,
            "language": info.language,
            "language_probability": info.language_probability,
            "duration": info.duration,
            "text": " ".join(full_text_parts).strip(),
            "segments": result_segments,
        }

    finally:
        if temp_path.exists():
            temp_path.unlink()