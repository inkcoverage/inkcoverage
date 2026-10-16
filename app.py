"""
InkCoverage — Web-based ink coverage analyzer
FastAPI backend that bridges PDF uploads to Ghostscript tiffsep analysis.

License: AGPL-3.0 (required by Ghostscript AGPL dependency)
"""

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from PIL import Image, ImageCms, UnidentifiedImageError
from starlette.datastructures import UploadFile as FormUploadFile
import workload as limits
from lifetime import FileLifetime, Unavailable
from outcomes import OutcomeMiddleware, PrivacyHeaders, ServiceOutcomes
from pdf_colors import UNKNOWN as UNKNOWN_COLORS
from localization import render_index
from heatmap import build_heatmap
from channel_preview import build_channel_masks
from typing import Annotated

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MAX_UPLOAD_MB = limits.MAX_UPLOAD_MB
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
ANALYSIS_DPI = limits.positive_setting("ANALYSIS_DPI", 72)
PREVIEW_DPI = limits.positive_setting("PREVIEW_DPI", 150)
TEMP_DIR = Path(os.environ.get("TEMP_DIR", tempfile.gettempdir())) / "inkcoverage"
FILE_TTL_SECONDS = limits.positive_setting("FILE_TTL_SECONDS", 600)
CLEANUP_INTERVAL_SECONDS = limits.positive_setting("CLEANUP_INTERVAL_SECONDS", 60)
files = FileLifetime(lambda: TEMP_DIR, lambda: FILE_TTL_SECONDS)
outcomes = ServiceOutcomes()
RATE_LIMIT_MAX = limits.RATE_LIMIT_MAX  # analyses per hour per IP
GS_EXECUTABLE = os.environ.get("GS_EXECUTABLE", "gs")
INK_LIMIT_DPI = 150

# ---------------------------------------------------------------------------
# Cleanup background task
# ---------------------------------------------------------------------------

async def cleanup_old_files():
    """Reclaim on startup, then retry each interval without blocking the loop."""
    while True:
        await asyncio.to_thread(files.sweep)
        outcomes.report()
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


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


@app.exception_handler(Unavailable)
async def unavailable_handler(request, exc):
    return JSONResponse({"code": "document_unavailable", "detail": "PDF unavailable; upload it again"},
                        status_code=exc.status, headers={"Cache-Control": "no-store"})


def _upload_capacity(reservations):
    """Reserve space for admitted uploads; retained originals are also bounded."""
    total = 0
    count = 0
    try:
        root = TEMP_DIR.resolve()
        if root.exists():
            for directory in root.iterdir():
                if not re.fullmatch(r"[0-9a-f]{32}", directory.name): continue
                file = (directory / "upload.pdf").resolve()
                if file.is_relative_to(root) and file.is_file():
                    count += 1
                    total += file.stat().st_size
                    if count + reservations > limits.MAX_STORED_DOCUMENTS:
                        raise limits.WorkloadError("server_busy", 503, retry_after=60)
        if total + reservations * MAX_UPLOAD_BYTES > limits.MAX_STORED_UPLOAD_BYTES:
            raise limits.WorkloadError("server_busy", 503, retry_after=60)
        limits.check_disk_space(root, reservations * MAX_UPLOAD_BYTES)
    except OSError:
        raise limits.WorkloadError("server_busy", 503, retry_after=60) from None


app.add_middleware(limits.RequestBounds, upload_capacity=_upload_capacity)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(OutcomeMiddleware, counters=outcomes)
app.add_middleware(PrivacyHeaders)

@app.exception_handler(limits.WorkloadError)
async def workload_error(request: Request, exc: limits.WorkloadError):
    return exc.response()


BASE_DIR = Path(__file__).parent
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Do not echo supplied values: NaN/Infinity cannot be encoded as JSON,
    # and error responses need no document-derived content.
    messages = [f"{'.'.join(map(str, error['loc'][1:])) or 'request'}: {error['msg']}"
                for error in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "; ".join(messages)})


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class PreviewRequest(BaseModel):
    id: str = Field(pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)
    page: int = Field(ge=1, strict=True)


class AnalyzeRequest(PreviewRequest):
    cropLeft: float = Field(ge=0, allow_inf_nan=False, strict=True)
    cropTop: float = Field(ge=0, allow_inf_nan=False, strict=True)
    cropWidth: float = Field(gt=0, allow_inf_nan=False, strict=True)
    cropHeight: float = Field(gt=0, allow_inf_nan=False, strict=True)
    pdfWidthPt: float = Field(gt=0, allow_inf_nan=False, strict=True)
    pdfHeightPt: float = Field(gt=0, allow_inf_nan=False, strict=True)


class InkLimitRequest(AnalyzeRequest):
    limit: float = Field(ge=0, le=6400, allow_inf_nan=False, strict=True)
    channels: list[Annotated[str, Field(min_length=1, max_length=1024, strict=True)]] = Field(min_length=1, max_length=64)


