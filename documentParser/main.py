import os
import uuid
import subprocess
from pathlib import Path
from fastapi import FastAPI, UploadFile, File, HTTPException

app = FastAPI()

WORK_DIR = Path(os.getenv("APP_WORK_DIR", "/work"))
WORK_DIR.mkdir(parents=True, exist_ok=True)

SUPPORTED = {".doc", ".docx", ".odt", ".md", ".rtf"}


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/parse")
async def parse(file: UploadFile = File(...)):
    ext = Path(file.filename).suffix.lower()

    if ext not in SUPPORTED:
        raise HTTPException(400, f"Unsupported: {ext}")

    job = WORK_DIR / str(uuid.uuid4())
    job.mkdir()

    input_file = job / f"input{ext}"
    output_txt = job / "output.txt"

    # guardar archivo
    with open(input_file, "wb") as f:
        f.write(await file.read())

    try:
        if ext == ".md":
            text = input_file.read_text()
            return {"text": text}

        # 🔥 clave: convertir todo a txt con pandoc + fallback
        subprocess.run(
            ["pandoc", str(input_file), "-t", "plain", "-o", str(output_txt)],
            check=True
        )

        text = output_txt.read_text()

        return {"text": text}

    except Exception as e:
        raise HTTPException(500, str(e))