"""Integrate reviewed DE/FR/UK copy and reciprocal navigation for seven languages."""
import html
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from localization import LANG_PATHS, HTML_LANGS, LOCALES, SITE, catalog

STATIC = ROOT / 'static'
COPY = ROOT / 'scripts/languages'
VERSION = '20261016-seven-languages'
NAMES = dict(en='English', es='Español', pt='Português', zh='简体中文', de='Deutsch', fr='Français', uk='Українська')
source = (STATIC / 'index.html').read_text(encoding='utf-8')
messages = catalog(source)
for lang in ('de', 'fr', 'uk'):
    m = json.loads((COPY / (lang + '.json')).read_text(encoding='utf-8'))
    assert m.keys() == messages['en'].keys(), lang
    for key, value in m.items():
        assert sorted(re.findall(r'\{\w+\}', value)) == sorted(re.findall(r'\{\w+\}', messages['en'][key])), (lang, key)
    messages[lang] = m
source = re.sub(r'const translations = (\{.*?\n\});', lambda _: 'const translations = ' + json.dumps(messages, ensure_ascii=False, indent=2) + ';', source, count=1, flags=re.S)

def esc(value):
    return html.escape(value, quote=True)

def route(lang, page=''):
    prefix = '' if lang == 'en' else '/' + lang
    return prefix + '/' + page if page else prefix or '/'

def language_links(lang, page='', dynamic=False):
    links = []
    for code in LANG_PATHS:
        active = code == lang
        data = f' data-lang="{code}"' if dynamic else ''
        links.append(f'<a href="{route(code, page)}" class="lang-btn' + (' active' if active else '')
                     + f'"{data} lang="{HTML_LANGS[code]}" hreflang="{HTML_LANGS[code]}"'
                     + f' aria-label="{NAMES[code]}" title="{NAMES[code]}" aria-current="' + ('page' if active else 'false') + f'">{code.upper()}</a>')
    return ''.join(links)

source = re.sub(r'(<nav class="lang-switcher"[^>]*>).*?(</nav>)', lambda m: m[1] + language_links('en', dynamic=True) + m[2], source, count=1, flags=re.S)
old_paths = "{ en: '/', es: '/es', pt: '/pt', zh: '/zh' }"
new_paths = json.dumps(LANG_PATHS)
source = source.replace(old_paths, new_paths)
source = source.replace("{en:'en_US',es:'es_ES',pt:'pt_BR',zh:'zh_CN'}", json.dumps(LOCALES))
source, changed = re.subn(r'  const langHrefs = \{.*?;\n(?=  document.querySelectorAll)', lambda _: '  const langHrefs = ' + json.dumps({page: {lang: route(lang, page) for lang in LANG_PATHS} for page in ('about','faq','contact','privacy')}) + ';\n', source, count=1, flags=re.S)
assert changed == 1, 'Missing language link map'
source = source.replace('(es|pt|zh)', '(es|pt|zh|de|fr|uk)')
# The limits fetch may refresh marked text after a crop was drawn.
source = source.replace("document.getElementById('cropSectionLabel').textContent = t(selected ? 'section_crop' : 'export_page_size');",
                        "const label = document.getElementById('cropSectionLabel');\n  label.dataset.i18n = selected ? 'section_crop' : 'export_page_size';\n  label.textContent = t(label.dataset.i18n);")
(STATIC / 'index.html').write_text(source, encoding='utf-8')

providers = ', '.join(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{name}</a>' for name,url in [
    ('Cloudflare','https://www.cloudflare.com/privacypolicy/'),('Hetzner','https://www.hetzner.com/legal/privacy-policy/'),
    ('Web3Forms','https://web3forms.com/privacy'),('Ko-fi','https://more.ko-fi.com/privacy')]) + '.'

def plain(value):
    return html.unescape(re.sub(r'<[^>]*>', '', value))