class InvalidSelection(ValueError):
    """A selection with no measurable pixels at the configured resolution."""


PROCESSING_ERRORS = {
    "pdf_invalid": "This PDF is damaged or unsupported. Export a new PDF and try again.",
    "pdf_encrypted": "This PDF requires a password. Upload an unlocked copy.",
    "pdf_timeout": "PDF processing took too long. Try a simpler or smaller PDF.",
    "processor_unavailable": "PDF processing is temporarily unavailable. Please try again later.",
    "preview_failed": "Could not create a complete preview. Export a new PDF and try again.",
    "analysis_failed": "Could not read complete ink separations. No coverage result was calculated.",
}


class ProcessingError(RuntimeError):
    """Public error codes only; never return processor diagnostics or paths."""
    def __init__(self, code: str):
        self.code = code
        super().__init__(PROCESSING_ERRORS[code])


@app.exception_handler(ProcessingError)
async def processing_error(request: Request, exc: ProcessingError):
    return JSONResponse(status_code=503 if exc.code == "processor_unavailable" else 422,
                        content={"code": exc.code, "detail": str(exc)})


def _session_paths(document_id: str) -> tuple[Path, Path]:
    """Validate IDs again at the filesystem boundary and reject escaping links."""
    if not re.fullmatch(r"[0-9a-f]{32}", document_id):
        raise HTTPException(400, "Invalid document ID")
    root = TEMP_DIR.resolve()
    session_dir = (root / document_id).resolve()
    pdf_path = (session_dir / "upload.pdf").resolve()
    if not session_dir.is_relative_to(root) or not pdf_path.is_relative_to(session_dir):
        raise HTTPException(400, "Invalid document path")
    return session_dir, pdf_path


def _validated_crop(req: AnalyzeRequest, width: float, height: float) -> tuple[float, float]:
    # Client dimensions are retained for API compatibility, never used to scale
    # the measurement. A mismatch means the selection needs a fresh preview.
    if not (math.isclose(req.pdfWidthPt, width, rel_tol=0, abs_tol=1e-4)
            and math.isclose(req.pdfHeightPt, height, rel_tol=0, abs_tol=1e-4)):
        raise HTTPException(400, "Page dimensions changed; reload the preview and select again")
    # Permit only floating-point roundoff at the page edge, then normalize it.
    if (req.cropLeft >= width or req.cropTop >= height
            or req.cropWidth > width - req.cropLeft + 1e-6
            or req.cropHeight > height - req.cropTop + 1e-6):
        raise HTTPException(400, "Selection must stay inside the PDF page")
    return min(req.cropWidth, width - req.cropLeft), min(req.cropHeight, height - req.cropTop)


# ---------------------------------------------------------------------------
# Ghostscript helpers (run in thread pool to avoid blocking)
# ---------------------------------------------------------------------------

def _run_gs(cmd: list[str], timeout: int, failure_code: str):
    try:
        result = limits.execute_processor(cmd, limits.remaining_timeout(timeout))
    except subprocess.TimeoutExpired:
        raise ProcessingError("pdf_timeout") from None
    except OSError:
        raise ProcessingError("processor_unavailable") from None
    if result.returncode != 0:
        diagnostics = f"{result.stdout} {result.stderr}".lower()
        if any(message in diagnostics for message in
               ("requires a password", "incorrect password", "password did not work")):
            raise ProcessingError("pdf_encrypted")
        raise ProcessingError(failure_code)
    return result


def _find_gs() -> str:
    """Verify the configured processor without exposing installation paths."""
    result = _run_gs([GS_EXECUTABLE, "--version"], 10, "processor_unavailable")
    version = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", result.stdout.strip())
    if not version or tuple(map(int, version.groups())) < (10, 8, 0):
        raise ProcessingError("processor_unavailable")
    return GS_EXECUTABLE


def _metadata_command(gs: str, pdf_path: str, code: str) -> list[str]:
    # Pass the filename as data, not a PostScript literal. Only this PDF is
    # explicitly readable; SAFER remains enabled for all processing.
    path = Path(pdf_path).resolve().as_posix()
    return [gs, "-q", "-dSAFER", "-dNODISPLAY", "-dPDFSTOPONERROR",
            "-dBATCH", "-dNOPAUSE", "-dNOPROMPT", f"--permit-file-read={path}",
            f"-sPDFFile={path}", "-c", "PDFFile (r) file runpdfbegin " + code + " quit"]


def _get_page_count(gs: str, pdf_path: str) -> int:
    result = _run_gs(_metadata_command(gs, pdf_path, "pdfpagecount ="), 30, "pdf_invalid")
    out = result.stdout.strip()
    if not re.fullmatch(r"[0-9]+", out) or int(out) < 1:
        raise ProcessingError("pdf_invalid")
    return int(out)


