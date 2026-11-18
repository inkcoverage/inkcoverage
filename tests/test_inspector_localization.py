"""Inspector locale parity, support page/schema agreement and exact ink names."""
import json,re,unittest
from pathlib import Path
from html import unescape
from localization import catalog
ROOT=Path(__file__).resolve().parents[1]

class InspectorLocales(unittest.TestCase):
    def test_ui_uses_shared_catalog_for_every_locale(self):
        messages=catalog((ROOT/'static/index.html').read_text(encoding='utf-8'))
        data=json.loads((ROOT/'scripts/languages/inspector.json').read_text(encoding='utf-8'))
        for key,values in data['ui'].items():
            self.assertEqual(len(values),7)
            for lang,value in zip(data['languages'],values):self.assertEqual(messages[lang]['inspect_'+key],value)
        source=(ROOT/'static/pdf-inspector.js').read_text(encoding='utf-8')
        for key in re.findall(r"(?:tr|message)\('([a-z_]+)'",source):
            if key=='type_':continue
            self.assertIn('inspect_'+key,messages['en'])

    def test_all_support_languages_match_visible_faq_and_schema(self):
        data=json.loads((ROOT/'scripts/languages/inspector-support.json').read_text(encoding='utf-8'))
        for lang,copy in data.items():
            suffix='' if lang=='en' else '-'+lang
            source=(ROOT/f'static/faq{suffix}.html').read_text(encoding='utf-8')
            pairs=re.findall(r'<h3 class="faq-question">(.*?)</h3><div class="faq-answer">(.*?)</div>',source,re.S)
            schema=json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',source,re.S)[1])
            self.assertEqual(len(pairs),19);self.assertEqual(len(schema['mainEntity']),19)
            for (question,answer),item in zip(pairs,schema['mainEntity']):
                self.assertEqual(unescape(question),item['name'])
                self.assertEqual(unescape(re.sub(r'<[^>]*>','',answer)),item['acceptedAnswer']['text'])
            for key in ('measure_a','inks_a','layers_a','quota'):self.assertIn(copy[key],unescape(source))
            self.assertIn(copy['about_body'],unescape((ROOT/f'static/about{suffix}.html').read_text(encoding='utf-8')))
            self.assertIn(copy['privacy'],unescape((ROOT/f'static/privacy{suffix}.html').read_text(encoding='utf-8')))

if __name__=='__main__':unittest.main()
