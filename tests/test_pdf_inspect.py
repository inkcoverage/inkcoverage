"""Estimator inspector: source inks, exact curve bounds, transforms and API leases."""
import asyncio
import base64
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as backend
import pdf_inspect
from PIL import Image
from pdf_inspect import Inspector, inspect_pdf
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, BooleanObject, DecodedStreamObject, DictionaryObject,
                          FloatObject, NameObject, NumberObject, RectangleObject, TextStringObject)
from test_session5 import call, JSON
from test_ink_limit import GS

ROOT = Path(__file__).resolve().parents[1]


def dictionary(**values):
    return DictionaryObject({NameObject('/'+k): v for k, v in values.items()})


def array(*values):
    return ArrayObject([FloatObject(v) for v in values])


def stream(data, **values):
    obj = DecodedStreamObject(); obj.set_data(data.encode() if isinstance(data, str) else data)
    obj.update(dictionary(**values)); return obj


def read(content, resources=None, *, rotation=0, box=None, unit=1):
    writer = PdfWriter(); page = writer.add_blank_page(width=300, height=200)
    page[NameObject('/Contents')] = writer._add_object(stream(content))
    page[NameObject('/Resources')] = resources or DictionaryObject()
    if box is not None: page.mediabox = page.cropbox = RectangleObject(box)
    if rotation: page.rotate(rotation)
    if unit != 1: page[NameObject('/UserUnit')] = FloatObject(unit)
    data = io.BytesIO(); writer.write(data); data.seek(0)
    return Inspector(PdfReader(data), 1).inspect()


def fixture(path):
    """Owned 100 × 60 mm label, spot-ink logo, CMYK panel and tricky objects."""
    writer = PdfWriter()
    foil = dictionary(Type=NameObject('/OCG'), Name=TextStringObject('Logo / foil'))
    cut = dictionary(Type=NameObject('/OCG'), Name=TextStringObject('Dieline'))
    foil_ref, cut_ref = writer._add_object(foil), writer._add_object(cut)
    function = dictionary(FunctionType=NumberObject(2), Domain=array(0, 1), C0=array(0, 0, 0, 0),
                          C1=array(0, .3, 1, .1), N=NumberObject(1))
    gold = ArrayObject([NameObject('/Separation'), NameObject('/Gold Foil'), NameObject('/DeviceCMYK'), function])
    cut_color = ArrayObject([NameObject('/Separation'), NameObject('/CutContour'), NameObject('/DeviceCMYK'),
                            dictionary(FunctionType=NumberObject(2), Domain=array(0, 1), C0=array(0, 0, 0, 0), C1=array(0, 1, 0, 0), N=NumberObject(1))])
    image = stream(bytes([25, 150, 210]), Type=NameObject('/XObject'), Subtype=NameObject('/Image'),
                   Width=NumberObject(1), Height=NumberObject(1), ColorSpace=NameObject('/DeviceRGB'), BitsPerComponent=NumberObject(8))
    form = stream('0 .7 1 0 k 0 0 40 20 re f', Type=NameObject('/XObject'), Subtype=NameObject('/Form'),
                  BBox=array(0, 0, 40, 20), Matrix=array(1, 0, 0, 1, 20, 0), Resources=dictionary())
    resources = dictionary(ColorSpace=dictionary(Gold=gold, Cut=cut_color),
        Properties=dictionary(Foil=foil_ref, Cut=cut_ref),
        ExtGState=dictionary(Alpha=dictionary(ca=FloatObject(.45))),
        XObject=dictionary(Im=writer._add_object(image), Form=writer._add_object(form)),
        Font=dictionary(F1=dictionary(Type=NameObject('/Font'), Subtype=NameObject('/Type1'), BaseFont=NameObject('/Helvetica'))),
        Shading=dictionary(Shade=dictionary(ShadingType=NumberObject(2), ColorSpace=NameObject('/DeviceRGB'),
            Coords=array(0, 0, 70, 0), Function=dictionary(FunctionType=NumberObject(2), Domain=array(0, 1),
                C0=array(1, .3, .1), C1=array(.2, .4, 1), N=NumberObject(1)), Extend=ArrayObject([BooleanObject(True), BooleanObject(True)]))))
    w, h = 100*72/25.4, 60*72/25.4
    content = f'''
      .05 .03 0 0 k 50 110 {w} {h} re f
      .85 .25 0 .05 k 50 110 {w} 42 re f
      /OC /Foil BDC /Gold cs 1 scn 102 215 m 144 215 l 157 245 l 170 215 l 212 215 l 178 193 l 191 162 l 157 182 l 123 162 l 136 193 l h f EMC
      /OC /Cut BDC /Cut CS 1 SCN .6 w 50 110 {w} {h} re S EMC
      BT /F1 12 Tf 0 0 0 1 k 65 124 Td (100 x 60 mm label) Tj ET
      q /Alpha gs 1 0 0 0 k 365 215 70 50 re f Q
      q 70 0 0 45 365 145 cm /Im Do Q
      q 365 70 70 40 re W n 1 0 0 1 365 70 cm /Shade sh Q
      q 1 0 0 1 65 45 cm /Form Do Q
      0 1 0 0 k 175 45 55 30 re f
      1 0 0 0 k 198 58 55 30 re f
      .25 g 280 45 35 30 re f
      BT /F1 9 Tf 0 0 0 1 k 365 274 Td (Transparency) Tj 0 -75 Td (Image) Tj 0 -75 Td (Gradient) Tj ET
    '''
    page = writer.add_blank_page(width=500, height=330)
    page[NameObject('/Resources')] = resources; page[NameObject('/Contents')] = writer._add_object(stream(content))
    writer.root_object[NameObject('/OCProperties')] = dictionary(OCGs=ArrayObject([foil_ref, cut_ref]), D=dictionary(Order=ArrayObject([foil_ref, cut_ref])))
    # Distinct, rotated second page exercises ownership, mapping and page dimensions.
    page = writer.add_blank_page(width=200, height=100)
    page[NameObject('/Resources')] = dictionary()
    page[NameObject('/Contents')] = writer._add_object(stream('0 .5 1 0 k 20 10 80 40 re f'))
    page.rotate(90)
    writer.write(path)