def _get_page_dimensions(gs: str, pdf_path: str, page: int) -> tuple[float, float]:
    result = _run_gs(_metadata_command(gs, pdf_path,
                     f"{page} pdfgetpage /MediaBox pget pop aload pop "
                     "/y1 exch def /x1 exch def /y0 exch def /x0 exch def "
                     "/w x1 x0 sub def /h y1 y0 sub def "
                     f"{page} pdfgetpage /Rotate pget "
                     "{ 180 mod abs 90 eq { /w h /h w def def } if } if "
                     "0 = 0 = w = h ="), 30, "pdf_invalid")
    try:
        box = [float(value) for value in result.stdout.split()]
        if len(box) != 4 or not all(math.isfinite(v) for v in box):
            raise ValueError
        w, h = box[2] - box[0], box[3] - box[1]
        if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
            raise ValueError
        return w, h
    except ValueError:
        raise ProcessingError("pdf_invalid") from None


def _get_page_user_unit(gs: str, pdf_path: str, page: int) -> float:
    result = _run_gs(_metadata_command(gs, pdf_path,
                     f"{page} pdfgetpage /UserUnit pget {{ = }} {{ 1 = }} ifelse"), 30, "pdf_invalid")
    try:
        scale = float(result.stdout.strip())
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError
        return scale
    except ValueError:
        raise ProcessingError("pdf_invalid") from None


def _raster_command(gs: str, pdf_path: str, page: int, device: str,
                    dpi: int, output: str) -> list[str]:
    width, height = _get_page_dimensions(gs, pdf_path, page)
    scale = _get_page_user_unit(gs, pdf_path, page)
    limits.check_raster(width, height, dpi, "preview" if device == "png16m" else "analysis", scale)
    return [gs, "-q", "-dSAFER", "-dPDFSTOPONERROR", "-dBATCH", "-dNOMEDIAATTRS",
            "-dMaxBitmap=16777216", "-dBufferSpace=16777216",
            "-dNOPAUSE", "-dNOPROMPT", f"-dFirstPage={page}", f"-dLastPage={page}",
            f"-sDEVICE={device}", f"-r{dpi}", f"-sOutputFile={output}",
            Path(pdf_path).resolve().as_posix()]


def _image_size(path: Path, expected_format: str, expected_mode: str,
                failure_code: str) -> tuple[int, int]:
    limits.check_deadline()
    if path.is_file() and path.stat().st_size > limits.MAX_OUTPUT_BYTES:
        raise limits.WorkloadError("raster_limit")
    try:
        # Own the file handle even if the decoder fails while opening/loading.
        with path.open("rb") as source, Image.open(source, formats=[expected_format]) as image:
            if image.format != expected_format or image.mode != expected_mode or image.n_frames != 1:
                raise ProcessingError(failure_code)
            width, height = image.size
            pixel_limit = limits.MAX_PREVIEW_PIXELS if expected_format == "PNG" else limits.MAX_ANALYSIS_PIXELS
            if max(width, height) > limits.MAX_RASTER_SIDE or width * height > pixel_limit:
                raise limits.WorkloadError("raster_limit")
            image.load()  # Decode all pixels; a header alone is not a usable output.
            if min(image.size) < 1:
                raise ProcessingError(failure_code)
            return image.size
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        raise ProcessingError(failure_code) from None


def _render_preview(gs: str, pdf_path: str, page: int, out_png: str) -> None:
    Path(out_png).unlink(missing_ok=True)
    command = _raster_command(gs, pdf_path, page, "png16m", PREVIEW_DPI, out_png)
    token = limits.WORK_DIR.set(Path(out_png).parent)
    try: _run_gs(command, 60, "preview_failed")
    finally: limits.WORK_DIR.reset(token)
    _image_size(Path(out_png), "PNG", "RGB", "preview_failed")


@lru_cache(maxsize=4)
def _spot_display_transform(gs: str):
    """Use the renderer's installed default profiles for screen swatches."""
    executable = Path(shutil.which(gs) or gs).resolve()
    prefix = executable.parent.parent
    for directory in (prefix / "iccprofiles",
                      prefix / "share/ghostscript/10.08.0/iccprofiles"):
        try:
            return ImageCms.buildTransform(str(directory / "default_cmyk.icc"),
                                           str(directory / "default_rgb.icc"), "CMYK", "RGB")
        except (OSError, ImageCms.PyCMSError):
            continue
    return None


def _spot_display_colors(gs: str, diagnostics: str) -> dict:
    """Read full-tint equivalents supplied by the PDF, never an ink-name table.

    Ghostscript tiffsep reports these in its fractional 0..32760 CMYK range.
    Missing/invalid diagnostics leave the existing unknown-colour marker.
    """
    colors = {}
    transform = _spot_display_transform(gs)
    for line in diagnostics.splitlines():
        if len(line) > 4096 or len(colors) >= 64:
            continue
        match = re.fullmatch(r'%%SeparationColor: "(.+)" 100% ink = (\d+) (\d+) (\d+) (\d+) CMYK', line.strip())
        if not match:
            continue
        values = [int(value) for value in match.groups()[1:]]
        if any(value > 32760 for value in values):
            continue
        pixel = tuple(round(value * 255 / 32760) for value in values)
        image = Image.new("CMYK", (1, 1), pixel)
        rgb = (ImageCms.applyTransform(image, transform) if transform else image.convert("RGB")).getpixel((0, 0))
        colors[match.group(1)] = "#" + "".join(f"{component:02x}" for component in rgb)
    return colors


