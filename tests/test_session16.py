import asyncio
import html
import json
import unittest
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
import app as backend
from localization import catalog, LANG_PATHS, HTML_LANGS, SITE

ROOT = Path(__file__).resolve().parents[1]


class Tags(HTMLParser):
    def __init__(self, source):
        super().__init__(); self.tags=[]; self.feed(source)
    def handle_starttag(self, tag, attrs):
        self.tags.append((tag,dict(attrs)))


async def get(route, method='GET'):
    sent=[]
    async def receive(): return {'type':'http.request','body':b'','more_body':False}
    async def send(message): sent.append(message)
    scope=dict(type='http',asgi={'version':'3.0'},http_version='1.1',method=method,scheme='http',path=route,
               raw_path=route.encode(),query_string=b'',root_path='',headers=[],client=('127.0.0.1',1),server=('127.0.0.1',8001))
    await backend.app(scope,receive,send)
    start=next(item for item in sent if item['type']=='http.response.start')
    return start, b''.join(item.get('body',b'') for item in sent if item['type']=='http.response.body').decode()


class Session16(unittest.TestCase):
    def test_language_catalog_and_initial_welcome_match(self):
        source=(ROOT/'static/index.html').read_text(encoding='utf-8'); messages=catalog(source)
        for lang in LANG_PATHS:
            self.assertEqual(messages[lang].keys(),messages['en'].keys())
            self.assertTrue(all(isinstance(value,str) for value in messages[lang].values()))
            self.assertIn('page_count_one',messages[lang]);self.assertIn('page_count_other',messages[lang])
            rendered=backend._render_index_for_lang(lang)
            for key in ['tour_title_first','tour_title_last','tour_lead','tour_usecases','tour_size_title','tour_foil_title','welcome_upload','try_test_file']:
                self.assertIn(html.escape(messages[lang][key]),rendered.split('// ---- Translations ----')[0])
            self.assertEqual(catalog(rendered),messages)
            self.assertIn('<html lang="'+HTML_LANGS[lang]+'">',rendered)
            self.assertIn('<title>'+html.escape(messages[lang]['page_title'])+'</title>',rendered)
            self.assertIn('history.pushState',rendered)

    def test_all_public_routes_and_metadata_are_reciprocal(self):
        ns={'s':'http://www.sitemaps.org/schemas/sitemap/0.9','x':'http://www.w3.org/1999/xhtml'}
        sitemap=ET.parse(ROOT/'static/sitemap.xml'); entries=sitemap.findall('s:url',ns)
        self.assertEqual(len(entries),5 * len(LANG_PATHS)); found=[]
        for kind in ['', 'about','faq','contact','privacy']:
            paths={lang:(('' if lang=='en' else '/'+lang)+'/'+kind if kind else LANG_PATHS[lang]) for lang in LANG_PATHS}
            alternatives={'x-default':SITE+paths['en'], **{HTML_LANGS[lang]:SITE+path for lang,path in paths.items()}}
            for lang,path in paths.items():
                start,source=asyncio.run(get(path)); self.assertEqual(start['status'],200)
                self.assertNotIn(b'set-cookie',dict(start['headers']))
                tags=Tags(source).tags; canonical=[a['href'] for tag,a in tags if tag=='link' and a.get('rel')=='canonical']
                self.assertEqual(canonical,[SITE+path]); found.append(SITE+path)
                self.assertEqual({a['hreflang']:a['href'] for tag,a in tags if tag=='link' and a.get('rel')=='alternate'},alternatives)
                meta={a.get('property',a.get('name')):a['content'] for tag,a in tags if tag=='meta' and 'content' in a}
                self.assertEqual(meta['description'],meta['og:description']);self.assertEqual(meta['description'],meta['twitter:description'])
                self.assertEqual(meta['og:title'],meta['twitter:title']);self.assertEqual(meta['og:url'],SITE+path)
                self.assertEqual(meta['og:image'],SITE+'/static/og-image.png')
                for tag,a in tags:
                    if tag=='a' and 'lang-btn' in a.get('class',''):
                        self.assertIn(a['href'],paths.values());self.assertIn(a['hreflang'],HTML_LANGS.values())
                    if tag=='a' and a.get('href','').startswith('/') and 'lang-btn' not in a.get('class',''):
                        href=a['href'].split('#')[0]
                        if href in ['/','/es','/pt','/zh'] or any(href.endswith('/'+page) for page in ['about','faq','contact','privacy']):
                            self.assertEqual(href,LANG_PATHS[lang] if href in LANG_PATHS.values() else ('' if lang=='en' else '/'+lang)+'/'+href.rsplit('/',1)[-1])
                self.assertEqual(asyncio.run(get(path,'HEAD'))[0]['status'],200)
        self.assertEqual(set(found),{entry.find('s:loc',ns).text for entry in entries})
        for entry in entries:
            url=entry.find('s:loc',ns).text; route=url.removeprefix(SITE); source=asyncio.run(get(route))[1]
            self.assertEqual({link.attrib['hreflang']:link.attrib['href'] for link in entry.findall('x:link',ns)}, {a['hreflang']:a['href'] for tag,a in Tags(source).tags if tag=='link' and a.get('rel')=='alternate'})


if __name__=='__main__':unittest.main()
