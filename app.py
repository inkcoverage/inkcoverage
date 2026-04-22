"""
InkCoverage — Web-based ink coverage analyzer
FastAPI backend that bridges PDF uploads to Ghostscript tiffsep analysis.

License: AGPL-3.0 (required by Ghostscript AGPL dependency)
"""

import asyncio
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
ANALYSIS_DPI = int(os.environ.get("ANALYSIS_DPI", "72"))
PREVIEW_DPI = int(os.environ.get("PREVIEW_DPI", "150"))
TEMP_DIR = Path(os.environ.get("TEMP_DIR", tempfile.gettempdir())) / "inkcoverage"
FILE_TTL_SECONDS = int(os.environ.get("FILE_TTL_SECONDS", "600"))  # 10 minutes
RATE_LIMIT_WINDOW = 3600  # 1 hour
RATE_LIMIT_MAX = int(os.environ.get("RATE_LIMIT_MAX", "30"))  # analyses per hour per IP
GS_EXECUTABLE = os.environ.get("GS_EXECUTABLE", "gs")

# ---------------------------------------------------------------------------
# Rate limiter (in-memory, simple)
# ---------------------------------------------------------------------------

rate_limit_store: dict[str, list[float]] = defaultdict(list)


def check_rate_limit(ip: str) -> bool:
    """Return True if the request is within rate limits."""
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW
    # Prune old entries
    rate_limit_store[ip] = [t for t in rate_limit_store[ip] if t > window_start]
    if len(rate_limit_store[ip]) >= RATE_LIMIT_MAX:
        return False
    rate_limit_store[ip].append(now)
    return True


# ---------------------------------------------------------------------------
# Cleanup background task
# ---------------------------------------------------------------------------

async def cleanup_old_files():
    """Periodically remove expired upload sessions."""
    while True:
        await asyncio.sleep(60)
        try:
            if not TEMP_DIR.exists():
                continue
            now = time.time()
            for item in TEMP_DIR.iterdir():
                if item.is_dir() and (now - item.stat().st_mtime) > FILE_TTL_SECONDS:
                    shutil.rmtree(item, ignore_errors=True)
                elif item.is_file() and (now - item.stat().st_mtime) > FILE_TTL_SECONDS:
                    item.unlink(missing_ok=True)
        except Exception:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    task = asyncio.create_task(cleanup_old_files())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="InkCoverage", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class PreviewRequest(BaseModel):
    id: str
    page: int = Field(ge=1)


class AnalyzeRequest(BaseModel):
    id: str
    page: int = Field(ge=1)
    cropLeft: float
    cropTop: float
    cropWidth: float
    cropHeight: float
    pdfWidthPt: float
    pdfHeightPt: float


# ---------------------------------------------------------------------------
# Ghostscript helpers (run in thread pool to avoid blocking)
# ---------------------------------------------------------------------------

def _find_gs() -> str:
    """Verify Ghostscript is available."""
    gs = GS_EXECUTABLE
    try:
        subprocess.run([gs, "--version"], capture_output=True, check=True, timeout=10)
        return gs
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise RuntimeError(
            "Ghostscript not found. Install it: apt-get install ghostscript"
        )