def _run_tiffsep(gs: str, pdf_path: str, page: int, session_dir: str, dpi: int = ANALYSIS_DPI) -> dict:
    output = (Path(session_dir) / "sep.tif").as_posix()
    # Separation names are derived from PDF ink names. Limit the additional
    # device outputs to this newly created job directory, not all of TEMP_DIR.
    cmd = _raster_command(gs, pdf_path, page, "tiffsep", dpi, output)
    cmd.insert(2, f"--permit-file-write={Path(session_dir).as_posix()}/")
    cmd.insert(2, "-dPrintSpotCMYK=true")
    token = limits.WORK_DIR.set(Path(session_dir))
    try:
        result = _run_gs(cmd, 120, "analysis_failed")
        return _spot_display_colors(gs, f"{result.stdout}\n{result.stderr}")
    finally: limits.WORK_DIR.reset(token)


def _separation_outputs(directory):
    directory = Path(directory)
    size = _image_size(directory / 'sep.tif', 'TIFF', 'CMYK', 'analysis_failed')
    named = [(f, re.fullmatch(r'sep\((.+)\)\.tif', f.name)) for f in sorted(directory.glob('sep*.tif'))]
    named = [(f, match.group(1)) for f, match in named if match]
    if not {'Cyan', 'Magenta', 'Yellow', 'Black'}.issubset({name for _, name in named}):
        raise ProcessingError('analysis_failed')
    for path, _ in named:
        if not path.resolve().is_relative_to(directory.resolve()) or _image_size(path, 'TIFF', 'L', 'analysis_failed') != size:
            raise ProcessingError('analysis_failed')
    return size, named


def _analyze_tiff_coverage(
    tiff_path: str,
    crop_left: float, crop_top: float,
    crop_width: float, crop_height: float,
    pdf_width_pt: float, pdf_height_pt: float,
) -> float:
    """Measure only fully decoded, single-channel 8-bit separations."""
    _image_size(Path(tiff_path), "TIFF", "L", "analysis_failed")
    try:
        with Path(tiff_path).open("rb") as source, Image.open(source, formats=["TIFF"]) as img:
            img_width, img_height = img.size
            scale_x = img_width / pdf_width_pt
            scale_y = img_height / pdf_height_pt
            left = max(0, int(crop_left * scale_x))
            top = max(0, int(crop_top * scale_y))
            right = min(img_width, int((crop_left + crop_width) * scale_x))
            bottom = min(img_height, int((crop_top + crop_height) * scale_y))
            if right <= left or bottom <= top:
                raise InvalidSelection("Selection is too small at the analysis resolution; select a larger area")
            pixels = img.load()
            ink = 0
            for y in range(top, bottom):
                if (y - top) % 32 == 0: limits.check_deadline()
                ink += sum(255 - pixels[x, y] for x in range(left, right))
            return round(ink / (255 * (right - left) * (bottom - top)) * 100, 2)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        if isinstance(exc, InvalidSelection):
            raise
        raise ProcessingError("analysis_failed") from None


def _analyze_ink_coverage(
    gs: str, pdf_path: str, page: int,
    crop_left: float, crop_top: float,
    crop_width: float, crop_height: float,
    pdf_width_pt: float, pdf_height_pt: float,
) -> dict:
    session_dir = os.path.join(TEMP_DIR, f"analysis_{uuid.uuid4().hex}")
    files.create(session_dir, scratch=True)

    try:
        spot_colors = _run_tiffsep(gs, pdf_path, page, session_dir) or {}

        # tiffsep with non-%d output creates named files:
        # sep(Cyan).tif, sep(Magenta).tif, sep(Yellow).tif, sep(Black).tif, sep(SpotName).tif
        # A successful process can still leave missing/truncated outputs.
        # Require the composite and all four process separations, then ensure
        # every separation has the same dimensions as the composite.
        size, named = _separation_outputs(session_dir)

        channels = []
        cmyk_names = {"Cyan", "Magenta", "Yellow", "Black"}
        cmyk_total = 0.0
        spot_total = 0.0

        for f, channel_name in named:
            coverage = _analyze_tiff_coverage(
                str(f),
                crop_left, crop_top, crop_width, crop_height,
                pdf_width_pt, pdf_height_pt,
            )

            channel = {"name": channel_name, "coverage": coverage}
            if channel_name not in cmyk_names and channel_name in spot_colors:
                channel["displayColor"] = spot_colors[channel_name]
            channels.append(channel)
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
        files.release(session_dir)


