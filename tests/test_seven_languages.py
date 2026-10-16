"""Translation completeness and agreement between visible pages and metadata."""
import json
import re
import unittest
from pathlib import Path
from html import unescape
from localization import catalog, LANG_PATHS

ROOT = Path(__file__).resolve().parents[1]


class SevenLanguages(unittest.TestCase):
    def test_catalog_placeholders_and_trusted_markup(self):
        messages = catalog((ROOT / 'static/index.html').read_text(encoding='utf-8'))
        self.assertEqual(set(messages), set(LANG_PATHS))
        for lang, values in messages.items():
            self.assertEqual(values.keys(), messages['en'].keys())
            for key, value in values.items():
                self.assertTrue(value.strip(), (lang, key))
                self.assertEqual(sorted(re.findall(r'\{\w+\}', value)),
                                 sorted(re.findall(r'\{\w+\}', messages['en'][key])), (lang, key))
                self.assertEqual(re.findall(r'</?\w+[^>]*>', value),
                                 re.findall(r'</?\w+[^>]*>', messages['en'][key]), (lang, key))

    def test_new_pages_are_complete_and_faq_schema_matches_visible_copy(self):
        for lang in ('de', 'fr', 'uk'):
            for page in ('about', 'faq', 'contact', 'privacy'):
                source = (ROOT / f'static/{page}-{lang}.html').read_text(encoding='utf-8')
                self.assertNotRegex(source, r'@(retention|providers|help_tip)')
                self.assertEqual(source.count('class="lang-btn'), 7)
                self.assertIn(f'<html lang="{lang}">', source)
                self.assertNotIn('Site navigation', source)
                self.assertNotIn('Free, open-source ink coverage analyzer.', source)
                if page == 'faq':
                    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', source, re.S)[1])
                    pairs = re.findall(r'<h3 class="faq-question">(.*?)</h3><div class="faq-answer">(.*?)</div>', source, re.S)
                    self.assertEqual(len(pairs), 17)
                    self.assertEqual(data['inLanguage'], lang)
                    for item, (question, answer) in zip(data['mainEntity'], pairs):
                        self.assertEqual(item['name'], unescape(question))
                        self.assertEqual(item['acceptedAnswer']['text'], unescape(re.sub(r'<[^>]*>', '', answer)))
                if page == 'contact':
                    for english in ('Sending…', 'Something went wrong.', 'Message sent — thank you!', 'Send message'):
                        self.assertNotIn(english, source)


if __name__ == '__main__':
    unittest.main()
