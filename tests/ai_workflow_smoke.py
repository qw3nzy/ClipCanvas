"""Real installed vision model, using synthetic QA media and explicit test captions."""
import json
from pathlib import Path
import sys
import time
import subprocess
from integration_smoke import Client, FFMPEG, NO_WINDOW

manifest = json.loads(Path(sys.argv[1]).read_text('utf-8'))
client = Client(manifest['base'])
folder = Path(manifest['directory'])

def wait(job):
    started = time.monotonic()
    while time.monotonic()-started < 600:
        result = client.request('/api/jobs/'+job['id'])
        if result['status'] == 'done':
            return result
        if result['status'] in {'error', 'cancelled'}:
            raise AssertionError(result.get('error', result['status']))
        time.sleep(.3)
    client.request('/api/jobs/'+job['id']+'/cancel', {})
    raise AssertionError('AI timeout')

status = client.request('/api/ai/start', {})
assert status['available'], status
start = time.monotonic()
positive = folder/'silent-intro-outro.mp4'
font = 'C\\:/Windows/Fonts/arial.ttf'
filters = (f"[0:v]drawtext=fontfile='{font}':text='CLIPCANVAS':fontsize=26:fontcolor=white:x=(w-tw)/2:y=(h-th)/2[a];"
           f"[2:v]drawtext=fontfile='{font}':text='END CREDITS':fontsize=24:fontcolor=white:x=(w-tw)/2:y=(h-th)/2[c];"
           '[a][1:v][c]concat=n=3:v=1:a=0[v]')
subprocess.run([str(FFMPEG), '-hide_banner','-loglevel','error','-y',
    '-f','lavfi','-i','color=c=black:s=256x144:r=24:d=4',
    '-f','lavfi','-i','testsrc2=s=256x144:r=24:d=16',
    '-f','lavfi','-i','color=c=black:s=256x144:r=24:d=4',
    '-filter_complex', filters,'-map','[v]','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(positive)],
    capture_output=True,check=True,creationflags=NO_WINDOW)
positive_asset = client.upload(positive,'video')
boundaries = wait(client.request('/api/ai/boundaries', {'source_id': positive_asset['id'], 'transcribe': False,
    'window_seconds': 10, 'auto_remove': True, 'cues': []}))
assert all(0 <= b['start'] < b['end'] <= 24 for b in boundaries['boundaries'])
assert {b['kind'] for b in boundaries['boundaries']} == {'intro','outro'}, boundaries['boundaries']
assert [(b['start'], b['end']) for b in boundaries['boundaries']] == [(0, 4), (20, 24)], boundaries['boundaries']
assert boundaries['model'] == status['model']
banner = client.upload(folder/'composition/key-alpha.png', 'banner')
layout = wait(client.request('/api/ai/banner-layout', {'source_id': manifest['source_id'], 'asset_id': banner['id'],
    'banner_id': 'qa-banner', 'width': 600, 'height': 600, 'start': 0, 'end': 8,
    'video': dict(x=0, y=0, w=600, h=600, fit='contain'),
    'original': dict(x=0, y=350, w=100, h=60, fit='contain'),
    'instructions': 'Баннер в верхней половине. Размер, изображение и время показа не менять.',
    'rules': {'movable': True, 'resizable': True, 'min_width': 0, 'min_height': 0, 'zone': 'top'},
    'protected_regions': [dict(x=0, y=450, w=600, h=150, fit='contain')]}))
assert layout['geometry'], layout
assert layout['geometry']['w'] == 100 and layout['geometry']['h'] == 60
assert layout['geometry']['y']+60 <= 300
assert layout['requirements']['lock_size'] and not layout['requirements']['lock_position']
assert layout['model'] == status['model'] and len(layout['frame_times']) == 6
result = {'model': status['model'], 'synthetic_media': True, 'seconds': round(time.monotonic()-start, 2),
          'boundaries': boundaries['boundaries'], 'layout': layout['geometry'], 'requirements': layout['requirements'],
          'passed': ['real vision finds silent logo intro and ending credits with validated source times',
                     'real banner inference finds placement and obeys client size/zone']}
(folder/'ai-workflow-result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
print(json.dumps(result, ensure_ascii=False, indent=2))