for lang in ('de','fr','uk'):
    c = json.loads((COPY / ('pages-' + lang + '.json')).read_text(encoding='utf-8'))
    m = messages[lang]
    def expand(value):
        value = value.replace('@retention', c['retention']).replace('@providers', providers)
        return re.sub(r'@(\w+)', lambda match: esc(m[match[1]]), value)
    for page in ('about','faq','contact','privacy'):
        s = (STATIC / (page + '.html')).read_text(encoding='utf-8')
        title, desc = c[page + '_title'] + ' — InkCoverage.app', c[page + '_desc']
        s = re.sub(r'<title>.*?</title>', lambda _: '<title>' + esc(title) + '</title>', s, count=1)
        s = re.sub(r'<meta name="description" content="[^"]*">', lambda _: '<meta name="description" content="' + esc(desc) + '">', s, count=1)
        for marker, value in [('og:title',title),('twitter:title',title),('og:description',desc),('twitter:description',desc),('og:locale',LOCALES[lang])]:
            s = re.sub(r'(<meta (?:property|name)="' + marker + r'" content=")[^"]*', lambda match: match[1] + esc(value), s, count=1)
        if page in ('about','privacy'):
            body = '<h2>' + esc(c[page + '_title']) + '</h2>'
            if page == 'privacy': body += '<p class="last-updated">' + esc(c['updated']) + '</p>'
            for heading, paragraph in c[page]:
                body += ('<h3>' + esc(heading) + '</h3>' if heading else '') + '<p>' + expand(paragraph) + '</p>\n'
        elif page == 'faq':
            body = '<h2>' + esc(c['faq_title']) + '</h2><p class="intro">' + esc(c['faq_intro']) + '</p>\n'
            pairs = [(q, expand(a)) for q,a in c['faq']]
            assert len(pairs) == 17
            body += '\n'.join('<div class="faq-item"><h3 class="faq-question">' + esc(q) + '</h3><div class="faq-answer">' + a + '</div></div>' for q,a in pairs)
            data = {'@context':'https://schema.org','@type':'FAQPage','inLanguage':lang,'mainEntity':[{'@type':'Question','name':q,'acceptedAnswer':{'@type':'Answer','text':plain(a)}} for q,a in pairs]}
            s = re.sub(r'<script type="application/ld\+json">.*?</script>', lambda _: '<script type="application/ld+json">' + json.dumps(data,ensure_ascii=False).replace('<','\\u003c') + '</script>', s, count=1, flags=re.S)
        else:
            body = re.search(r'<main[^>]*>(.*?)</main>', s, re.S)[1]
            body = re.sub(r'<h2>.*?</h2>', lambda _: '<h2>' + esc(c['contact_heading']) + '</h2>', body, count=1)
            body = re.sub(r'<p class="lead">.*?</p>', lambda _: '<p class="lead">' + c['contact_lead'] + '</p>', body, count=1, flags=re.S)
            body = body.replace('Your message', esc(c['message'])).replace('Email <span', esc(c['email']) + ' <span')
            body = re.sub(r'(<span class="form-optional">).*?(</span>)', lambda match: match[1] + esc(c['optional']) + match[2], body)
            body = re.sub(r'(<textarea[^>]*placeholder=")[^"]*', lambda match: match[1] + esc(c['placeholder']), body)
            body = re.sub(r'<p class="contact-privacy"[^>]*>.*?</p>', lambda _: '<p class="contact-privacy" id="contactPrivacy">' + esc(c['contact_privacy']) + f' <a href="/{lang}/privacy">' + esc(c['privacy_title']) + '</a></p>', body, flags=re.S)
            body = body.replace('Send message</button>', esc(c['send']) + '</button>')
            for old, key in [("'Sending…'",'sending'),('"Message sent — thank you! We\'ll be in touch if you left an email."','sent'),("'Something went wrong. Please try again in a moment.'",'send_failed'),("'Send message'",'send'),("'Submission failed'",'send_failed')]:
                s = s.replace(old, json.dumps(c[key], ensure_ascii=False))
        s = re.sub(r'<main[^>]*>.*?</main>', lambda _: '<main class="content" id="main" tabindex="-1">\n' + body + '\n</main>', s, count=1, flags=re.S)
        s = re.sub(r'(<a class="skip-link"[^>]*>).*?(</a>)', lambda match: match[1] + esc(m['skip_to_tool']) + match[2], s)
        # Header/footer follow the same complete catalog as the analyzer.
        s = s.replace('>Ink Coverage Analyzer</span>', '>' + esc(m['header_tagline']) + '</span>')
        s = s.replace('>Analyzer</a>', '>' + esc(c['analyzer']) + '</a>')
        s = s.replace('aria-label="Language"', 'aria-label="' + esc(m['language_label']) + '"')
        s = s.replace('aria-label="Site navigation"', 'aria-label="' + esc(m['footer_navigation']) + '"')
        for old,key in [('About','nav_about'),('FAQ','nav_faq'),('Contact','nav_contact'),('Privacy','nav_privacy')]:
            s = s.replace('>' + old + '</a>', '>' + esc(m[key]) + '</a>')
        s = s.replace('Free, open-source ink coverage analyzer.', esc(m['footer_text']))
        for destination in ('','about','faq','contact','privacy'):
            old = '/' + destination
            s = s.replace('href="' + old + '"', 'href="' + route(lang, destination) + '"')
        (STATIC / (page + '-' + lang + '.html')).write_text(s, encoding='utf-8')

