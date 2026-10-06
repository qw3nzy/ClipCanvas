"""Exact position API and optional real bounded model analysis on isolated media."""
import argparse
import json
from pathlib import Path
import time
from integration_smoke import Client


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('manifest')
    parser.add_argument('--ai',action='store_true')
    args=parser.parse_args()
    manifest=json.loads(Path(args.manifest).read_text('utf-8'))
    client=Client(manifest['base'])
    directory=Path(manifest['directory'])
    banner=client.upload(Path(manifest['advert_fixture']),'banner')
    request=dict(source_id=manifest['source_id'],asset_id=banner['id'],banner_id='confirmed-banner',
        width=600,height=600,start=0,end=8,video=dict(x=0,y=0,w=600,h=600,fit='contain'),
        original=dict(x=10,y=10,w=160,h=80,fit='contain'),instructions='Баннер по центру холста.',
        rules={'zone':'center'},protected_regions=[])
    def wait(created):
        deadline=time.monotonic()+600
        while time.monotonic()<deadline:
            job=client.request('/api/jobs/'+created['id'])
            if job['status']=='done':return job
            if job['status'] in {'error','cancelled'}:raise AssertionError(job.get('error',job['status']))
            time.sleep(.2)
        client.request('/api/jobs/'+created['id']+'/cancel',{})
        raise AssertionError('Model timeout')
    exact=wait(client.request('/api/ai/banner-layout',request))
    assert (exact['geometry']['x'],exact['geometry']['y'])==(220,260), exact
    assert exact['fixed_position'] and 'frame_times' not in exact
    result={'passed':['exact canvas centre without model analysis'],'exact_job':exact['id']}
    if args.ai:
        status=client.request('/api/ai/start',{})
        assert status['available'], status.get('error')
        started=time.monotonic()
        layout=wait(client.request('/api/ai/banner-layout',{**request,
            'instructions':'Баннер в верхней половине. Размер, изображение и время показа не менять.',
            'rules':{'zone':'top','resizable':True},'original':dict(x=10,y=400,w=100,h=40,fit='contain')}))
        assert layout['geometry'] and layout['geometry']['y']+40<=300,layout
        assert layout['geometry']['w']==100 and layout['geometry']['h']==40
        assert len(layout['frame_times'])==6 and all(size<=2 for size in layout['analysis_batch_sizes'])
        assert layout['requirements']['lock_size'] and not layout['requirements']['lock_position']
        result.update(model=status['model'],seconds=round(time.monotonic()-started,2),
                      batch_sizes=layout['analysis_batch_sizes'],layout_job=layout['id'],layout=layout['geometry'])
        result['passed'].append('real model reads six frames in bounded requests and preserves client size/zone')
    (directory/'banner-brief-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