def _get_page_count(gs: str, pdf_path: str) -> int:
    pdf_posix = pdf_path.replace("\\", "/")
    cmd = [
        gs, "-q", "-dNODISPLAY", "-dNOSAFER", "-dBATCH", "-dNOPAUSE",
        "-c", f"({pdf_posix}) (r) file runpdfbegin pdfpagecount = quit",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    out = result.stdout.strip()
    m = re.search(r"(\d+)", out)
    return int(m.group(1)) if m else 1


def _get_page_dimensions(gs: str, pdf_path: str, page: int) -> tuple[float, float]:
    pdf_posix = pdf_path.replace("\\", "/")
    code = (
        f"({pdf_posix}) (r) file runpdfbegin "
        f"{page} pdfgetpage /MediaBox pget pop {{ = }} forall quit"
    )
    cmd = [gs, "-q", "-dNODISPLAY", "-dNOSAFER", "-dBATCH", "-dNOPAUSE", "-c", code]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    values = result.stdout.strip().split("\n")
    values = [v.strip() for v in values if v.strip()]
    if len(values) >= 4:
        w = float(values[2]) - float(values[0])
        h = float(values[3]) - float(values[1])
        return (w, h)
    return (612.0, 792.0)


def _render_preview(gs: str, pdf_path: str, page: int, out_png: str) -> None:
    cmd = [
        gs, "-q", "-dBATCH", "-dNOMEDIAATTRS", "-dNOPAUSE", "-dNOPROMPT",
        f"-dFirstPage={page}", f"-dLastPage={page}",
        "-sDEVICE=png16m", f"-r{PREVIEW_DPI}",
        f"-sOutputFile={out_png}", pdf_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=60)


def _run_tiffsep(gs: str, pdf_path: str, page: int, session_dir: str) -> None:
    out_prefix = os.path.join(session_dir, "sep")
    cmd = [
        gs, "-q", "-dBATCH", "-dNOMEDIAATTRS", "-dNOPAUSE", "-dNOPROMPT",
        f"-dFirstPage={page}", f"-dLastPage={page}",
        "-sDEVICE=tiffsep", f"-r{ANALYSIS_DPI}",
        f"-sOutputFile={out_prefix}.tif", pdf_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=120)


def _analyze_tiff_coverage(
    tiff_path: str,
    crop_left: float, crop_top: float,
    crop_width: float, crop_height: float,
    pdf_width_pt: float, pdf_height_pt: float,
) -> float:
    """Analyze a single-channel grayscale TIFF and return coverage percentage.

    Uses raw TIFF parsing to avoid PIL/Pillow dependency for simple
    uncompressed grayscale TIFFs produced by Ghostscript tiffsep.
    Falls back to PIL if available.
    """
    try:
        from PIL import Image
        img = Image.open(tiff_path)
        img_width, img_height = img.size

        scale_x = img_width / pdf_width_pt
        scale_y = img_height / pdf_height_pt

        pix_left = max(0, int(crop_left * scale_x))
        pix_top = max(0, int(crop_top * scale_y))
        pix_right = min(img_width, int((crop_left + crop_width) * scale_x))
        pix_bottom = min(img_height, int((crop_top + crop_height) * scale_y))

        pixels = img.load()
        total = 0
        ink = 0.0

        for y in range(pix_top, pix_bottom):
            for x in range(pix_left, pix_right):
                p = pixels[x, y]
                # Handle both single-value and tuple pixel formats
                val = p if isinstance(p, int) else p[0]
                ink += (255 - val) / 255.0
                total += 1

        img.close()
        return round((ink / total) * 100, 2) if total > 0 else 0.0

    except ImportError:
        # Fallback: no PIL, try numpy
        raise RuntimeError("Pillow is required: pip install Pillow")


def _analyze_ink_coverage(
    gs: str, pdf_path: str, page: int,
    crop_left: float, crop_top: float,
    crop_width: float, crop_height: float,
    pdf_width_pt: float, pdf_height_pt: float,
) -> dict:
    session_dir = os.path.join(TEMP_DIR, f"analysis_{uuid.uuid4().hex}")
    os.makedirs(session_dir, exist_ok=True)

    try:
        _run_tiffsep(gs, pdf_path, page, session_dir)

        # tiffsep with non-%d output creates named files:
        # sep(Cyan).tif, sep(Magenta).tif, sep(Yellow).tif, sep(Black).tif, sep(SpotName).tif
        sep_files = sorted(Path(session_dir).glob("sep*.tif"))

        channels = []
        cmyk_names = {"Cyan", "Magenta", "Yellow", "Black"}
        cmyk_total = 0.0
        spot_total = 0.0

        for f in sep_files:
            m = re.search(r"\((.+?)\)", f.name)
            if not m:
                continue  # skip composite
            channel_name = m.group(1)

            coverage = _analyze_tiff_coverage(
                str(f),
                crop_left, crop_top, crop_width, crop_height,
                pdf_width_pt, pdf_height_pt,
            )

            channels.append({"name": channel_name, "coverage": coverage})
            if channel_name in cmyk_names:
                cmyk_total += coverage
            else:
                spot_total += coverage

        return {
            "channels": channels,
            "cmykTotal": round(cmyk_total, 2),
            "spotTotal": round(spot_total, 2),
            "grandTotal": round(cmyk_total + spot_total, 2),
        }

    finally:
        shutil.rmtree(session_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = BASE_DIR / "static" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/privacy", response_class=HTMLResponse)
async def privacy():
    html_path = BASE_DIR / "static" / "privacy.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/about", response_class=HTMLResponse)
async def about():
    html_path = BASE_DIR / "static" / "about.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/contact", response_class=HTMLResponse)
