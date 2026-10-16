"""Local ink-limit measurements: strict limits, scope, real renderer and bounds."""
import asyncio
import base64
import io
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as backend
import workload as limits
from heatmap import build_heatmap
from pdf_colors import inspect_pdf
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject,
                          DictionaryObject, FloatObject, NameObject, NumberObject)
from test_session5 import call, JSON

ROOT = Path(__file__).resolve().parents[1]
LOCAL_GS = ROOT / '.local-runtime/ghostscript-10.08.0/bin/gswin64c.exe'
GS = str(LOCAL_GS) if LOCAL_GS.exists() else shutil.which('gs')
PROCESS = ['Cyan', 'Magenta', 'Yellow', 'Black']


def fixture(path):
    """A small 340% patch and a 100% spot; spot is deliberately named White."""
    writer = PdfWriter()
    function = DictionaryObject({NameObject('/FunctionType'): NumberObject(2),
        NameObject('/Domain'): ArrayObject([NumberObject(0), NumberObject(1)]),
        NameObject('/C0'): ArrayObject([NumberObject(1)] * 3),
        NameObject('/C1'): ArrayObject([NumberObject(0), NumberObject(1), NumberObject(0)]),
        NameObject('/N'): NumberObject(1)})
    space = ArrayObject([NameObject('/Separation'), NameObject('/White'),
                         NameObject('/DeviceRGB'), writer._add_object(function)])
    resources = DictionaryObject({NameObject('/ColorSpace'): DictionaryObject({NameObject('/Ink'): space}),
        NameObject('/ExtGState'): DictionaryObject({NameObject('/O'): DictionaryObject({
            NameObject('/OP'): BooleanObject(True), NameObject('/op'): BooleanObject(True), NameObject('/OPM'): NumberObject(1)})})})
    for data, rotation in ((b'.8 .8 .8 1 k 0 108 36 36 re f /O gs /Ink cs 1 scn 72 0 72 144 re f', 0),
                           (b'1 0 0 0 k 0 0 72 144 re f', 90)):
        page = writer.add_blank_page(width=144, height=144)
        page[NameObject('/Resources')] = resources
        stream = DecodedStreamObject(); stream.set_data(data)
        page[NameObject('/Contents')] = writer._add_object(stream)
        if rotation: page.rotate(rotation)
    writer.write(path)


def request(page=1, width=144, height=144, channels=None, limit=300, **crop):
    return backend.InkLimitRequest(id='a' * 32, page=page, cropLeft=crop.get('left', 0),
        cropTop=crop.get('top', 0), cropWidth=crop.get('w', width), cropHeight=crop.get('h', height),
        pdfWidthPt=width, pdfHeightPt=height, channels=channels or PROCESS, limit=limit)


def rgba(data):
    return Image.open(io.BytesIO(base64.b64decode(data['image'].split(',')[1]))).convert('RGBA')


class PixelTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def plane(self, name, ink, size=(4, 2)):
        image = Image.new('L', size)
        image.putdata([255 - value for value in ink])
        path = self.root / f'{name}.tif'; image.save(path); return path

    def test_strict_boundary_with_unrounded_total_and_transparency(self):
        # Exactly 100% stays clear. A single 8-bit step above it is highlighted.
        a = self.plane('a', [255, 255, 255, 255, 0, 0, 0, 0])
        b = self.plane('b', [0, 1, 64, 128, 0, 0, 0, 0])
        data = build_heatmap([a, b], (4, 2), (0, 0, 4, 2), 100)
        self.assertEqual(data['pixelsOverLimit'], 3)
        self.assertEqual(data['checkedPixels'], 8)
        self.assertEqual(data['areaOverLimit'], 37.5)
        self.assertEqual(data['maximumCoverage'], 150.2)
        self.assertEqual([rgba(data).getpixel((x, 0))[3] for x in range(4)], [0, 200, 218, 232])

    def test_selection_denominator_and_full_page_overlay_alignment(self):
        a = self.plane('a', [255, 0, 255, 0, 0, 0, 0, 0])
        data = build_heatmap([a], (4, 2), (1, 0, 3, 1), 50)
        self.assertEqual((data['checkedPixels'], data['pixelsOverLimit'], data['areaOverLimit']), (2, 1, 50))
        overlay = rgba(data); self.assertEqual(overlay.size, (4, 2))
        self.assertEqual(overlay.getpixel((0, 0))[3], 0)
        self.assertGreater(overlay.getpixel((2, 0))[3], 0)
        self.assertEqual(overlay.getpixel((2, 1))[3], 0)

    def test_excluding_channel_and_decimal_limit_can_clear_heatmap(self):
        a = self.plane('a', [255] * 8); b = self.plane('b', [1] * 8)
        self.assertEqual(build_heatmap([a, b], (4, 2), (0, 0, 4, 2), 100.39)['pixelsOverLimit'], 8)
        self.assertEqual(build_heatmap([a, b], (4, 2), (0, 0, 4, 2), 100.4)['pixelsOverLimit'], 0)
        self.assertEqual(build_heatmap([a], (4, 2), (0, 0, 4, 2), 100)['pixelsOverLimit'], 0)

    def test_totals_do_not_clip_at_400_percent(self):
        paths = [self.plane(str(i), [255] * 8) for i in range(6)]
        data = build_heatmap(paths, (4, 2), (0, 0, 4, 2), 500)
        self.assertEqual((data['maximumCoverage'], data['areaOverLimit']), (600, 100))

    def test_output_budget_and_deadline_are_applied(self):
        path = self.plane('a', [255] * 8)
        with patch.object(limits, 'MAX_OUTPUT_BYTES', 1), self.assertRaises(limits.WorkloadError):
            build_heatmap([path], (4, 2), (0, 0, 4, 2), 50)
        with patch.object(limits, 'check_deadline', side_effect=limits.WorkloadError('job_limit', 422)), self.assertRaises(limits.WorkloadError):
            build_heatmap([path], (4, 2), (0, 0, 4, 2), 50)


