"""Create owned live-text artwork for the inspector's browser checks."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests')]
from test_pdf_inspect import array, dictionary, stream
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, NameObject, NumberObject, TextStringObject

writer = PdfWriter()
page = writer.add_blank_page(width=500, height=330)
layer = writer._add_object(dictionary(Type=NameObject('/OCG'), Name=TextStringObject('Live text')))
gold = ArrayObject([NameObject('/Separation'), NameObject('/Gold Foil'), NameObject('/DeviceCMYK'),
    dictionary(FunctionType=NumberObject(2), Domain=array(0,1), C0=array(0,0,0,0), C1=array(0,.3,1,.1), N=NumberObject(1))])
page[NameObject('/Resources')] = dictionary(
    Font=dictionary(F1=dictionary(Type=NameObject('/Font'), Subtype=NameObject('/Type1'), BaseFont=NameObject('/Helvetica')),
                    F2=dictionary(Type=NameObject('/Font'), Subtype=NameObject('/Type1'), BaseFont=NameObject('/Helvetica-Bold'))),
    ColorSpace=dictionary(Gold=gold), Properties=dictionary(Text=layer))
page[NameObject('/Contents')] = writer._add_object(stream('''
    .96 g 20 20 460 290 re f
    /OC /Text BDC
    0 .7 1 0 k BT /F1 20 Tf 28 TL 60 260 Td (Live text box) Tj T* (CMYK fill) Tj ET
    /Gold CS .85 SCN .8 w BT /F2 30 Tf 1 Tr 60 140 Td (FOIL OUTLINE) Tj ET
    .25 g BT /F1 18 Tf 0 Tr 0 1 -1 0 430 60 Tm (Rotated text) Tj ET
    0 g BT /F1 12 Tf 3 Tr 60 50 Td (Invisible search text) Tj ET
    EMC
'''))
writer.root_object[NameObject('/OCProperties')] = dictionary(OCGs=ArrayObject([layer]), D=dictionary())
target = ROOT/'tmp/inspect-pdf/live-text-sample.pdf'
target.parent.mkdir(parents=True, exist_ok=True)
writer.write(target)
print(target)
