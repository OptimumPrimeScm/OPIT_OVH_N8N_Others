from __future__ import annotations

import asyncio
import json
import math
import os
import re
import secrets
import shutil
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse


SERVICE_NAME = "collabteams-audio-slicer"
WORK_DIR = Path(os.getenv("AUDIO_SLICER_WORK_DIR", "/work")).resolve()
MAX_UPLOAD_BYTES = int(os.getenv("AUDIO_SLICER_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))
MAX_CHUNK_BYTES = int(os.getenv("AUDIO_SLICER_MAX_CHUNK_BYTES", str(8 * 1024 * 1024)))
TARGET_CHUNK_BYTES = int(os.getenv("AUDIO_SLICER_TARGET_CHUNK_BYTES", str(int(7.25 * 1024 * 1024))))
DEFAULT_OVERLAP_SECONDS = float(os.getenv("AUDIO_SLICER_OVERLAP_SECONDS", "1"))
MAX_CHUNKS = int(os.getenv("AUDIO_SLICER_MAX_CHUNKS", "100"))
JOB_TTL_SECONDS = int(os.getenv("AUDIO_SLICER_JOB_TTL_SECONDS", "3600"))
FFMPEG_TIMEOUT_SECONDS = int(os.getenv("AUDIO_SLICER_FFMPEG_TIMEOUT_SECONDS", "600"))
FFPROBE_TIMEOUT_SECONDS = int(os.getenv("AUDIO_SLICER_FFPROBE_TIMEOUT_SECONDS", "120"))
MAX_CONCURRENT_JOBS = int(os.getenv("AUDIO_SLICER_MAX_CONCURRENT_JOBS", "2"))
API_KEY = os.getenv("AUDIO_SLICER_API_KEY", "").strip()
ENCODED_BYTES_PER_SECOND = 6_000  # 48 kbit/s

WORK_DIR.mkdir(parents=True, exist_ok=True)
job_slots = asyncio.Semaphore(MAX_CONCURRENT_JOBS)

app = FastAPI(
    title="CollabTeams Audio Slicer",
    version="1.0.0",
    description="Internal FFmpeg service that prepares bounded audio chunks for Whisper.",
)


def require_api_key(
    x_audio_slicer_key: Annotated[str | None, Header()] = None,
) -> None:
    if API_KEY and (not x_audio_slicer_key or not secrets.compare_digest(x_audio_slicer_key, API_KEY)):
        raise HTTPException(status_code=401, detail="Invalid or missing audio slicer API key")


def safe_filename(value: str | None) -> str:
    candidate = Path(value or "audio.bin").name
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", candidate).strip("._")
    return candidate[:180] or "audio.bin"


def job_directory(job_id: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", job_id):
        raise HTTPException(status_code=404, detail="Audio slicing job not found")
    return WORK_DIR / job_id


def manifest_path(job_id: str) -> Path:
    return job_directory(job_id) / "manifest.json"


def read_manifest(job_id: str) -> dict[str, Any]:
    path = manifest_path(job_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio slicing job not found or expired")
    return json.loads(path.read_text(encoding="utf-8"))


def cleanup_expired_jobs() -> int:
    cutoff = time.time() - JOB_TTL_SECONDS
    removed = 0
    for child in WORK_DIR.iterdir():
        if not child.is_dir():
            continue
        try:
            if child.stat().st_mtime < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except FileNotFoundError:
            continue
    return removed


async def run_process(command: str, arguments: list[str], timeout: int) -> str:
    process = await asyncio.create_subprocess_exec(
        command,
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError as error:
        process.kill()
        await process.communicate()
        raise HTTPException(status_code=504, detail=f"{command} exceeded its {timeout}-second timeout") from error

    if process.returncode != 0:
        message = stderr.decode("utf-8", errors="replace")[-3000:].strip()
        raise HTTPException(status_code=422, detail=f"{command} failed: {message}")
    return stdout.decode("utf-8", errors="replace").strip()


async def audio_duration_seconds(source_path: Path) -> float:
    output = await run_process(
        "ffprobe",
        [
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(source_path),
        ],
        FFPROBE_TIMEOUT_SECONDS,
    )
    try:
        duration = float(output)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="ffprobe did not return a valid duration") from error
    if not math.isfinite(duration) or duration <= 0:
        raise HTTPException(status_code=422, detail="Audio duration must be greater than zero")
    return duration


async def save_upload(upload: UploadFile, destination: Path) -> int:
    total = 0
    with destination.open("wb") as output:
        while data := await upload.read(1024 * 1024):
            total += len(data)
            if total > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Audio exceeds the {MAX_UPLOAD_BYTES}-byte upload limit",
                )
            output.write(data)
    if total == 0:
        raise HTTPException(status_code=422, detail="Uploaded audio is empty")
    return total


async def encode_chunks(
    source_path: Path,
    output_directory: Path,
    original_stem: str,
    duration: float,
    source_size: int,
    overlap_seconds: float,
) -> list[dict[str, Any]]:
    count_by_source_size = math.ceil(source_size / TARGET_CHUNK_BYTES)
    count_by_encoded_size = math.ceil((duration * ENCODED_BYTES_PER_SECOND) / TARGET_CHUNK_BYTES)
    chunk_count = max(2, count_by_source_size, count_by_encoded_size)

    while True:
        if chunk_count > MAX_CHUNKS:
            raise HTTPException(
                status_code=422,
                detail=f"Audio requires more than the {MAX_CHUNKS}-chunk safety limit",
            )

        for previous_chunk in output_directory.glob("*.mp3"):
            previous_chunk.unlink(missing_ok=True)

        base_duration = duration / chunk_count
        chunks: list[dict[str, Any]] = []
        largest_chunk = 0

        for index in range(chunk_count):
            nominal_start = index * base_duration
            start = max(0.0, nominal_start - (overlap_seconds if index > 0 else 0.0))
            nominal_end = min(duration, (index + 1) * base_duration)
            segment_duration = max(0.1, nominal_end - start)
            filename = (
                f"{original_stem}.part-{index + 1:03d}-of-{chunk_count:03d}.mp3"
            )
            output_path = output_directory / filename

            await run_process(
                "ffmpeg",
                [
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-nostdin",
                    "-y",
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    str(source_path),
                    "-t",
                    f"{segment_duration:.3f}",
                    "-map",
                    "0:a:0",
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "48k",
                    str(output_path),
                ],
                FFMPEG_TIMEOUT_SECONDS,
            )

            chunk_size = output_path.stat().st_size
            largest_chunk = max(largest_chunk, chunk_size)
            chunks.append(
                {
                    "index": index,
                    "number": index + 1,
                    "count": chunk_count,
                    "filename": filename,
                    "content_type": "audio/mpeg",
                    "size_bytes": chunk_size,
                    "start_seconds": round(start, 3),
                    "duration_seconds": round(segment_duration, 3),
                    "overlap_seconds": overlap_seconds if index > 0 else 0.0,
                }
            )

        if largest_chunk <= MAX_CHUNK_BYTES:
            return chunks

        growth_factor = max(1.1, largest_chunk / MAX_CHUNK_BYTES * 1.05)
        chunk_count = max(chunk_count + 1, math.ceil(chunk_count * growth_factor))


@app.get("/health")
async def health() -> dict[str, Any]:
    ffmpeg_version, ffprobe_version = await asyncio.gather(
        run_process("ffmpeg", ["-version"], 10),
        run_process("ffprobe", ["-version"], 10),
    )
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "ffmpeg": ffmpeg_version.splitlines()[0],
        "ffprobe": ffprobe_version.splitlines()[0],
    }


@app.post("/v1/audio/split", dependencies=[Depends(require_api_key)])
async def split_audio(
    file: Annotated[UploadFile, File(...)],
    overlap_seconds: Annotated[float | None, Form()] = None,
) -> dict[str, Any]:
    cleanup_expired_jobs()
    requested_overlap = DEFAULT_OVERLAP_SECONDS if overlap_seconds is None else overlap_seconds
    if requested_overlap < 0 or requested_overlap > 10:
        raise HTTPException(status_code=422, detail="overlap_seconds must be between 0 and 10")

    job_id = uuid.uuid4().hex
    directory = job_directory(job_id)
    chunks_directory = directory / "chunks"
    directory.mkdir(parents=True)
    chunks_directory.mkdir()
    original_filename = safe_filename(file.filename)
    source_path = directory / f"source{Path(original_filename).suffix.lower() or '.bin'}"

    try:
        async with job_slots:
            source_size = await save_upload(file, source_path)
            duration = await audio_duration_seconds(source_path)
            original_stem = safe_filename(Path(original_filename).stem)

            if source_size <= MAX_CHUNK_BYTES:
                chunk_filename = original_filename
                chunk_path = chunks_directory / chunk_filename
                shutil.move(source_path, chunk_path)
                chunks = [
                    {
                        "index": 0,
                        "number": 1,
                        "count": 1,
                        "filename": chunk_filename,
                        "content_type": file.content_type or "application/octet-stream",
                        "size_bytes": source_size,
                        "start_seconds": 0.0,
                        "duration_seconds": round(duration, 3),
                        "overlap_seconds": 0.0,
                    }
                ]
                strategy = "direct"
            else:
                chunks = await encode_chunks(
                    source_path,
                    chunks_directory,
                    original_stem,
                    duration,
                    source_size,
                    requested_overlap,
                )
                source_path.unlink(missing_ok=True)
                strategy = "ffmpeg-mono-16khz-48k"

        created_at = datetime.now(UTC)
        for chunk in chunks:
            chunk["download_path"] = f"/v1/audio/jobs/{job_id}/chunks/{chunk['index']}"

        manifest = {
            "job_id": job_id,
            "status": "ready",
            "strategy": strategy,
            "original_filename": original_filename,
            "original_size_bytes": source_size,
            "original_duration_seconds": round(duration, 3),
            "max_chunk_bytes": MAX_CHUNK_BYTES,
            "target_chunk_bytes": TARGET_CHUNK_BYTES,
            "created_at": created_at.isoformat(),
            "expires_at": (created_at + timedelta(seconds=JOB_TTL_SECONDS)).isoformat(),
            "chunks": chunks,
        }
        manifest_path(job_id).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest
    except Exception:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    finally:
        await file.close()


@app.get("/v1/audio/jobs/{job_id}", dependencies=[Depends(require_api_key)])
async def get_job(job_id: str) -> dict[str, Any]:
    cleanup_expired_jobs()
    return read_manifest(job_id)


@app.get(
    "/v1/audio/jobs/{job_id}/chunks/{chunk_index}",
    dependencies=[Depends(require_api_key)],
)
async def download_chunk(job_id: str, chunk_index: int) -> FileResponse:
    cleanup_expired_jobs()
    manifest = read_manifest(job_id)
    chunks = manifest.get("chunks", [])
    if chunk_index < 0 or chunk_index >= len(chunks):
        raise HTTPException(status_code=404, detail="Audio chunk not found")
    chunk = chunks[chunk_index]
    path = job_directory(job_id) / "chunks" / chunk["filename"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Audio chunk has expired")
    return FileResponse(
        path,
        media_type=chunk["content_type"],
        filename=chunk["filename"],
    )


@app.delete("/v1/audio/jobs/{job_id}", dependencies=[Depends(require_api_key)])
async def delete_job(job_id: str) -> dict[str, Any]:
    directory = job_directory(job_id)
    if not directory.exists():
        return {"status": "already_deleted", "job_id": job_id}
    shutil.rmtree(directory)
    return {"status": "deleted", "job_id": job_id}