class GeometryTests(unittest.TestCase):
    def test_rect_dimensions_exclude_outline_and_keep_fill_stroke_separate(self):
        result = read('.1 .2 .3 .4 k 0 1 0 RG 8 w 10 20 100 60 re B')
        obj = result['objects'][0]
        self.assertFalse(result['limited']); self.assertEqual(obj['bounds'], [10, 120, 110, 180])
        self.assertEqual(obj['fill'], {'kind':'CMYK', 'values':[.1, .2, .3, .4]})
        self.assertEqual(obj['stroke'], {'kind':'RGB', 'values':[0, 1, 0]}); self.assertEqual(obj['lineWidthPt'], 8)

    def test_bezier_bounds_use_curve_extrema_not_control_points(self):
        obj = read('0 0 m 0 100 100 100 100 0 c S')['objects'][0]
        self.assertEqual(obj['bounds'], [0, 125, 100, 200])

    def test_graphics_stack_and_nested_transforms(self):
        result = read('1 0 0 0 k q 2 0 0 2 10 20 cm 0 1 0 0 k q 1 0 0 1 5 10 cm 0 0 10 20 re f Q Q 0 0 10 20 re f')
        self.assertEqual(result['objects'][0]['bounds'], [20, 120, 40, 160])
        self.assertEqual(result['objects'][1]['bounds'], [0, 180, 10, 200])
        self.assertEqual(result['objects'][1]['fill']['values'], [1, 0, 0, 0])

    def test_rotation_nonzero_origin_and_user_units(self):
        expected = {0:[0, 40, 40, 80], 90:[0, 0, 40, 40], 180:[160, 0, 200, 40], 270:[40, 160, 80, 200]}
        for angle in expected:
            result = read('10 20 20 20 re f', rotation=angle, box=[10, 20, 110, 60], unit=2)
            self.assertEqual(result['objects'][0]['bounds'], expected[angle])
            self.assertEqual([result['widthPt'], result['heightPt']], [80, 200] if angle in (90, 270) else [200, 80])

    def test_form_matrix_resources_and_parent_colour(self):
        form = stream('0 0 10 20 re f', Type=NameObject('/XObject'), Subtype=NameObject('/Form'),
                      BBox=array(0, 0, 10, 20), Matrix=array(2, 0, 0, 2, 5, 6))
        result = read('1 0 0 0 k q 1 0 0 1 10 20 cm /F Do Q', dictionary(XObject=dictionary(F=form)))
        self.assertFalse(result['limited']); self.assertEqual(result['objects'][0]['bounds'], [15, 134, 35, 174])
        self.assertEqual(result['objects'][0]['fill']['values'], [1, 0, 0, 0])

    def test_clipping_warns_for_partial_path_and_skips_fully_hidden_shapes(self):
        result = read('0 0 30 30 re W n 10 10 50 50 re f 100 100 10 10 re f')
        self.assertEqual(len(result['objects']), 1)
        self.assertTrue(result['objects'][0]['geometryWarning'])
        self.assertEqual(result['objects'][0]['bounds'], [10, 140, 60, 190])

    def test_disjoint_stroked_outlines_are_individually_selectable(self):
        result = read('10 10 40 20 re 100 10 40 20 re S')
        self.assertEqual(len(result['objects']), 2)

    def test_nonuniform_scaling_does_not_claim_one_outline_thickness(self):
        self.assertIsNone(read('2 0 0 1 0 0 cm 10 10 40 20 re S')['objects'][0]['lineWidthPt'])
        self.assertIsNone(read('1 0 .6 .8 0 0 cm 10 10 40 20 re S')['objects'][0]['lineWidthPt'])