# Existing translations keep their approved copy and receive the new links.
for lang in LANG_PATHS:
    for page in ('about','faq','contact','privacy'):
        path = STATIC / (page + ('' if lang == 'en' else '-' + lang) + '.html')
        s = path.read_text(encoding='utf-8')
        s = re.sub(r'<html lang="[^"]*">', f'<html lang="{HTML_LANGS[lang]}">', s, count=1)
        s = re.sub(r'(<nav class="lang-switcher"[^>]*>).*?(</nav>)', lambda match: match[1] + language_links(lang,page) + match[2], s, count=1, flags=re.S)
        s = re.sub(r'<link rel="alternate"[^>]*>\s*', '', s)
        alternates = '\n'.join(f'<link rel="alternate" hreflang="{tag}" href="{SITE + route(code,page)}">' for tag,code in [('x-default','en'), *[(HTML_LANGS[code],code) for code in LANG_PATHS]])
        s = s.replace('</head>', alternates + '\n</head>', 1)
        s = re.sub(r'(<link rel="canonical" href=")[^"]*', lambda match: match[1] + SITE + route(lang,page), s, count=1)
        s = re.sub(r'(<meta property="og:url" content=")[^"]*', lambda match: match[1] + SITE + route(lang,page), s, count=1)
        s = re.sub(r'(/static/(?:site-chrome\.css|site-navigation\.js))(?:\?v=[^"\s]+)?', lambda match: match[1] + '?v=' + VERSION, s)
        path.write_text(s, encoding='utf-8')
source = (STATIC / 'index.html').read_text(encoding='utf-8')
source = re.sub(r'(/static/(?:site-chrome\.css|site-navigation\.js|coverage-report\.js))(?:\?v=[^"\s]+)?', lambda match: match[1] + '?v=' + VERSION, source)
(STATIC / 'index.html').write_text(source, encoding='utf-8')
ns, xn = 'http://www.sitemaps.org/schemas/sitemap/0.9', 'http://www.w3.org/1999/xhtml'
ET.register_namespace('',ns); ET.register_namespace('xhtml',xn)
root = ET.Element(f'{{{ns}}}urlset')
for page in ('','about','faq','contact','privacy'):
    for lang in LANG_PATHS:
        node = ET.SubElement(root, f'{{{ns}}}url')
        ET.SubElement(node, f'{{{ns}}}loc').text = SITE + route(lang,page)
        ET.SubElement(node, f'{{{ns}}}lastmod').text = '2026-10-16'
        for tag,code in [('x-default','en'), *[(HTML_LANGS[code],code) for code in LANG_PATHS]]:
            ET.SubElement(node, f'{{{xn}}}link', rel='alternate', hreflang=tag, href=SITE + route(code,page))
ET.indent(root)
ET.ElementTree(root).write(STATIC / 'sitemap.xml', encoding='utf-8', xml_declaration=True)
for path in STATIC.glob('*.html'):
    if path.name.startswith(('index', 'about', 'faq', 'contact', 'privacy')):
        source = path.read_text(encoding='utf-8')
        if 'data-site="inkcoverage"' not in source:
            source = source.replace('</head>', '<link rel="stylesheet" href="/static/analytics.css?v=20261021"><script defer src="/static/analytics.js?v=20261021" data-site="inkcoverage"></script></head>')
            path.write_text(source, encoding='utf-8')
print('Integrated seven full analyzer catalogs and 35 public routes.')
