"""
Generate static/og-image.png for social sharing (1200×630px).
Run once inside the Docker container after building:
  docker exec inkcoverage-inkcoverage-1 python generate_og_image.py
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Palette  (matches the app's CSS variables)
# ---------------------------------------------------------------------------
BG          = (14,  15,  19)   # --bg
SURFACE     = (22,  23,  29)   # --surface
BORDER      = (42,  43,  54)   # --border
TEXT        = (232, 233, 237)  # --text
TEXT_DIM    = (139, 141, 154)  # --text-dim
TEXT_MUTED  = (92,  94,  110)  # --text-muted
ACCENT      = (108, 138, 255)  # --accent
CYAN        = (0,   180, 204)  # --cyan-color
MAGENTA     = (232, 32,  90)   # --magenta-color
YELLOW      = (245, 200, 0)    # --yellow-color
KEY         = (55,  56,  72)   # Dark bar — visible against BG

W, H = 1200, 630

# ---------------------------------------------------------------------------
# Fonts  (Liberation Sans ships with fonts-liberation on Debian/Ubuntu)
# ---------------------------------------------------------------------------
FONT_DIR = Path("/usr/share/fonts/truetype/liberation")
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / name
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()

font_label   = _font("LiberationSans-Bold.ttf",    15)
font_heading = _font("LiberationSans-Bold.ttf",    68)
font_sub     = _font("LiberationSans-Bold.ttf",    40)
font_tagline = _font("LiberationSans-Regular.ttf", 22)
font_url     = _font("LiberationSans-Regular.ttf", 17)

# ---------------------------------------------------------------------------
# Canvas
# ---------------------------------------------------------------------------
img  = Image.new("RGB", (W, H), BG)
draw = ImageDraw.Draw(img)

# ---------------------------------------------------------------------------
# Left: CMYK bar chart
# ---------------------------------------------------------------------------
BARS = [
    (CYAN,    0.55),
    (MAGENTA, 0.78),
    (YELLOW,  0.42),
    (KEY,     0.90),
]

BAR_W     = 72
BAR_GAP   = 22
BAR_FLOOR = H - 72     # bottom edge of all bars
BAR_CEIL  = 72         # maximum top (full height)
MAX_BAR_H = BAR_FLOOR - BAR_CEIL
BAR_X0    = 68
RADIUS    = 10

for i, (color, ratio) in enumerate(BARS):
    x       = BAR_X0 + i * (BAR_W + BAR_GAP)
    bar_h   = int(MAX_BAR_H * ratio)
    y_top   = BAR_FLOOR - bar_h
    draw.rounded_rectangle(
        [x, y_top, x + BAR_W, BAR_FLOOR],
        radius=RADIUS, fill=color,
    )

# Subtle CMYK colour dots below bars (like channel labels)
DOT_Y = BAR_FLOOR + 18
DOT_R = 5
for i, (color, _) in enumerate(BARS):
    cx = BAR_X0 + i * (BAR_W + BAR_GAP) + BAR_W // 2
    draw.ellipse([cx - DOT_R, DOT_Y - DOT_R, cx + DOT_R, DOT_Y + DOT_R], fill=color)

# ---------------------------------------------------------------------------
# Vertical divider
# ---------------------------------------------------------------------------
DIV_X = BAR_X0 + len(BARS) * (BAR_W + BAR_GAP) - BAR_GAP + 52
draw.line([(DIV_X, 60), (DIV_X, H - 60)], fill=BORDER, width=1)

# ---------------------------------------------------------------------------
# Right: typography
# ---------------------------------------------------------------------------
TX = DIV_X + 64   # left edge of text column

# Brand label
draw.text((TX, 138), "InkCoverage.app", font=font_label, fill=ACCENT)

# Headline  (two lines)
draw.text((TX, 172), "Free PDF Ink",       font=font_heading, fill=TEXT)
draw.text((TX, 248), "Coverage Analyzer",  font=font_sub,     fill=TEXT)

# Separator rule
RULE_Y = 318
draw.line([(TX, RULE_Y), (TX + 480, RULE_Y)], fill=BORDER, width=1)

# Tagline
draw.text((TX, 334), "CMYK & spot color coverage by channel",     font=font_tagline, fill=TEXT_DIM)
draw.text((TX, 364), "Per page · Crop area · Batch · CSV export", font=font_tagline, fill=TEXT_DIM)
draw.text((TX, 394), "For flexo, offset & digital prepress",      font=font_tagline, fill=TEXT_DIM)

# URL
draw.text((TX, H - 72), "inkcoverage.app", font=font_url, fill=TEXT_MUTED)

# ---------------------------------------------------------------------------
# Save
# ---------------------------------------------------------------------------
out = Path(__file__).parent / "static" / "og-image.png"
img.save(str(out), "PNG", optimize=True)
print(f"Saved {out}  ({W}×{H}px)")
