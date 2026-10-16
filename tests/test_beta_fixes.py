"""Spot swatches must follow PDF definitions, including misleading ink names."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as backend
from PIL import Image
from pypdf import PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject)

ROOT = Path(__file__).resolve().parents[1]
LOCAL_GS = ROOT / '.local-runtime/ghostscript-10.08.0/bin/gswin64c.exe'
GS = str(LOCAL_GS) if LOCAL_GS.exists() else shutil.which('gs')


def spot_fixture(path):
    writer = PdfWriter()
    for rgb in ((0, 1, 0), (1, 0, 0)):
        page = writer.add_blank_page(width=144, height=144)
        function = DictionaryObject({
            NameObject('/FunctionType'): NumberObject(2),
            NameObject('/Domain'): ArrayObject([NumberObject(0), NumberObject(1)]),
            NameObject('/C0'): ArrayObject([NumberObject(1)] * 3),
            NameObject('/C1'): ArrayObject([FloatObject(v) for v in rgb]),
            NameObject('/N'): NumberObject(1),
        })
        space = ArrayObject([NameObject('/Separation'), NameObject('/P485'),
                             NameObject('/DeviceRGB'), writer._add_object(function)])
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/ColorSpace'):
            DictionaryObject({NameObject('/Ink'): space})})
        stream = DecodedStreamObject()
        stream.set_data(b'/Ink cs 1 scn 0 0 144 144 re f')
        page[NameObject('/Contents')] = writer._add_object(stream)
    writer.write(path)


class SpotDiagnosticsTests(unittest.TestCase):
    def test_full_tint_range_and_quoted_ink_name(self):
        with patch.object(backend, '_spot_display_transform', return_value=None):
            colors = backend._spot_display_colors('unused',
                '%%SeparationColor: "P485" 100% ink = 32760 0 32760 0 CMYK\n'
                '%%SeparationColor: "Ink "green"" 100% ink = 0 0 0 32760 CMYK')
        self.assertEqual(colors, {'P485': '#00ff00', 'Ink "green"': '#000000'})

    def test_missing_malformed_and_out_of_range_colors_are_unknown(self):
        with patch.object(backend, '_spot_display_transform', return_value=None):
            colors = backend._spot_display_colors('unused',
                '%%SeparationColor: "bad" 100% ink = 32761 0 0 0 CMYK\n'
                '%%SeparationColor: "negative" 100% ink = -1 0 0 0 CMYK\n'
                '%%SeparationColor: "bad" 100% ink = nan 0 0 0 CMYK\n'
                '%%SeparationName: P485\n')
        self.assertEqual(colors, {})

    def test_diagnostics_have_a_channel_bound(self):
        with patch.object(backend, '_spot_display_transform', return_value=None):
            colors = backend._spot_display_colors('unused', '\n'.join(
                f'%%SeparationColor: "Ink{i}" 100% ink = 0 0 0 0 CMYK' for i in range(100)))
        self.assertEqual(len(colors), 64)


@unittest.skipUnless(GS, 'Requires installed Ghostscript')
class RenderedSpotTests(unittest.TestCase):
    def test_same_name_uses_each_pages_definition_and_keeps_coverage(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as directory:
            root = Path(directory)
            pdf = root / 'owned.pdf'
            spot_fixture(pdf)
            with patch.object(backend, 'TEMP_DIR', root):
                for page, dominant in ((1, 1), (2, 0)):
                    with patch.object(backend, '_find_gs', return_value=GS):
                        data = backend._analysis_job(backend.AnalyzeRequest(id='a' * 32, page=page,
                            cropLeft=0, cropTop=0, cropWidth=144, cropHeight=144,
                            pdfWidthPt=144, pdfHeightPt=144), pdf)
                    spot = next(ch for ch in data['channels'] if ch['name'] == 'P485')
                    self.assertEqual(spot['coverage'], 100)
                    self.assertEqual(data['spotTotal'], 100)
                    self.assertEqual(data['cmykTotal'], 0)
                    rgb = tuple(bytes.fromhex(spot['displayColor'][1:]))
                    self.assertGreater(rgb[dominant], max(v for i, v in enumerate(rgb) if i != dominant) + 60)
                    png = root / f'page-{page}.png'
                    backend._render_preview(GS, str(pdf), page, str(png))
                    with Image.open(png) as preview:
                        pixel = preview.getpixel((preview.width // 2, preview.height // 2))
                    # Profiles and 8-bit CMYK quantization may differ slightly.
                    self.assertLess(max(abs(a - b) for a, b in zip(rgb, pixel)), 12)


if __name__ == '__main__':
    unittest.main()
