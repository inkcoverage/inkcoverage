"""Update matching visible FAQ/schema text and related localized support content."""
from pathlib import Path
from html import escape,unescape
import json,re
ROOT=Path(__file__).resolve().parents[1]
copy=json.loads((ROOT/'scripts/languages/inspector-support.json').read_text(encoding='utf-8'))
for lang,values in copy.items():
    suffix='' if lang=='en' else '-'+lang
    faq=ROOT/f'static/faq{suffix}.html';source=faq.read_text(encoding='utf-8')
    pattern=r'<div class="faq-item"><h3 class="faq-question">(.*?)</h3><div class="faq-answer">(.*?)</div></div>'
    pairs=list(re.finditer(pattern,source,re.S));assert len(pairs) in (17,19),(lang,len(pairs))
    replacements={4:(values['measure_q'],values['measure_a'])}
    for index,(question,answer) in replacements.items():
        old=pairs[index][0];new=f'<div class="faq-item"><h3 class="faq-question">{escape(question)}</h3><div class="faq-answer">{escape(answer)}</div></div>'
        source=source.replace(old,new,1)
    if len(pairs)==17:
        additions=''.join(f'<div class="faq-item"><h3 class="faq-question">{escape(values[k+"_q"])}</h3><div class="faq-answer">{escape(values[k+"_a"])}</div></div>\n' for k in ('inks','layers'))
        source=source.replace('</main>',additions+'</main>',1)
        quota=pairs[11][0];source=source.replace(quota,quota.replace('</div></div>',' '+escape(values['quota'])+'</div></div>'),1)
    schema_match=re.search(r'<script type="application/ld\+json">(.*?)</script>',source,re.S)
    schema=json.loads(schema_match[1]);visible=re.findall(pattern,source,re.S)
    schema['mainEntity']=[{'@type':'Question','name':unescape(q),'acceptedAnswer':{'@type':'Answer','text':unescape(re.sub(r'<[^>]*>','',a))}} for q,a in visible]
    source=source[:schema_match.start(1)]+'\n'+json.dumps(schema,ensure_ascii=False,indent=2)+'\n'+source[schema_match.end(1):]
    faq.write_text(source,encoding='utf-8')
    about=ROOT/f'static/about{suffix}.html';source=about.read_text(encoding='utf-8')
    if 'id="pdf-inspector"' not in source:
        source=source.replace('</main>',f'<section id="pdf-inspector"><h3>{escape(values["about_title"])}</h3><p>{escape(values["about_body"])}</p></section>\n</main>',1)
    about.write_text(source,encoding='utf-8')
    privacy=ROOT/f'static/privacy{suffix}.html';source=privacy.read_text(encoding='utf-8')
    if 'class="inspector-privacy"' not in source:
        first=re.search(r'<h3>1\..*?</h3><p>.*?</p>',source,re.S)
        assert first,lang
        end=first.end()
        source=source[:end]+f'<p class="inspector-privacy">{escape(values["privacy"])}</p>'+source[end:]
    source=re.sub(r'(<p class="last-updated">).*?(</p>)',lambda m:m[1]+values['date']+m[2],source,count=1)
    privacy.write_text(source,encoding='utf-8')
print('Updated FAQ/schema, About and Privacy in',len(copy),'languages.')