class ColorTests(unittest.TestCase):
    def test_native_spot_names_tints_and_devicen_are_not_converted(self):
        resources = dictionary(ColorSpace=dictionary(
            Foil=ArrayObject([NameObject('/Separation'), NameObject('/Gold Foil'), NameObject('/DeviceCMYK'), dictionary()]),
            Mix=ArrayObject([NameObject('/DeviceN'), ArrayObject([NameObject('/White'), NameObject('/Silver')]), NameObject('/DeviceCMYK'), dictionary()])))
        result = read('/Foil CS .75 SCN 0 0 20 20 re S /Mix cs .2 1 scn 30 0 20 20 re f', resources)
        self.assertEqual(result['objects'][0]['stroke'], {'kind':'Spot', 'inks':[{'name':'Gold Foil', 'tint':.75}]})
        self.assertEqual(result['objects'][1]['fill']['inks'], [{'name':'White','tint':.2}, {'name':'Silver','tint':1}])

    def test_transparency_pattern_image_and_gradient_are_unavailable(self):
        resources = dictionary(ExtGState=dictionary(A=dictionary(ca=FloatObject(.5))),
            ColorSpace=dictionary(P=ArrayObject([NameObject('/Pattern')])),
            XObject=dictionary(Im=stream(b'\xff\x00\x00', Subtype=NameObject('/Image'))),
            Shading=dictionary(G=dictionary(BBox=array(50, 50, 70, 70))))
        result = read('q /A gs 1 0 0 0 k 0 0 20 20 re f Q /P cs /PatternName scn 30 0 20 20 re f q 20 0 0 10 50 30 cm /Im Do Q /G sh', resources)
        self.assertFalse(result['limited']); self.assertEqual(len(result['objects']), 4)
        for obj in result['objects']:
            self.assertEqual(obj['fill']['kind'], 'unavailable'); self.assertTrue(obj['colorWarning'])
        self.assertEqual(result['objects'][2]['bounds'], [50, 160, 70, 170])

    def test_q_restores_solid_colour_after_transparency(self):
        result = read('q /A gs 0 0 20 20 re f Q 30 0 20 20 re f', dictionary(ExtGState=dictionary(A=dictionary(ca=FloatObject(.5)))))
        self.assertFalse(result['objects'][1]['colorWarning'])

    def test_opaque_shapes_inside_a_transparency_group_keep_original_inks(self):
        form = stream('0 .3 1 0 k 0 0 20 20 re f', Subtype=NameObject('/Form'),
                      BBox=array(0, 0, 20, 20), Group=dictionary(S=NameObject('/Transparency')))
        result = read('/F Do', dictionary(XObject=dictionary(F=form)))
        self.assertEqual(result['objects'][0]['fill']['kind'], 'CMYK')
        self.assertFalse(result['objects'][0]['colorWarning'])

    def test_group_opacity_still_warns_if_child_resets_its_own_alpha(self):
        form = stream('/Reset gs 0 .3 1 0 k 0 0 20 20 re f', Subtype=NameObject('/Form'),
                      BBox=array(0, 0, 20, 20), Group=dictionary(S=NameObject('/Transparency')),
                      Resources=dictionary(ExtGState=dictionary(Reset=dictionary(ca=NumberObject(1)))))
        result = read('/A gs /F Do', dictionary(XObject=dictionary(F=form), ExtGState=dictionary(A=dictionary(ca=FloatObject(.5)))))
        self.assertTrue(result['objects'][0]['colorWarning'])

    def test_default_icc_substitution_is_not_reported_as_plain_device_cmyk(self):
        result = read('1 0 0 0 k 0 0 20 20 re f', dictionary(ColorSpace=dictionary(DefaultCMYK=ArrayObject([NameObject('/ICCBased'), dictionary()]))))
        self.assertEqual(result['objects'][0]['fill']['kind'], 'unavailable')

    def test_unknown_color_is_explicit_without_losing_geometry(self):
        result = read('/Missing cs .5 scn 0 0 20 20 re f')
        self.assertEqual(result['objects'][0]['fill']['kind'], 'unavailable')
        self.assertEqual(result['objects'][0]['bounds'], [0, 180, 20, 200])