async def contact():
    html_path = BASE_DIR / "static" / "contact.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return (BASE_DIR / "static" / "robots.txt").read_text()


@app.get("/sitemap.xml")
async def sitemap_xml():
    return Response(
        (BASE_DIR / "static" / "sitemap.xml").read_text(encoding="utf-8"),
        media_type="application/xml",
    )


@app.get("/api/ping")
async def ping():
    return {"status": "ok"}


@app.post("/api/upload")
async def upload(request: Request, file: UploadFile = File(...)):
    # Validate content type
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        # Also accept by extension since some browsers send wrong MIME
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "Only PDF files are accepted")

    # Read with size limit
    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"File too large (max {MAX_UPLOAD_MB} MB)")

    # Save to temp
    pdf_id = uuid.uuid4().hex
    session_dir = TEMP_DIR / pdf_id
    session_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = session_dir / "upload.pdf"
    pdf_path.write_bytes(contents)

    # Get page count
    gs = _find_gs()
    loop = asyncio.get_event_loop()
    page_count = await loop.run_in_executor(None, _get_page_count, gs, str(pdf_path))

    return {
        "id": pdf_id,
        "fileName": file.filename,
        "pageCount": page_count,
    }


@app.post("/api/preview")
async def preview(req: PreviewRequest):
    session_dir = TEMP_DIR / req.id
    pdf_path = session_dir / "upload.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "PDF not found or expired")

    gs = _find_gs()
    loop = asyncio.get_event_loop()

    # Get dimensions and render preview in parallel
    dims_future = loop.run_in_executor(
        None, _get_page_dimensions, gs, str(pdf_path), req.page
    )

    preview_png = str(session_dir / f"preview_{req.page}.png")
    render_future = loop.run_in_executor(
        None, _render_preview, gs, str(pdf_path), req.page, preview_png
    )

    width_pt, height_pt = await dims_future
    await render_future

    if not os.path.exists(preview_png):
        raise HTTPException(500, "Failed to render preview")

    import base64
    png_bytes = Path(preview_png).read_bytes()
    b64 = base64.b64encode(png_bytes).decode("ascii")

    # Clean up preview file
    Path(preview_png).unlink(missing_ok=True)

    return {
        "image": f"data:image/png;base64,{b64}",
        "widthPt": width_pt,
        "heightPt": height_pt,
    }


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, request: Request):
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not check_rate_limit(client_ip):
        raise HTTPException(429, f"Rate limit exceeded ({RATE_LIMIT_MAX} analyses/hour)")

    session_dir = TEMP_DIR / req.id
    pdf_path = session_dir / "upload.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, "PDF not found or expired")

    gs = _find_gs()
    loop = asyncio.get_event_loop()

    result = await loop.run_in_executor(
        None,
        _analyze_ink_coverage,
        gs, str(pdf_path), req.page,
        req.cropLeft, req.cropTop, req.cropWidth, req.cropHeight,
        req.pdfWidthPt, req.pdfHeightPt,
    )

    return result
