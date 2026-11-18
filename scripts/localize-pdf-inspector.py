"""Integrate the reviewed inspector with the current site's shared locale catalog."""
from pathlib import Path
import json
import re
ROOT=Path(__file__).resolve().parents[1]
data=json.loads((ROOT/'scripts/languages/inspector.json').read_text(encoding='utf-8'))
path=ROOT/'static/index.html'; source=path.read_text(encoding='utf-8')
match=re.search(r'const translations = (\{.*?\n\});',source,re.S)
catalog=json.loads(match[1])
for key,values in data['ui'].items():
    assert len(values)==len(data['languages']),key
    for lang,value in zip(data['languages'],values):catalog[lang]['inspect_'+key]=value
source=source[:match.start(1)]+json.dumps(catalog,ensure_ascii=False,indent=2)+source[match.end(1):]
if 'id="btnInspectPdf"' not in source:
    button='          <button type="button" id="btnInspectPdf" aria-haspopup="dialog" aria-controls="pdfInspectorDialog" aria-expanded="false" disabled><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 3h16v18H4zM8 7h8M8 11h4"/><circle cx="14" cy="15" r="3"/><path d="m16.2 17.2 3 3"/></svg><span data-i18n="inspect_title">Inspect PDF</span></button>'
    source=source.replace('          <span class="sr-only" id="previewMoveHint"></span>',button+'\n          <span class="sr-only" id="previewMoveHint"></span>')
    source=source.replace('<script src="/static/welcome-tour.js?v=20261015-preview-pages"></script>', '<script src="/static/welcome-tour.js?v=20261015-preview-pages"></script>\n<script src="/static/pdf-inspector.js?v=20261118-release"></script>')
    source=source.replace('<link rel="stylesheet" href="/static/site-chrome.css?v=20261016-seven-languages">','<link rel="stylesheet" href="/static/site-chrome.css?v=20261016-seven-languages">\n<link rel="stylesheet" href="/static/pdf-inspector.css?v=20261118-release">')
    source=source.replace('  window.channelPreview?.sync();\n  window.coverageReport?.sync();','  window.channelPreview?.sync();\n  window.coverageReport?.sync();\n  window.pdfInspector?.sync();')
source=source.replace(' disabled lang="en"><svg',' disabled><svg').replace('<span>Inspect PDF</span></button>','<span data-i18n="inspect_title">Inspect PDF</span></button>')
if 'data-i18n="inspect_help_title"' not in source:
    marker='      <div class="help-tip">'
    help_step='<div class="help-step"><div class="help-step-num">9</div><div class="help-step-content"><h3 data-i18n="inspect_help_title">'+catalog['en']['inspect_help_title']+'</h3><p data-i18n="inspect_help_body">'+catalog['en']['inspect_help_body']+'</p></div></div>\n'
    assert marker in source;source=source.replace(marker,help_step+marker,1)
path.write_text(source,encoding='utf-8')
print('Integrated',len(data['ui']),'inspector messages in',len(catalog),'languages.')