class TextTests(unittest.TestCase):
    @staticmethod
    def resources(**extra):
        font = dictionary(Type=NameObject('/Font'), Subtype=NameObject('/TrueType'),
            BaseFont=NameObject('/OwnedMetrics'), FirstChar=NumberObject(32),
            Widths=array(*([500]*95)),
            FontDescriptor=dictionary(Ascent=NumberObject(800), Descent=NumberObject(-200),
                                      FontBBox=array(-50, -200, 1100, 800)))
        return dictionary(Font=dictionary(F1=font), **extra)

    def test_multiline_text_object_keeps_metric_bounds_and_native_inks(self):
        result = read('0 .3 1 .2 k 0 1 0 RG 2 w BT /F1 20 Tf 2 Tr 25 TL '
                      '10 100 Td (AB) Tj T* (CD) Tj ET', self.resources())
        self.assertFalse(result['limited']); self.assertTrue(result['textPresent'])
        self.assertEqual(len(result['objects']), 1)
        obj = result['objects'][0]
        self.assertEqual(obj['type'], 'Text')
        self.assertEqual(obj['bounds'], [10, 84, 30, 129])
        self.assertEqual(obj['fill'], {'kind':'CMYK','values':[0,.3,1,.2]})
        self.assertEqual(obj['stroke'], {'kind':'RGB','values':[0,1,0]})
        self.assertEqual(obj['lineWidthPt'], 2)
        self.assertNotIn('font', obj); self.assertNotIn('text', obj)

    def test_spacing_scaling_rise_and_consecutive_show_advance(self):
        result = read('BT /F1 10 Tf 2 Tc 3 Tw 50 Tz 4 Ts 10 40 Td (A B) Tj (A) Tj ET', self.resources())
        self.assertFalse(result['limited'])
        self.assertEqual(result['objects'][0]['bounds'], [10, 148, 24.5, 158])

    def test_kerning_and_quote_operators_keep_one_text_box(self):
        result = read('BT /F1 20 Tf 25 TL 10 100 Td [(A) -200 (B)] TJ '
                      '(C) \' 3 2 (D E) " ET', self.resources())
        self.assertFalse(result['limited'])
        self.assertEqual(len(result['objects']), 1)
        self.assertEqual(result['objects'][0]['bounds'], [10, 84, 47, 154])

    def test_paint_changes_split_text_boxes_with_original_spot_fill_and_outline(self):
        resources = self.resources(ColorSpace=dictionary(Gold=ArrayObject([
            NameObject('/Separation'), NameObject('/Gold Foil'), NameObject('/DeviceCMYK'), dictionary()])))
        result = read('BT /F1 20 Tf 10 100 Td /Gold cs .8 scn (AB) Tj '
                      '1 Tr /Gold CS .5 SCN (CD) Tj ET', resources)
        self.assertFalse(result['limited']); self.assertEqual(len(result['objects']), 2)
        a, b = result['objects']
        self.assertEqual(a['fill']['inks'], [{'name':'Gold Foil','tint':.8}])
        self.assertEqual(a['stroke']['kind'], 'None')
        self.assertEqual(b['fill']['kind'], 'None')
        self.assertEqual(b['stroke']['inks'], [{'name':'Gold Foil','tint':.5}])

    def test_rotated_text_form_and_physical_page_transform(self):
        form = stream('BT /F1 20 Tf 0 1 -1 0 80 20 Tm (AB) Tj ET',
            Subtype=NameObject('/Form'), BBox=array(0,0,100,100), Resources=self.resources())
        result = read('1 0 0 0 k /F Do', dictionary(XObject=dictionary(F=form)),
                      rotation=90, box=[10,20,110,120], unit=2)
        self.assertFalse(result['limited'])
        self.assertEqual(result['objects'][0]['bounds'], [0,108,40,148])
        self.assertEqual(result['objects'][0]['fill']['values'], [1,0,0,0])

    def test_invisible_and_clip_only_text_are_not_selectable(self):
        for mode in (3, 7):
            result = read(f'BT /F1 20 Tf {mode} Tr 10 100 Td (AB) Tj ET '
                          '10 90 10 10 re f 200 20 10 10 re f', self.resources())
            self.assertFalse(result['limited'])
            self.assertTrue(all(o['type'] == 'Path' for o in result['objects']))
            self.assertEqual(len(result['objects']), 1 if mode == 7 else 2)
            if mode == 7: self.assertTrue(result['objects'][0]['geometryWarning'])

    def test_identity_cid_widths_without_unicode_and_hidden_layer_membership(self):
        font = dictionary(Type=NameObject('/Font'), Subtype=NameObject('/Type0'),
            BaseFont=NameObject('/OwnedCID'), Encoding=NameObject('/Identity-H'),
            DescendantFonts=ArrayObject([dictionary(Type=NameObject('/Font'), Subtype=NameObject('/CIDFontType2'),
                W=ArrayObject([NumberObject(1),array(600,700)]), DW=NumberObject(1000),
                FontDescriptor=dictionary(Ascent=NumberObject(800),Descent=NumberObject(-200)))]))
        resources = dictionary(Font=dictionary(F1=font), Properties=dictionary(
            A=dictionary(Type=NameObject('/OCG'), Name=TextStringObject('Text layer'))))
        result = read('/OC /A BDC BT /F1 20 Tf 10 100 Td <00010002> Tj ET EMC', resources)
        self.assertFalse(result['limited'])
        obj = result['objects'][0]
        self.assertEqual(obj['bounds'], [10,84,36,104])
        self.assertEqual(obj['layers'], [result['layers'][0]['id']])

    def test_q_restores_text_parameters_but_not_text_matrix(self):
        result = read('BT /F1 20 Tf 10 100 Td q 50 Tz (AB) Tj Q (CD) Tj ET', self.resources())
        self.assertFalse(result['limited'])
        self.assertEqual([o['bounds'] for o in result['objects']], [[10,84,20,104],[20,84,40,104]])

    def test_scattered_captions_do_not_create_a_text_box_over_intervening_artwork(self):
        result = read('BT /F1 10 Tf 10 150 Td (AB) Tj 0 -70 Td (CD) Tj '
                      '1 0 0 1 150 20 Tm (EF) Tj ET', self.resources())
        self.assertFalse(result['limited'])
        self.assertEqual([o['bounds'] for o in result['objects']],
                         [[10,42,20,52], [10,112,20,122], [150,172,160,182]])

    def test_unknown_font_does_not_discard_later_artwork_and_glyph_budget_is_bounded(self):
        result = read('BT /Missing 20 Tf 10 100 Td (AB) Tj ET 100 20 20 20 re f')
        self.assertTrue(result['limited']); self.assertEqual(len(result['objects']), 1)
        self.assertEqual(result['objects'][0]['type'], 'Path')
        with patch.object(pdf_inspect, 'MAX_TEXT_GLYPHS', 1):
            result = read('BT /F1 20 Tf 10 100 Td (AB) Tj ET', self.resources())
        self.assertTrue(result['limited']); self.assertEqual(result['objects'], [])


class DocumentTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'tmp'); self.addCleanup(self.temp.cleanup)
        self.pdf = Path(self.temp.name)/'owned.pdf'; fixture(self.pdf)

    def test_owned_label_size_spot_layers_and_rotated_page(self):
        result = inspect_pdf(self.pdf, 1)
        self.assertFalse(result['limited']); self.assertTrue(result['textPresent'])
        cut = next(o for o in result['objects'] if o['stroke']['kind'] == 'Spot')
        self.assertAlmostEqual((cut['bounds'][2]-cut['bounds'][0])*25.4/72, 100, places=5)
        self.assertAlmostEqual((cut['bounds'][3]-cut['bounds'][1])*25.4/72, 60, places=5)
        self.assertEqual(cut['stroke']['inks'][0]['name'], 'CutContour')
        self.assertEqual({l['name'] for l in result['layers']}, {'Dieline', 'Logo / foil'})
        rotated = inspect_pdf(self.pdf, 2)
        self.assertEqual(rotated['objects'][0]['bounds'], [10, 20, 50, 100])

    def test_budget_returns_explicit_partial_result(self):
        with patch.object(pdf_inspect, 'MAX_OBJECTS', 2):
            data = inspect_pdf(self.pdf, 1)
        self.assertEqual(len(data['objects']), 2); self.assertTrue(data['limited'])
        with patch.object(pdf_inspect, 'MAX_PACKET_BYTES', 1400):
            data = inspect_pdf(self.pdf, 1)
        self.assertLess(len(json.dumps(data, separators=(',', ':'))), 1400); self.assertTrue(data['limited'])

    def test_hidden_layers_keep_geometry_for_later_showing_and_declare_initial_visibility(self):
        reader = PdfReader(self.pdf)
        config = reader.root_object['/OCProperties']['/D']
        config[NameObject('/OFF')] = ArrayObject([reader.root_object['/OCProperties']['/OCGs'][0]])
        result = Inspector(reader, 1).inspect()
        foil = next(o for o in result['objects'] if o['fill']['kind'] == 'Spot')
        layer = next(l for l in result['layers'] if l['id'] == foil['layer'])
        self.assertFalse(layer['visible']); self.assertEqual(foil['layers'], [foil['layer']])

    def test_nested_layer_membership_requires_both_layers_to_be_visible(self):
        resources = dictionary(Properties=dictionary(
            A=dictionary(Type=NameObject('/OCG'), Name=TextStringObject('Outer')),
            B=dictionary(Type=NameObject('/OCG'), Name=TextStringObject('Inner'))))
        result = read('/OC /A BDC /OC /B BDC 0 0 20 20 re f EMC EMC 30 0 20 20 re f', resources)
        self.assertEqual(len(result['objects'][0]['layers']), 2)
        self.assertEqual(result['objects'][1]['layers'], [])