# ---------------------------------------------------------------------------
# Multilingual index rendering
# ---------------------------------------------------------------------------

def _render_index_for_lang(lang: str) -> str:
    source = (BASE_DIR / "static" / "index.html").read_text(encoding="utf-8")
    return render_index(source, lang, MAX_UPLOAD_MB, limits.MAX_DOCUMENT_PAGES)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
@app.head("/")
async def index():
    return HTMLResponse(_render_index_for_lang("en"))


@app.get("/es", response_class=HTMLResponse)
@app.head("/es")
async def index_es():
    return HTMLResponse(_render_index_for_lang("es"))


@app.get("/pt", response_class=HTMLResponse)
@app.head("/pt")
async def index_pt():
    return HTMLResponse(_render_index_for_lang("pt"))


@app.get("/zh", response_class=HTMLResponse)
@app.head("/zh")
async def index_zh():
    return HTMLResponse(_render_index_for_lang("zh"))


@app.get("/privacy", response_class=HTMLResponse)
@app.head("/privacy")
async def privacy():
    html_path = BASE_DIR / "static" / "privacy.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/es/privacy", response_class=HTMLResponse)
@app.head("/es/privacy")
async def privacy_es():
    return HTMLResponse((BASE_DIR / "static" / "privacy-es.html").read_text(encoding="utf-8"))


@app.get("/pt/privacy", response_class=HTMLResponse)
@app.head("/pt/privacy")
async def privacy_pt():
    return HTMLResponse((BASE_DIR / "static" / "privacy-pt.html").read_text(encoding="utf-8"))


@app.get("/zh/privacy", response_class=HTMLResponse)
@app.head("/zh/privacy")
async def privacy_zh():
    return HTMLResponse((BASE_DIR / "static" / "privacy-zh.html").read_text(encoding="utf-8"))


@app.get("/about", response_class=HTMLResponse)
@app.head("/about")
async def about():
    html_path = BASE_DIR / "static" / "about.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/contact", response_class=HTMLResponse)
@app.head("/contact")
async def contact():
    html_path = BASE_DIR / "static" / "contact.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/faq", response_class=HTMLResponse)
@app.head("/faq")
async def faq():
    html_path = BASE_DIR / "static" / "faq.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/es/faq", response_class=HTMLResponse)
@app.head("/es/faq")
async def faq_es():
    return HTMLResponse((BASE_DIR / "static" / "faq-es.html").read_text(encoding="utf-8"))


@app.get("/pt/faq", response_class=HTMLResponse)
@app.head("/pt/faq")
async def faq_pt():
    return HTMLResponse((BASE_DIR / "static" / "faq-pt.html").read_text(encoding="utf-8"))


@app.get("/zh/faq", response_class=HTMLResponse)
@app.head("/zh/faq")
async def faq_zh():
    return HTMLResponse((BASE_DIR / "static" / "faq-zh.html").read_text(encoding="utf-8"))


@app.get("/es/about", response_class=HTMLResponse)
@app.head("/es/about")
async def about_es():
    return HTMLResponse((BASE_DIR / "static" / "about-es.html").read_text(encoding="utf-8"))


@app.get("/pt/about", response_class=HTMLResponse)
@app.head("/pt/about")
async def about_pt():
    return HTMLResponse((BASE_DIR / "static" / "about-pt.html").read_text(encoding="utf-8"))


@app.get("/zh/about", response_class=HTMLResponse)
@app.head("/zh/about")
async def about_zh():
    return HTMLResponse((BASE_DIR / "static" / "about-zh.html").read_text(encoding="utf-8"))


@app.get("/es/contact", response_class=HTMLResponse)
@app.head("/es/contact")
async def contact_es():
    return HTMLResponse((BASE_DIR / "static" / "contact-es.html").read_text(encoding="utf-8"))


@app.get("/pt/contact", response_class=HTMLResponse)
@app.head("/pt/contact")
async def contact_pt():
    return HTMLResponse((BASE_DIR / "static" / "contact-pt.html").read_text(encoding="utf-8"))


@app.get("/zh/contact", response_class=HTMLResponse)
@app.head("/zh/contact")
async def contact_zh():
    return HTMLResponse((BASE_DIR / "static" / "contact-zh.html").read_text(encoding="utf-8"))


def _additional_language_route(lang, page):
    async def localized_page():
        if not page:
            return HTMLResponse(_render_index_for_lang(lang))
        return HTMLResponse((BASE_DIR / "static" / f"{page}-{lang}.html").read_text(encoding="utf-8"))
    return localized_page


for _lang in ("de", "fr", "uk"):
    for _page in ("", "about", "faq", "contact", "privacy"):
        _path = f"/{_lang}" + (f"/{_page}" if _page else "")
        app.add_api_route(_path, _additional_language_route(_lang, _page),
                          methods=["GET", "HEAD"], response_class=HTMLResponse,
                          name=f"{_page or 'index'}_{_lang}")