@unittest.skipUnless(GS, 'Requires installed Ghostscript')
class RendererTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.pdf = self.root / 'owned.pdf'; fixture(self.pdf)
        self.addCleanup(patch.stopall)
        patch.object(backend, 'TEMP_DIR', self.root).start()
        patch.object(backend, '_find_gs', return_value=GS).start()

    def run_check(self, req, pdf=None):
        data = backend._ink_limit_job(req, pdf or self.pdf)
        self.assertFalse(list(self.root.glob('analysis_*')), 'Scratch separations must be removed')
        return data

    def test_small_heavy_patch_is_detected_despite_low_page_average(self):
        data = self.run_check(request())
        self.assertAlmostEqual(data['maximumCoverage'], 340, delta=1)
        self.assertEqual(data['areaOverLimit'], 6.25)
        self.assertEqual(data['excludedChannels'], ['White'])
        overlay = rgba(data); self.assertEqual(overlay.size, (300, 300))
        self.assertGreater(overlay.getpixel((30, 30))[3], 0)
        self.assertEqual(overlay.getpixel((200, 150))[3], 0)
        self.assertEqual(overlay.getpixel((30, 200))[3], 0)

    def test_spot_chooser_preserves_name_and_document_colour(self):
        info = inspect_pdf(self.pdf, 1)
        self.assertEqual(info['inkChannelsStatus'], 'complete')
        self.assertEqual(info['inkChannels'][-1], {'name': 'White', 'displayColor': '#00ff00'})
        with patch.object(backend, '_page_colors', return_value=info), patch.object(backend, '_run_tiffsep') as raster:
            self.assertEqual(backend._ink_channels_job(backend.PreviewRequest(id='a' * 32, page=1), self.pdf)['channels'], info['inkChannels'])
            raster.assert_not_called()
        with patch.object(backend, '_page_colors', return_value={'inkChannelsStatus': 'unknown'}):
            inks = backend._ink_channels_job(backend.PreviewRequest(id='a' * 32, page=1), self.pdf)['channels']
            self.assertIn('White', [ink['name'] for ink in inks])
        included = self.run_check(request(channels=PROCESS + ['White'], limit=50))
        excluded = self.run_check(request(limit=50))
        self.assertEqual(included['areaOverLimit'], 56.25)
        self.assertEqual(excluded['areaOverLimit'], 6.25)

    def test_selection_peak_area_and_clear_result(self):
        patch_data = self.run_check(request(w=36, h=36))
        self.assertEqual(patch_data['areaOverLimit'], 100)
        clear = self.run_check(request(left=36, top=36, w=36, h=36))
        self.assertEqual((clear['maximumCoverage'], clear['areaOverLimit'], clear['pixelsOverLimit']), (0, 0, 0))
        self.assertEqual(rgba(clear).getbbox(), None)

    def test_rotation_nonzero_origin_transparency_overprint_and_knockout(self):
        matrix = ROOT / 'tests/fixtures/session13.pdf'
        for page, threshold, maximum, area in ((7, 60, 50, 0), (8, 150, 200, 100), (9, 150, 100, 0),
                                              (10, 50, 100, 50), (11, 50, 100, 50)):
            with self.subTest(page=page):
                width, height = backend._inspect_page(GS, matrix, page)
                data = self.run_check(request(page=page, width=width, height=height, limit=threshold), matrix)
                self.assertAlmostEqual(data['maximumCoverage'], maximum, delta=1)
                self.assertAlmostEqual(data['areaOverLimit'], area, delta=.5)
                if page in (10, 11):
                    overlay = rgba(data)
                    self.assertGreater(overlay.getpixel((20, 20))[3], 0)
                    self.assertEqual(overlay.getpixel((20, overlay.height - 20) if page == 10 else (overlay.width - 20, 20))[3], 0)

    def test_invalid_inks_geometry_and_empty_raster_selection_reject(self):
        for req in (request(channels=['Missing']), request(channels=['Cyan', 'Cyan']),
                    request(width=145), request(left=143, w=10), request(w=.0001, h=.0001)):
            with self.subTest(request=req):
                with self.assertRaises(backend.HTTPException) as rejected: self.run_check(req)
                self.assertEqual(rejected.exception.status_code, 400)
                self.assertFalse(list(self.root.glob('analysis_*')))

    def test_discovery_fallback_obeys_raster_budget(self):
        with patch.object(backend, '_page_colors', return_value={'inkChannelsStatus': 'unknown'}), \
             patch.object(backend, '_inspect_page', return_value=(100000, 100000)), \
             patch.object(backend, '_run_tiffsep') as raster, self.assertRaises(limits.WorkloadError):
            backend._ink_channels_job(backend.PreviewRequest(id='a' * 32, page=1), self.pdf)
        raster.assert_not_called()


class ApiTests(unittest.TestCase):
    def test_invalid_requests_and_body_bound_do_not_render(self):
        body = request().model_dump()
        with patch.object(backend, '_ink_limit_job') as job:
            for change in ({'limit': float('nan')}, {'limit': -1}, {'limit': 6401}, {'channels': []},
                           {'channels': ['a'] * 65}, {'channels': [7]}, {'channels': ['x' * 1025]}):
                status, _, _ = asyncio.run(call('/api/ink-limit', json.dumps({**body, **change}).encode(), JSON))
                self.assertEqual(status, 422)
            for route in ('/api/ink-limit', '/api/ink-limit/channels'):
                status, _, headers = asyncio.run(call(route, b'x' * 20000, JSON))
                self.assertEqual(status, 413); self.assertEqual(headers[b'cache-control'], b'no-store')
            job.assert_not_called()

    def test_unavailable_source_rejects_before_processing(self):
        with patch.object(backend, '_ink_limit_job') as job:
            status, _, headers = asyncio.run(call('/api/ink-limit', request().model_dump_json().encode(), JSON))
            self.assertEqual(status, 404); self.assertEqual(headers[b'cache-control'], b'no-store')
            job.assert_not_called()


if __name__ == '__main__': unittest.main()