class ApiTests(unittest.TestCase):
    @unittest.skipUnless(GS, 'Ghostscript unavailable')
    def test_layer_visibility_also_hides_live_text_and_images(self):
        from lifetime import FileLifetime
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            root=Path(directory); session=root/('a'*32); files=FileLifetime(lambda:root,lambda:600)
            files.create(session);files.ready(session);files.release(session)
            writer=PdfWriter();page=writer.add_blank_page(width=144,height=144)
            group=writer._add_object(dictionary(Type=NameObject('/OCG'),Name=TextStringObject('Text and image')))
            image=writer._add_object(stream(bytes([0,100,200]),Subtype=NameObject('/Image'),Width=NumberObject(1),Height=NumberObject(1),BitsPerComponent=NumberObject(8),ColorSpace=NameObject('/DeviceRGB')))
            page[NameObject('/Resources')]=dictionary(Properties=dictionary(A=group),XObject=dictionary(Im=image),Font=dictionary(F1=dictionary(Type=NameObject('/Font'),Subtype=NameObject('/Type1'),BaseFont=NameObject('/Helvetica'))))
            page[NameObject('/Contents')]=writer._add_object(stream('/OC /A BDC q 50 0 0 50 10 10 cm /Im Do Q BT /F1 18 Tf 10 95 Td (Live text) Tj ET EMC'))
            writer.root_object[NameObject('/OCProperties')]=dictionary(OCGs=ArrayObject([group]),D=dictionary())
            source=session/'upload.pdf';writer.write(source)
            layer=inspect_pdf(source,1)['layers'][0]['id']
            with patch.object(backend,'GS_EXECUTABLE',GS),patch.object(backend,'TEMP_DIR',root),patch.object(backend,'files',files):
                def render(hidden):
                    data=backend._inspector_layer_job(backend.InspectorLayerRequest(id='a'*32,page=1,hiddenLayers=hidden),session,source)
                    return Image.open(io.BytesIO(base64.b64decode(data['image'].split(',')[1])))
                shown,hidden=render([]),render([layer])
                self.assertLess(shown.getextrema()[0][0],200)
                self.assertEqual(hidden.getextrema(),((255,255),(255,255),(255,255)))

    @unittest.skipUnless(GS, 'Ghostscript unavailable')
    def test_layer_render_reveals_underlying_artwork_and_preserves_original(self):
        from lifetime import FileLifetime
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            root = Path(directory); session = root/('a'*32)
            files = FileLifetime(lambda:root, lambda:600)
            files.create(session); files.ready(session); files.release(session)
            source = session/'upload.pdf'; fixture(source); original = source.read_bytes()
            foil = next(l['id'] for l in inspect_pdf(source,1)['layers'] if l['name'] == 'Logo / foil')
            with patch.object(backend, 'GS_EXECUTABLE', GS), patch.object(backend, 'TEMP_DIR', root), patch.object(backend, 'files', files):
                shown = backend._inspector_layer_job(backend.InspectorLayerRequest(id='a'*32,page=1,hiddenLayers=[]), session, source)
                hidden = backend._inspector_layer_job(backend.InspectorLayerRequest(id='a'*32,page=1,hiddenLayers=[foil]), session, source)
                def pixel(data):
                    img = Image.open(io.BytesIO(base64.b64decode(data['image'].split(',')[1])))
                    return img.getpixel((int(img.width*157/500), int(img.height*130/330)))
                self.assertLess(pixel(shown)[2], 150)
                self.assertGreater(pixel(hidden)[2], 200)
                self.assertEqual(source.read_bytes(), original)
                self.assertFalse(list(session.glob('preview_*')))
                with self.assertRaises(backend.HTTPException):
                    backend._inspector_layer_job(backend.InspectorLayerRequest(id='a'*32,page=1,hiddenLayers=['999999999']), session, source)
                self.assertFalse(list(session.glob('preview_*')))

    def test_layer_endpoint_has_strict_body_page_and_source_guards(self):
        with patch.object(backend, '_inspector_layer_job') as job:
            for body, expected in ((b'x'*20000, 413),
                (json.dumps({'id':'a'*32,'page':1,'hiddenLayers':['../bad']}).encode(),422),
                (json.dumps({'id':'a'*32,'page':1,'hiddenLayers':['1']*129}).encode(),422),
                (json.dumps({'id':'a'*32,'page':True,'hiddenLayers':[]}).encode(),422),
                (json.dumps({'id':'a'*32,'page':1,'hiddenLayers':[]}).encode(),404)):
                status, _, headers = asyncio.run(call('/api/inspect/layers',body,JSON))
                self.assertEqual(status,expected); self.assertEqual(headers[b'cache-control'],b'no-store')
            job.assert_not_called()

    def test_success_quota_busy_and_expiry_release_source_leases(self):
        from lifetime import FileLifetime
        import workload
        (ROOT/'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            root = Path(directory)
            files = FileLifetime(lambda:root, lambda:600)
            session = root/('a'*32)
            files.create(session); files.ready(session); files.release(session)
            (session/'upload.pdf').write_bytes(b'%PDF-owned')
            rates, jobs = workload.RateLimiter(), workload.JobRunner()
            body = json.dumps({'id':'a'*32,'page':1}).encode()
            with patch.object(backend, 'TEMP_DIR', root), patch.object(backend, 'files', files), \
                    patch.object(workload, 'rates', rates), patch.object(workload, 'jobs', jobs), \
                    patch.object(workload, 'PREVIEW_RATE_MAX', 1), \
                    patch.object(backend, '_object_inspection_job', return_value={'objects':[]}) as job:
                self.assertEqual(asyncio.run(call('/api/inspect', body, JSON))[0], 200)
                self.assertFalse(files.active); self.assertEqual(jobs.active, 0)
                self.assertEqual(asyncio.run(call('/api/inspect', body, JSON))[0], 429)
                self.assertFalse(files.active); self.assertEqual(job.call_count, 1)
                rates.clients.clear(); jobs.active = 1
                self.assertEqual(asyncio.run(call('/api/inspect', body, JSON))[0], 503)
                self.assertFalse(files.active); self.assertEqual(job.call_count, 1)
                jobs.active = 0
                manifest = json.loads((session/'lifetime.json').read_text())
                manifest['expiresAt'] = manifest['uploadedAt']
                (session/'lifetime.json').write_text(json.dumps(manifest))
                self.assertEqual(asyncio.run(call('/api/inspect', body, JSON))[0], 410)
                self.assertFalse(files.active); self.assertEqual(job.call_count, 1)

    def test_body_page_source_and_private_response_guards(self):
        with patch.object(backend, '_object_inspection_job') as job:
            for body, expected in ((b'x'*20000, 413), (json.dumps({'id':'a'*32,'page':0}).encode(), 422),
                                   (json.dumps({'id':'a'*32,'page':True}).encode(), 422),
                                   (json.dumps({'id':'a'*32,'page':1}).encode(), 404)):
                status, _, headers = asyncio.run(call('/api/inspect', body, JSON))
                self.assertEqual(status, expected); self.assertEqual(headers[b'cache-control'], b'no-store')
            job.assert_not_called()

    @unittest.skipUnless(GS, 'Ghostscript unavailable')
    def test_real_worker_and_page_range(self):
        (ROOT/'tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            path = Path(directory)/'owned.pdf'; fixture(path)
            with patch.object(backend, 'GS_EXECUTABLE', GS):
                result = backend._object_inspection_job(backend.PreviewRequest(id='a'*32,page=1), path)
                self.assertTrue(result['objects']); self.assertFalse(result['limited'])
                with self.assertRaises(backend.HTTPException):
                    backend._object_inspection_job(backend.PreviewRequest(id='a'*32,page=3), path)

    @unittest.skipUnless(GS, 'Ghostscript unavailable')
    def test_dense_outlined_artwork_survives_parser_and_worker_transport(self):
        # Packaging often outlines every letter. Exercise all three former
        # limits: >1500 objects, >18000 commands and >190KB of JSON. A small
        # final object detects truncation of artwork painted late in the page.
        with tempfile.TemporaryDirectory(dir=ROOT/'tmp') as directory:
            path = Path(directory)/'dense.pdf'
            writer = PdfWriter(); page = writer.add_blank_page(width=500, height=500)
            shapes = []
            for i in range(2100):
                x, y = i % 50 * 8, i // 50 * 8
                shapes.append(f'{x} {y} m {x+2} {y} l {x+3} {y+1} l {x+4} {y+2} l '
                              f'{x+4} {y+3} l {x+3} {y+4} l {x+2} {y+4} l '
                              f'{x+1} {y+3} l {x} {y+2} l h f')
            shapes.append('0 1 0 0 k 470 470 12 15 re f')
            page[NameObject('/Contents')] = writer._add_object(stream('\n'.join(shapes)))
            writer.write(path)
            with patch.object(backend, 'GS_EXECUTABLE', GS):
                result = backend._object_inspection_job(backend.PreviewRequest(id='a'*32,page=1), path)
            self.assertFalse(result['limited'])
            self.assertEqual(len(result['objects']), 2101)
            self.assertGreater(len(json.dumps(result)), 190000)
            self.assertEqual(result['objects'][-1]['bounds'], [470, 15, 482, 30])
            self.assertEqual(result['objects'][-1]['fill']['values'], [0, 1, 0, 0])

    def test_structured_worker_output_keeps_stderr_and_stdout_bounded(self):
        import workload
        with patch.object(workload, 'MAX_DIAGNOSTIC_BYTES', 1024):
            result = workload.execute_processor([sys.executable, '-c', 'print("x"*2048)'], 5,
                                               stdout_limit=4096)
            self.assertEqual(len(result.stdout.strip()), 2048)
            for code in ('print("x"*8192)', 'import sys;sys.stderr.write("x"*2048)'):
                with self.assertRaises(workload.WorkloadError):
                    workload.execute_processor([sys.executable, '-c', code], 5, stdout_limit=4096)


if __name__ == '__main__': unittest.main()