@app.get("/BingSiteAuth.xml")
async def bing_auth():
    return Response(
        (BASE_DIR / "static" / "BingSiteAuth.xml").read_text(encoding="utf-8"),
        media_type="application/xml",
    )


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
@app.head("/api/ping")
async def ping():
    return {"status": "ok"}


@app.get("/api/limits")
async def public_limits():
    return {
        "maxUploadMb": MAX_UPLOAD_MB, "maxUploadBytes": MAX_UPLOAD_BYTES,
        "maxDocumentPages": limits.MAX_DOCUMENT_PAGES,
        "previewPixels": limits.MAX_PREVIEW_PIXELS, "analysisPixels": limits.MAX_ANALYSIS_PIXELS,
        "rasterSide": limits.MAX_RASTER_SIDE, "analysisDpi": ANALYSIS_DPI, "previewDpi": PREVIEW_DPI,
        "jobSeconds": limits.MAX_JOB_SECONDS, "pagesPerAnalysis": 1,
        "allowances": {"upload": limits.UPLOAD_RATE_MAX, "preview": limits.PREVIEW_RATE_MAX, "analysis": RATE_LIMIT_MAX},
        "windowSeconds": limits.RATE_WINDOW,
        "fileLifetimeSeconds": FILE_TTL_SECONDS, "cleanupIntervalSeconds": CLEANUP_INTERVAL_SECONDS,
    }


@app.get("/api/allowance")
async def allowance(request: Request):
    return JSONResponse({"windowSeconds": limits.RATE_WINDOW,
                         "allowances": limits.rates.snapshot(limits.client_identity(request.scope))},
                        headers={"Cache-Control": "no-store"})


def _inspect_upload(pdf_path: str):
    count = _get_page_count(_find_gs(), pdf_path)
    limits.check_page_count(count)
    return count


async def _write_upload_chunk(output, chunk):
    """Finish an admitted disk write before closing/deleting its upload file."""
    pending = asyncio.get_running_loop().run_in_executor(None, output.write, chunk)
    try:
        await asyncio.shield(pending)
    except asyncio.CancelledError:
        # Even repeated HTTP cancellation cannot close the handle under the
        # executor's write. Copy chunks are bounded to one MiB.
        while not pending.done():
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                continue
        if not pending.cancelled(): pending.exception()
        raise


async def _store_upload(file: UploadFile):
    if file.content_type not in ("application/pdf", "application/x-pdf"):
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(400, "Only PDF files are accepted")
    pdf_id = uuid.uuid4().hex
    session_dir, pdf_path = _session_paths(pdf_id)
    files.create(session_dir)
    handed_to_worker = False
    try:
        total = 0
        header = b""
        with pdf_path.open("wb") as output:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise limits.WorkloadError("upload_too_large", maxMb=MAX_UPLOAD_MB)
                if len(header) < 1024: header += chunk[:1024-len(header)]
                await _write_upload_chunk(output, chunk)
        if b"%PDF-" not in header:
            raise ProcessingError("pdf_invalid")
        def inspect():
            count = _inspect_upload(str(pdf_path))
            metadata = files.ready(session_dir)
            return count, metadata
        handed_to_worker = True
        page_count, metadata = await limits.jobs.run(inspect, on_finished=lambda: files.release(session_dir))
    except BaseException:
        files.discard(session_dir)
        raise
    finally:
        if not handed_to_worker: files.release(session_dir)
    return {"id": pdf_id, "fileName": file.filename, "pageCount": page_count,
            "uploadedAt": metadata["uploadedAt"], "expiresAt": metadata["expiresAt"],
            "remainingSeconds": max(0, metadata["expiresAt"] - time.time())}


@app.post("/api/upload")
async def upload(request: Request):
    if not request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
        raise HTTPException(400, "Send one PDF as a multipart upload")
    async with request.form(max_files=1, max_fields=0, max_part_size=1024) as form:
        file = form.get("file")
        if not isinstance(file, FormUploadFile):
            raise HTTPException(400, "One PDF file is required")
        return await _store_upload(file)


def _inspect_page(gs, pdf_path, page):
    try:
        count = _get_page_count(gs, str(pdf_path))
        limits.check_page_count(count)
        if page > count:
            raise HTTPException(400, "Page number is outside this PDF")
        return _get_page_dimensions(gs, str(pdf_path), page)
    except ProcessingError:
        raise
    except (RuntimeError, subprocess.TimeoutExpired):
        raise HTTPException(422, "Could not read PDF page information") from None


