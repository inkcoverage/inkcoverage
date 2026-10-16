"""Bounded per-ink preview masks; no measurements or retained output files."""
import base64
import io
from PIL import Image, ImageOps
import workload as limits

# Bound decoded browser storage, not just the compressed PNG response.
MAX_CHANNEL_PIXELS = 32_000_000


def build_channel_masks(named, colors, size):
    if not 1 <= len(named) <= 64 or size[0] * size[1] * len(named) > MAX_CHANNEL_PIXELS:
        raise limits.WorkloadError('job_limit', 422)
    channels, output_bytes = [], 0
    for path, name in named:
        limits.check_deadline()
        with path.open('rb') as source, Image.open(source, formats=['TIFF']) as separation:
            # Zero means no ink; 255 means full coverage. Preserve all tints.
            ink = ImageOps.invert(separation)
            # Store coverage in alpha: browser/monitor colour management can
            # change greyscale RGB values, but must preserve transparency.
            mask = Image.new('LA', separation.size, (255, 0))
            mask.putalpha(ink)
            output = io.BytesIO()
            mask.save(output, format='PNG')
        output_bytes += output.tell()
        if output_bytes > limits.MAX_OUTPUT_BYTES:
            raise limits.WorkloadError('job_limit', 422)
        channels.append({'name': name, 'maskEncoding': 'alpha', **({'displayColor': colors[name]} if name in colors else {}),
                         'mask': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii')})
    limits.check_deadline()
    return channels
