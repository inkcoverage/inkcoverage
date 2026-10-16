"""Real separation masks and bounded, private channel-preview requests."""
import asyncio
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as backend
import workload as limits
from channel_preview import build_channel_masks
from PIL import Image
from test_ink_limit import GS, ROOT, fixture
from test_session5 import call, JSON


def mask(channel):
    return Image.open(io.BytesIO(base64.b64decode(channel['mask'].split(',')[1]))).getchannel('A')


class MaskTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp'); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'sep(White).tif'
        plane = Image.new('L', (3, 1)); plane.putdata([255, 127, 0]); plane.save(self.path)

    def test_actual_ink_shape_tints_and_colour_are_preserved(self):
        channels = build_channel_masks([(self.path, 'White')], {'White': '#ffffff'}, (3, 1))
        self.assertEqual(channels[0]['name'], 'White'); self.assertEqual(channels[0]['displayColor'], '#ffffff')
        self.assertEqual(channels[0]['maskEncoding'], 'alpha')
        self.assertEqual([mask(channels[0]).getpixel((x, 0)) for x in range(3)], [0, 128, 255])

    def test_every_tint_survives_in_alpha_without_source_colour_metadata(self):
        plane = Image.new('L', (256, 1)); plane.putdata(range(255, -1, -1))
        plane.save(self.path, icc_profile=b'untrusted source profile')
        channel = build_channel_masks([(self.path, 'Cyan')], {}, (256, 1))[0]
        image = Image.open(io.BytesIO(base64.b64decode(channel['mask'].split(',')[1])))
        self.assertNotIn('icc_profile', image.info)
        self.assertEqual(list(image.getchannel('A').tobytes()), list(range(256)))

    def test_unknown_colour_is_not_guessed_from_ink_name(self):
        channels = build_channel_masks([(self.path, 'P485')], {}, (3, 1))
        self.assertNotIn('displayColor', channels[0])

    def test_compressed_output_and_decoded_storage_and_channel_bounds(self):
        for named, size in (([(self.path, 'x')] * 65, (3, 1)), ([(self.path, 'x')] * 5, (4000, 4000))):
            with self.assertRaises(limits.WorkloadError): build_channel_masks(named, {}, size)
        with patch.object(limits, 'MAX_OUTPUT_BYTES', 1), self.assertRaises(limits.WorkloadError):
            build_channel_masks([(self.path, 'White')], {}, (3, 1))

    def test_deadline_is_checked_before_decoding(self):
        with patch.object(limits, 'check_deadline', side_effect=limits.WorkloadError('job_timeout', 422)), self.assertRaises(limits.WorkloadError):
            build_channel_masks([(self.path, 'White')], {}, (3, 1))


@unittest.skipUnless(GS, 'Requires installed Ghostscript')
class RenderedMaskTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'tmp'); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.pdf = self.root / 'owned.pdf'; fixture(self.pdf)
        self.addCleanup(patch.stopall)
        patch.object(backend, 'TEMP_DIR', self.root).start()
        patch.object(backend, '_find_gs', return_value=GS).start()

    def check(self, pdf, page=1):
        data = backend._channel_preview_job(backend.PreviewRequest(id='a' * 32, page=page), pdf)
        self.assertFalse(list(self.root.glob('analysis_*')))
        self.assertEqual((data['width'], data['height']), (round(data['widthPt'] * 150 / 72), round(data['heightPt'] * 150 / 72)))
        return data, {channel['name']: mask(channel) for channel in data['channels']}

    def test_spot_uses_document_definition_and_real_mask_not_preview_colour_guess(self):
        data, channels = self.check(self.pdf)
        self.assertEqual(next(ch for ch in data['channels'] if ch['name'] == 'White')['displayColor'], '#00ff00')
        self.assertEqual(channels['White'].getpixel((225, 150)), 255)
        self.assertEqual(channels['White'].getpixel((30, 30)), 0)
        self.assertEqual(channels['Black'].getpixel((30, 30)), 255)
        self.assertEqual(channels['Black'].getpixel((225, 150)), 0)
        self.assertTrue(200 <= channels['Cyan'].getpixel((30, 30)) <= 205)

    def test_overprint_knockout_rotated_and_offset_page_alignment(self):
        matrix = ROOT / 'tests/fixtures/session13.pdf'
        for page in (8, 9, 10, 11):
            with self.subTest(page=page):
                data, channels = self.check(matrix, page)
                if page in (8, 9):
                    self.assertEqual(channels['Cyan'].getpixel((100, 100)), 255 if page == 8 else 0)
                    self.assertEqual(channels['Magenta'].getpixel((100, 100)), 255)
                else:
                    self.assertEqual(channels['Cyan'].getpixel((20, 20)), 255)
                    clear = (20, data['height'] - 20) if page == 10 else (data['width'] - 20, 20)
                    self.assertEqual(channels['Cyan'].getpixel(clear), 0)

    def test_failure_releases_scratch_lease(self):
        with patch.object(backend, '_run_tiffsep', side_effect=backend.ProcessingError('analysis_failed')), self.assertRaises(backend.ProcessingError):
            self.check(self.pdf)
        self.assertFalse(list(self.root.glob('analysis_*')))


class ApiTests(unittest.TestCase):
    def test_body_request_and_unavailable_source_bounds(self):
        with patch.object(backend, '_channel_preview_job') as job:
            for body, expected in ((b'x' * 20000, 413), (json.dumps({'id':'a' * 32, 'page':0}).encode(), 422),
                                   (json.dumps({'id':'a' * 32, 'page':True}).encode(), 422),
                                   (json.dumps({'id':'a' * 32, 'page':1}).encode(), 404)):
                status, _, headers = asyncio.run(call('/api/channel-preview', body, JSON))
                self.assertEqual(status, expected); self.assertEqual(headers[b'cache-control'], b'no-store')
            job.assert_not_called()


if __name__ == '__main__': unittest.main()