def _page_colors(pdf_path, page):
    # A parser failure/timeout must never be reported as absence of RGB.
    # Run separately so cancellation/time limits can kill PDF inspection.
    try:
        result = limits.execute_processor([sys.executable, str(BASE_DIR / "pdf_colors.py"),
                                            str(pdf_path), str(page)], limits.remaining_timeout(15))
        if result.returncode == 0:
            data = json.loads(result.stdout)
            if (data.get("rgbStatus") in ("detected", "not_detected", "unknown")
                    and data.get("profilesStatus") in ("complete", "unknown")
                    and all(isinstance(data.get(key), list) and len(data[key]) <= 16
                            and all(isinstance(name, str) and len(name) <= 128 for name in data[key])
                            for key in ("outputProfiles", "sourceProfiles", "outputConditions"))):
                colors = data.get("spotColors", {})
                if (not isinstance(colors, dict) or len(colors) > 64
                        or any(not isinstance(name, str) or len(name) > 1024
                               or not isinstance(color, str) or not re.fullmatch(r"#[0-9a-f]{6}", color)
                               for name, color in colors.items())):
                    data.pop("spotColors", None)
                channels = data.get('inkChannels')
                if (data.get('inkChannelsStatus') not in ('complete', 'unknown')
                        or not isinstance(channels, list) or not 4 <= len(channels) <= 64
                        or any(not isinstance(ch, dict) or not isinstance(ch.get('name'), str)
                               or not 1 <= len(ch['name']) <= 1024 for ch in channels)
                        or len({ch['name'] for ch in channels}) != len(channels)):
                    data.pop('inkChannelsStatus', None)
                    data.pop('inkChannels', None)
                return data
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        pass
    return {key: value.copy() if isinstance(value, list) else value for key, value in UNKNOWN_COLORS.items()}


def _preview_job(req, session_dir, pdf_path):
    limits.check_disk_space(TEMP_DIR)
    gs = _find_gs()
    width_pt, height_pt = _inspect_page(gs, pdf_path, req.page)
    limits.check_raster(width_pt, height_pt, PREVIEW_DPI, "preview")
    job_dir = Path(tempfile.mkdtemp(prefix="preview_", dir=session_dir))
    try:
        preview_path = (job_dir / f"preview_{req.page}.png").resolve()
        if not preview_path.is_relative_to(session_dir):
            raise HTTPException(400, "Invalid document path")
        _render_preview(gs, str(pdf_path), req.page, str(preview_path))
        import base64
        b64 = base64.b64encode(preview_path.read_bytes()).decode("ascii")
        limits.check_deadline()
    except OSError:
        raise ProcessingError("preview_failed") from None
    finally:
        files.remove_derivative(job_dir, session_dir)
    color_info = _page_colors(pdf_path, req.page)
    limits.check_deadline()
    return {"image": f"data:image/png;base64,{b64}", "widthPt": width_pt, "heightPt": height_pt,
            "colorInfo": color_info}


@app.post("/api/preview")
async def preview(req: PreviewRequest, request: Request):
    session_dir, pdf_path = _session_paths(req.id)
    files.acquire(session_dir)
    try:
        limits.rates.take(limits.client_identity(request.scope), "preview", limits.PREVIEW_RATE_MAX)
    except BaseException:
        files.release(session_dir)
        raise
    return await limits.jobs.run(_preview_job, req, session_dir, pdf_path,
                                 on_finished=lambda: files.release(session_dir))


def _analysis_job(req, pdf_path):
    limits.check_disk_space(TEMP_DIR)
    gs = _find_gs()
    width_pt, height_pt = _inspect_page(gs, pdf_path, req.page)
    limits.check_raster(width_pt, height_pt, ANALYSIS_DPI, "analysis")
    crop_width, crop_height = _validated_crop(req, width_pt, height_pt)
    try:
        result = _analyze_ink_coverage(gs, str(pdf_path), req.page,
                 req.cropLeft, req.cropTop, crop_width, crop_height, width_pt, height_pt)
        result["colorInfo"] = _page_colors(pdf_path, req.page)
        # RGB alternates can be outside the CMYK gamut. Keep their original
        # screen colour when the bounded inspector understands the tint.
        for channel in result["channels"]:
            color = result["colorInfo"].get("spotColors", {}).get(channel["name"])
            if color and channel["name"] not in ("Cyan", "Magenta", "Yellow", "Black"):
                channel["displayColor"] = color
        limits.check_deadline()
        return result
    except InvalidSelection as exc:
        raise HTTPException(400, str(exc)) from None


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest, request: Request):
    session_dir, pdf_path = _session_paths(req.id)
    files.acquire(session_dir)
    try:
        limits.rates.take(limits.client_identity(request.scope), "analysis", RATE_LIMIT_MAX)
    except BaseException:
        files.release(session_dir)
        raise
    return await limits.jobs.run(_analysis_job, req, pdf_path,
                                 on_finished=lambda: files.release(session_dir))


