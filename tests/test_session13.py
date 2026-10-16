"""Page-scoped colour detection and safe incomplete-inspection behaviour."""
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as backend
from pdf_colors import Inspector, clean_name, inspect_pdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

FIXTURE = Path(__file__).parent / 'fixtures/session13.pdf'


class ColorTests(unittest.TestCase):
    def test_used_vectors_images_forms_profiles_defaults_patterns_inline(self):
        for page in (2, 3, 4, 12, 13, 15, 16):
            with self.subTest(page=page):
                self.assertEqual(inspect_pdf(FIXTURE, page)['rgbStatus'], 'detected')

    def test_cmyk_and_unused_rgb_resources_do_not_warn(self):
        for page in (1, 5, 6, 7, 8, 9, 10, 11, 14):
            with self.subTest(page=page):
                self.assertEqual(inspect_pdf(FIXTURE, page)['rgbStatus'], 'not_detected')

    def test_profile_roles_are_separate_and_page_specific(self):
        rgb = inspect_pdf(FIXTURE, 4)
        self.assertTrue(any('sRGB' in name for name in rgb['sourceProfiles']))
        self.assertEqual(rgb['outputProfiles'], [])
        output = inspect_pdf(FIXTURE, 5)
        self.assertTrue(output['outputProfiles'])
        self.assertEqual(output['sourceProfiles'], [])
        condition = inspect_pdf(FIXTURE, 14)
        self.assertEqual(condition['outputConditions'], ['FOGRA39'])
        self.assertEqual(condition['outputProfiles'], [])
        self.assertEqual(inspect_pdf(FIXTURE, 6)['outputProfiles'], [])

    def inspect_content(self, content, resources=None):
        writer = PdfWriter()
        page = writer.add_blank_page(width=144, height=144)
        stream = DecodedStreamObject(); stream.set_data(content)
        page[NameObject('/Contents')] = writer._add_object(stream)
        page[NameObject('/Resources')] = resources or DictionaryObject()
        data = io.BytesIO(); writer.write(data); data.seek(0)
        reader = PdfReader(data)
        return Inspector(reader).inspect(reader.pages[0])

    def test_graphics_state_does_not_leak_unused_rgb(self):
        data = self.inspect_content(b'q 1 0 0 rg Q 1 0 0 0 k 0 0 144 144 re f')
        self.assertEqual(data['rgbStatus'], 'not_detected')
        data = self.inspect_content(b'1 0 0 rg 1 0 0 0 k 0 0 144 144 re f')
        self.assertEqual(data['rgbStatus'], 'not_detected')

    def test_mixed_rgb_and_cmyk_and_rgb_text_stroke(self):
        for content in (b'1 0 0 rg 0 0 72 144 re f 1 0 0 0 k 72 0 72 144 re f',
                        b'1 0 0 RG 0 0 m 100 100 l S', b'BT 1 0 0 rg (owned) Tj ET'):
            self.assertEqual(self.inspect_content(content)['rgbStatus'], 'detected')

    def test_rgb_blending_space_on_a_page(self):
        writer = PdfWriter();page = writer.add_blank_page(width=144, height=144)
        stream=DecodedStreamObject();stream.set_data(b'1 0 0 0 k 0 0 144 144 re f')
        page[NameObject('/Contents')]=writer._add_object(stream)
        page[NameObject('/Group')]=DictionaryObject({NameObject('/S'):NameObject('/Transparency'),NameObject('/CS'):NameObject('/DeviceRGB')})
        data=io.BytesIO();writer.write(data);data.seek(0);reader=PdfReader(data)
        self.assertEqual(Inspector(reader).inspect(reader.pages[0])['rgbStatus'], 'detected')

    def test_unresolvable_colour_and_budget_are_unknown(self):
        self.assertEqual(self.inspect_content(b'/Missing cs 0 0 144 144 re f')['rgbStatus'], 'unknown')
        with patch('pdf_colors.MAX_BYTES', 1):
            self.assertEqual(self.inspect_content(b'0 0 144 144 re f')['rgbStatus'], 'unknown')

    def test_names_are_bounded_and_control_characters_removed(self):
        self.assertEqual(clean_name('ICC\x00\n profile\u202e'), 'ICC profile')
        self.assertEqual(len(clean_name('x' * 1000)), 128)

    def test_parser_timeout_failure_and_malformed_json_do_not_claim_no_rgb(self):
        for response in (Mock(returncode=1, stdout=''), Mock(returncode=0, stdout='not-json'),
                         Mock(returncode=0, stdout='[]'),
                         Mock(returncode=0, stdout=json.dumps({'rgbStatus':'not_detected'}))):
            with patch.object(backend.limits, 'execute_processor', return_value=response):
                self.assertEqual(backend._page_colors('owned.pdf', 1)['rgbStatus'], 'unknown')
        with patch.object(backend.limits, 'execute_processor', side_effect=subprocess.TimeoutExpired('test',15)):
            self.assertEqual(backend._page_colors('owned.pdf', 1)['rgbStatus'], 'unknown')

    def test_detected_rgb_is_kept_when_a_later_feature_is_unknown(self):
        self.assertEqual(self.inspect_content(b'1 0 0 rg 0 0 144 144 re f /Missing Do')['rgbStatus'], 'detected')


if __name__ == '__main__': unittest.main()
