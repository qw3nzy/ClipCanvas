"""Build the bundled HTML guide from the repository manual (no dependencies)."""
from pathlib import Path
import html
import re

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT/'docs/manual.md').read_text('utf-8')
def inline(text):
    return re.sub(r'`([^`]+)`', r'<code>\1</code>', html.escape(text))
body, toc, kind = [], [], ''
for line in source.splitlines():
    if not line.strip():
        if kind:
            body.append('</'+kind+'>')
            kind = ''
        continue
    if line.startswith('# '):
        body.append('<h1>'+inline(line[2:])+'</h1>')
    elif line.startswith('## '):
        key = 'section-'+str(len(toc)+1)
        toc.append('<li><a href="#'+key+'">'+inline(line[3:])+'</a></li>')
        body.append('<h2 id="'+key+'">'+inline(line[3:])+'</h2>')
    elif line.startswith('- ') or re.match(r'^\d+\. ', line):
        target = 'ul' if line.startswith('- ') else 'ol'
        if kind != target:
            if kind: body.append('</'+kind+'>')
            body.append('<'+target+'>')
            kind = target
        body.append('<li>'+inline(re.sub(r'^(?:- |\d+\. )','',line))+'</li>')
    else:
        if kind:
            body.append('</'+kind+'>')
            kind = ''
        body.append('<p>'+inline(line)+'</p>')
if kind: body.append('</'+kind+'>')
page='''<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Руководство ClipCanvas 3.0</title><link rel="icon" href="favicon.svg"><style>
:root{color-scheme:light;font:16px/1.65 system-ui,sans-serif;color:#24324a;background:#f3f5f9}body{margin:0}header{padding:16px max(20px,calc((100% - 960px)/2));background:#17233c;color:white}header a{color:white;text-decoration:none}main{max-width:960px;margin:30px auto;background:white;padding:36px;box-sizing:border-box;border-radius:14px}h1{font-size:32px;line-height:1.2}h2{margin-top:42px;scroll-margin-top:20px}p,li{overflow-wrap:anywhere}li{margin:8px 0}code{font:14px ui-monospace,monospace;background:#eef2f8;padding:2px 5px;border-radius:4px}a{color:#1759c7}nav{padding:14px 24px;background:#eef3fc;border-radius:12px}nav ol{padding-left:20px;columns:2}nav li{break-inside:avoid;margin:6px 0}@media(max-width:650px){main{margin:0;border-radius:0;padding:24px 18px}h1{font-size:27px}nav ol{columns:1}}@media print{header,nav{display:none}main{max-width:none;margin:0;padding:0}h2{break-after:avoid}}
</style></head><body><header><a href="./">← ClipCanvas</a></header><main>'''
page+=body[0]+'<nav aria-label="Содержание"><strong>Содержание</strong><ol>'+''.join(toc)+'</ol></nav>'+'\n'.join(body[1:])+'</main></body></html>\n'
(ROOT/'web/manual.html').write_text(page,'utf-8')
