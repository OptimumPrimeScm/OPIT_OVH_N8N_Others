# CollabTeams Audio Slicer

Internal HTTP service for preparing large audio files before they are sent to Whisper. FFmpeg and FFprobe live only in this container; n8n and Ollama do not need either executable.

## API

- `GET /health`: validates the service, FFmpeg and FFprobe.
- `POST /v1/audio/split`: accepts multipart field `file` and optional `overlap_seconds`.
- `GET /v1/audio/jobs/{job_id}`: returns the job manifest.
- `GET /v1/audio/jobs/{job_id}/chunks/{zero_based_index}`: streams one chunk.
- `DELETE /v1/audio/jobs/{job_id}`: removes the temporary job immediately.

When `AUDIO_SLICER_API_KEY` is non-empty, every `/v1` request must include `X-Audio-Slicer-Key`. Health checks intentionally require no key.

Files no larger than 8 MiB are returned unchanged as a one-chunk job. Larger files are normalized to mono, 16 kHz, 48 kbit/s MP3 and divided into ordered chunks. Each generated file is verified against the hard 8 MiB ceiling. If an encoded chunk exceeds the ceiling, the service automatically retries with more chunks.

The response contains paths rather than base64 audio. This allows n8n to download and transcribe one chunk at a time without retaining all audio slices in one execution item.

## n8n request sequence

1. POST binary property `file` to `http://audio-slicer:8000/v1/audio/split`.
2. Expand the returned `chunks` array into n8n items.
3. Loop with batch size `1`.
4. Download `http://audio-slicer:8000` plus each `download_path` as binary property `file`.
5. POST that binary property to `http://whisper-api:8000/transcribe`.
6. Concatenate transcripts in the returned `index` order.
7. DELETE the slicer job. Jobs also expire automatically after the configured TTL.

The service is deliberately only `expose`d to the Compose network. Do not add a public `ports` mapping.

## Deploy

Copy this directory next to the existing `docker-compose.yml` as `audioSlicer`, merge `compose.audio-slicer.yml` into the existing file, and add a random value to `.env`:

```dotenv
AUDIO_SLICER_API_KEY=replace-with-a-long-random-value
```

Pass this variable to both services. The audio-slicer validates it and the importable n8n workflow sends it as `X-Audio-Slicer-Key`:

```yaml
services:
  n8n:
    environment:
      - AUDIO_SLICER_API_KEY=${AUDIO_SLICER_API_KEY:?Set AUDIO_SLICER_API_KEY in .env}
```

Then run:

```bash
docker compose build audio-slicer
docker compose up -d audio-slicer
docker compose ps audio-slicer
docker compose logs --tail=100 audio-slicer
```

Internal health test:

```bash
docker compose exec n8n node -e "fetch('http://audio-slicer:8000/health').then(r => r.json()).then(console.log)"
```