def _ink_channels_job(req, pdf_path):
    info = _page_colors(pdf_path, req.page)
    if info.get('inkChannelsStatus') == 'complete':
        return {'channels': info['inkChannels']}
    # Unsupported PDF inspection falls back to real renderer separations.
    limits.check_disk_space(TEMP_DIR)
    gs = _find_gs()
    width, height = _inspect_page(gs, pdf_path, req.page)
    limits.check_raster(width, height, ANALYSIS_DPI, 'analysis')
    directory = TEMP_DIR / f'analysis_{uuid.uuid4().hex}'
    files.create(directory, scratch=True)
    try:
        colors = _run_tiffsep(gs, str(pdf_path), req.page, str(directory))
        _, named = _separation_outputs(directory)
        if len(named) > 64:
            raise limits.WorkloadError('job_limit', 422)
        return {'channels': [{'name': name, **({'displayColor': colors[name]} if name in colors else {})}
                             for _, name in named]}
    finally:
        files.release(directory)


def _ink_limit_job(req, pdf_path):
    if len(set(req.channels)) != len(req.channels):
        raise HTTPException(400, 'Select each ink only once')
    limits.check_disk_space(TEMP_DIR)
    gs = _find_gs()
    width, height = _inspect_page(gs, pdf_path, req.page)
    limits.check_raster(width, height, INK_LIMIT_DPI, 'analysis')
    crop_width, crop_height = _validated_crop(req, width, height)
    directory = TEMP_DIR / f'analysis_{uuid.uuid4().hex}'
    files.create(directory, scratch=True)
    try:
        _run_tiffsep(gs, str(pdf_path), req.page, str(directory), INK_LIMIT_DPI)
        size, named = _separation_outputs(directory)
        if len(named) > 64:
            raise limits.WorkloadError('job_limit', 422)
        available = dict((name, path) for path, name in named)
        if any(name not in available for name in req.channels):
            raise HTTPException(400, 'The selected inks do not match this page. Open the ink limit check again.')
        left = max(0, int(req.cropLeft * size[0] / width))
        top = max(0, int(req.cropTop * size[1] / height))
        right = min(size[0], int((req.cropLeft + crop_width) * size[0] / width))
        bottom = min(size[1], int((req.cropTop + crop_height) * size[1] / height))
        if right <= left or bottom <= top:
            raise HTTPException(400, 'Selection is too small for the ink limit check. Select a larger area.')
        result = build_heatmap([available[name] for name in req.channels], size, (left, top, right, bottom), req.limit)
        result.update(limit=req.limit, includedChannels=req.channels,
                      excludedChannels=[name for name in available if name not in req.channels],
                      page=req.page, widthPt=width, heightPt=height, dpi=INK_LIMIT_DPI,
                      checkedArea={'leftPt': req.cropLeft, 'topPt': req.cropTop,
                                   'widthPt': crop_width, 'heightPt': crop_height})
        return result
    finally:
        files.release(directory)


async def _ink_request(req, request, job, operation):
    session_dir, pdf_path = _session_paths(req.id)
    files.acquire(session_dir)
    try:
        limits.rates.take(limits.client_identity(request.scope), operation,
                          RATE_LIMIT_MAX if operation == 'analysis' else limits.PREVIEW_RATE_MAX)
    except BaseException:
        files.release(session_dir)
        raise
    return await limits.jobs.run(job, req, pdf_path, on_finished=lambda: files.release(session_dir))


@app.post('/api/ink-limit/channels')
async def ink_channels(req: PreviewRequest, request: Request):
    return await _ink_request(req, request, _ink_channels_job, 'preview')


@app.post('/api/ink-limit')
async def ink_limit(req: InkLimitRequest, request: Request):
    return await _ink_request(req, request, _ink_limit_job, 'analysis')


def _channel_preview_job(req, pdf_path):
    limits.check_disk_space(TEMP_DIR)
    gs = _find_gs()
    width, height = _inspect_page(gs, pdf_path, req.page)
    limits.check_raster(width, height, INK_LIMIT_DPI, 'analysis')
    directory = TEMP_DIR / f'analysis_{uuid.uuid4().hex}'
    files.create(directory, scratch=True)
    try:
        colors = _run_tiffsep(gs, str(pdf_path), req.page, str(directory), INK_LIMIT_DPI)
        size, named = _separation_outputs(directory)
        transform = _spot_display_transform(gs)
        for index, name in enumerate(('Cyan', 'Magenta', 'Yellow', 'Black')):
            ink = [0] * 4; ink[index] = 255
            sample = Image.new('CMYK', (1, 1), tuple(ink))
            rgb = (ImageCms.applyTransform(sample, transform) if transform else sample.convert('RGB')).getpixel((0, 0))
            colors[name] = '#' + ''.join(f'{value:02x}' for value in rgb)
        colors.update(_page_colors(pdf_path, req.page).get('spotColors', {}))
        channels = build_channel_masks(named, colors, size)
        return {'page': req.page, 'widthPt': width, 'heightPt': height, 'dpi': INK_LIMIT_DPI,
                'width': size[0], 'height': size[1], 'channels': channels}
    finally:
        files.release(directory)


@app.post('/api/channel-preview')
async def channel_preview(req: PreviewRequest, request: Request):
    return await _ink_request(req, request, _channel_preview_job, 'preview')
