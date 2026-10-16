"""Render the analyzer's shared, trusted translation catalog before JavaScript."""
import html
import json
import re
from html.parser import HTMLParser

LANG_PATHS = {"en": "/", "es": "/es", "pt": "/pt", "zh": "/zh", "de": "/de", "fr": "/fr", "uk": "/uk"}
HTML_LANGS = {lang: ("zh-Hans" if lang == "zh" else lang) for lang in LANG_PATHS}
LOCALES = dict(en="en_US", es="es_ES", pt="pt_BR", zh="zh_CN", de="de_DE", fr="fr_FR", uk="uk_UA")
SITE = "https://inkcoverage.app"


def catalog(source):
    match = re.search(r"const translations = (\{.*?\n\});", source, re.S)
    if not match:
        raise ValueError("Missing shared JSON translation catalog")
    return json.loads(match[1])


class TranslatedMarkup(HTMLParser):
    """Patch source positions so scripts, styles and unrelated markup stay intact."""
    VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}

    def __init__(self, source, lang, messages):
        super().__init__(convert_charrefs=False)
        self.source, self.lang, self.messages = source, lang, messages
        self.offsets = [0]
        for line in source.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.patches, self.stack = [], []

    def position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        start = self.position()
        raw = self.get_starttag_text()
        changes = {}
        for suffix, attribute in [("aria", "aria-label"), ("title", "title"), ("alt", "alt")]:
            key = attrs.get("data-i18n-" + suffix)
            if key in self.messages:
                changes[attribute] = self.messages[key]
        prefix = "" if self.lang == "en" else "/" + self.lang
        if "data-lang-href" in attrs:
            changes["href"] = prefix + "/" + attrs["data-lang-href"]
        classes = attrs.get("class", "").split()
        if "brand" in classes:
            changes["href"] = LANG_PATHS[self.lang]
        if "lang-btn" in classes:
            active = attrs["data-lang"] == self.lang
            changes["class"] = " ".join(c for c in classes if c != "active") + (" active" if active else "")
            changes["aria-current"] = "page" if active else "false"
        for attribute, value in changes.items():
            encoded = html.escape(value, quote=True)
            pattern = rf'\s{re.escape(attribute)}="[^"]*"'
            if re.search(pattern, raw):
                raw = re.sub(pattern, lambda _: f' {attribute}="{encoded}"', raw, count=1)
            else:
                raw = raw[:-1] + f' {attribute}="{encoded}">'
        if raw != self.get_starttag_text():
            self.patches.append((start, start + len(self.get_starttag_text()), raw))
        key = attrs.get("data-i18n") or attrs.get("data-i18n-html")
        translated = self.messages.get(key)
        if translated is not None and "data-i18n-html" not in attrs:
            translated = html.escape(translated)
        if tag not in self.VOID:
            self.stack.append((tag, start + len(self.get_starttag_text()), translated))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                _, start, translated = self.stack[index]
                del self.stack[index:]
                if translated is not None:
                    self.patches.append((start, self.position(), translated))
                break

    def result(self):
        self.feed(self.source)
        result = self.source
        for start, end, replacement in sorted(self.patches, reverse=True):
            result = result[:start] + replacement + result[end:]
        return result


def render_index(source, lang, max_upload_mb=50, max_pages=100):
    messages = catalog(source)[lang].copy()
    for key in ["upload_hint", "help_1_body"]:
        messages[key] = messages[key].replace("50", str(max_upload_mb))
    messages["help_1_body"] = messages["help_1_body"].replace("100", str(max_pages))
    messages["upload_limits"] = messages["upload_limits"].replace("{pages}", str(max_pages))
    if "tour_upload_facts" in messages:
        messages["tour_upload_facts"] = messages["tour_upload_facts"].replace("{max}", str(max_upload_mb)).replace("{pages}", str(max_pages))
    source = TranslatedMarkup(source, lang, messages).result()
    source = re.sub(r'<html lang="[^"]*">', f'<html lang="{HTML_LANGS[lang]}">', source, count=1)
    source = re.sub(r'<title>[^<]*</title>', lambda _: f'<title>{html.escape(messages["page_title"])}</title>', source, count=1)
    values = {"name=\"description\"": messages["meta_desc"], "property=\"og:title\"": messages["page_title"],
              "property=\"og:description\"": messages["meta_desc"], "property=\"og:url\"": SITE + LANG_PATHS[lang]}
    for attribute, value in values.items():
        source = re.sub(rf'<meta {attribute} content="[^"]*">', lambda _, a=attribute, v=value: f'<meta {a} content="{html.escape(v, quote=True)}">', source, count=1)
    source = re.sub(r'<link rel="canonical" href="[^"]*">', f'<link rel="canonical" href="{SITE + LANG_PATHS[lang]}">', source, count=1)
    alternates = {"x-default": "/", **{HTML_LANGS[key]: path for key, path in LANG_PATHS.items()}}
    inject = "\n".join(f'<link rel="alternate" hreflang="{key}" href="{SITE + path}">' for key, path in alternates.items())
    inject += f'\n<meta property="og:locale" content="{LOCALES[lang]}">'
    for key, value in [("twitter:title", messages["page_title"]), ("twitter:description", messages["meta_desc"])]:
        inject += f'\n<meta name="{key}" content="{html.escape(value, quote=True)}">'
    inject += f'\n<script>window.__LANG__="{lang}";</script>'
    source = source.replace("</head>", inject + "\n</head>", 1)
    def schema(match):
        data = json.loads(match[1])
        data.update(url=SITE + LANG_PATHS[lang], description=messages["meta_desc"], inLanguage=HTML_LANGS[lang])
        return '<script type="application/ld+json">\n' + json.dumps(data, ensure_ascii=False).replace("<", "\\u003c") + '\n</script>'
    return re.sub(r'<script type="application/ld\+json">(.*?)</script>', schema, source, flags=re.S)
