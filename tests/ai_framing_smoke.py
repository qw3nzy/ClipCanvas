"""Installed vision model follows a controlled object on synthetic QA footage."""
import json
from pathlib import Path
import sys
from integration_smoke import Client, command

manifest=json.loads(Path(sys.argv[1]).read_text('utf-8'))
client=Client(manifest['base'])
folder=Path(manifest['directory'])/'ai-framing'
folder.mkdir(exist_ok=True)
movie=folder/'object.mp4'
command(['-f','lavfi','-i','color=c=blue:s=640x360:r=24:d=4',
    '-f','lavfi','-i','color=c=blue:s=640x360:r=24:d=4',
    '-filter_complex','[0:v]drawbox=x=80:y=120:w=120:h=120:color=red:t=fill[a];[1:v]drawbox=x=440:y=120:w=120:h=120:color=red:t=fill[b];[a][b]concat=n=2:v=1:a=0[v]',
    '-map','[v]','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(movie)])
source=client.upload(movie,'video')
job=client.wait(client.request('/api/pro/framing',{'source_id':source['id'],'interval':2,
    'framing':{'mode':'subject','subject':'Центр красного квадрата на синем фоне.'}})['id'],timeout=600)
points=job['framing']['points']
assert len(points)>=2 and all(0<=p['x']<=1 and 0<=p['y']<=1 for p in points)
left=[p for p in points if p['time']<4]
right=[p for p in points if p['time']>=4]
assert left and right and all(p['x']<.4 for p in left) and all(p['x']>.6 for p in right),points
result={'model':client.request('/api/ai/status')['model'],'source_id':source['id'],'points':points,
    'issues':job.get('framing_issues',[]),'passed':['Real vision locates controlled object before/after change of position']}
(folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
print(json.dumps(result,ensure_ascii=False,indent=2))
