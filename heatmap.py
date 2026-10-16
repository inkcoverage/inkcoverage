"""Bounded ink-limit statistics and overlay from validated separation rasters."""
import base64
import io
import math

from PIL import Image, ImageMath
import workload as limits


def build_heatmap(separations, size, bounds, limit):
    left, top, right, bottom = bounds
    checked_size = (right - left, bottom - top)
    total = Image.new('I', checked_size, 0)
    for path in separations:
        limits.check_deadline()
        with path.open('rb') as source, Image.open(source, formats=['TIFF']) as image:
            plane = image.crop(bounds)
            total = ImageMath.lambda_eval(lambda v: v['total'] + (255 - v['plane']),
                                          total=total, plane=plane)
    limits.check_deadline()
    maximum = total.getextrema()[1]
    # Integer sums avoid rounding the threshold or a reading before comparison.
    # More intense colours mean more than 25 / 50 percentage points of excess.
    grades = ImageMath.lambda_eval(
        lambda v: (v['ink'] > v['limit']) + (v['ink'] > v['warm']) + (v['ink'] > v['hot']),
        ink=total, limit=math.floor(limit * 255 / 100),
        warm=math.floor((limit + 25) * 255 / 100), hot=math.floor((limit + 50) * 255 / 100)
    ).convert('L')
    histogram = grades.histogram()
    over = sum(histogram[1:])
    checked = checked_size[0] * checked_size[1]
    overlay = Image.new('P', size, 0)
    overlay.putpalette([0, 0, 0, 255, 196, 0, 255, 116, 36, 239, 48, 75] + [0] * (768 - 12))
    overlay.paste(grades, (left, top))
    # Strong enough to read the severity colours over dark/coloured artwork.
    overlay.info['transparency'] = bytes([0, 200, 218, 232] + [0] * 252)
    output = io.BytesIO()
    overlay.save(output, format='PNG')
    limits.check_deadline()
    if output.tell() > limits.MAX_OUTPUT_BYTES:
        raise limits.WorkloadError('job_limit', 422)
    return {'image': 'data:image/png;base64,' + base64.b64encode(output.getvalue()).decode('ascii'),
            'maximumCoverage': round(maximum * 100 / 255, 2),
            'areaOverLimit': round(over * 100 / checked, 4),
            'pixelsOverLimit': over, 'checkedPixels': checked}
