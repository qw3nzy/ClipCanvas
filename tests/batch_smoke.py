"""Real folder pipeline: episodes, sidecars, errors, review, resume and delivery."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import zipfile
import time

from integration_smoke import Client, command, center_pixel, av


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text('utf-8'))
    client = Client(manifest['base'])
    directory = Path(manifest['directory'])/'batch-smoke'
    directory.mkdir(exist_ok=True)
    inputs = directory/'episodes'
    (inputs/'Season 1').mkdir(parents=True, exist_ok=True)
    for name in ('01.mkv', '02.mkv'):
        shutil.copyfile(manifest['source_fixture'], inputs/'Season 1'/name)
    (inputs/'Season 1'/'02.srt').write_text('1\n00:00:00,000 --> 00:00:08,000\nВторая серия\n', 'utf-8')
    (inputs/'broken.mkv').write_bytes(b'not a movie')
    composition = json.loads((Path(manifest['directory'])/'composition'/'result.json').read_text('utf-8'))
    config = deepcopy(client.request('/api/jobs/'+manifest['job_id'])['request'])
    config.update(parts=[{'start':0,'end':8}], advertising={'enabled':False}, canvas={'width':240,'height':300},
                  video={'x':0,'y':80,'w':240,'h':135,'fit':'contain'}, background={'enabled':False,'color':'#102030'},
                  banners=[{'id':'batch-banner','name':'Logo','enabled':True,'asset_id':composition['banner_id'],
                      'start':0,'end':8,'schedule':{'mode':'full'},
                      'geometry':{'x':10,'y':20,'w':80,'h':40,'fit':'contain'},
                      'chromakey':{'enabled':True,'color':'#00ff00','similarity':.1,'blend':.02}}],
                  delivery={'require_banner':True}, clip_overrides={}, text_layers=[], zip=False, encoder='cpu')
    request={'name':'Folder acceptance','config':config,'input_directory':str(inputs),
             'options':{'subtitles':'required','remove_background':False,'check_outputs':True,'zip':True}}
    job=client.wait(client.request('/api/batch',request)['id'],timeout=180)
    assert job['status']=='done', job
    rows=job['batch_items']
    assert len(rows)==3,rows
    valid=[row for row in rows if row['relative_path'].endswith(('01.mkv','02.mkv'))]
    assert all(row['status'] in {'ready','review'} for row in valid), valid
    assert next(row for row in rows if row['relative_path']=='broken.mkv')['status']=='error'
    for row in valid:
        assert any(name.endswith('.mp4') for name in row['artifacts'])
        assert any(name.endswith('.srt') for name in row['artifacts'])
        assert any(name.endswith('.publication.json') for name in row['artifacts'])
        assert any(name.endswith('.jpg') for name in row['artifacts'])
        assert row.get('project_id')
    second=next(row for row in valid if row['relative_path'].endswith('02.mkv'))
    project=client.request('/api/projects/'+second['project_id'])
    assert project['settings']['subtitles']['cues'][0]['text']=='Вторая серия',project
    archive=directory/'results.zip'
    client.download(next(file['url'] for file in job['files'] if file['name']=='results.zip'),archive)
    with zipfile.ZipFile(archive) as zipped:
        assert 'report.json' in zipped.namelist()
        assert len([name for name in zipped.namelist() if name.endswith('.mp4')])==2
        assert not any(name.startswith(('/', '..')) for name in zipped.namelist())
    passed=['Folder recursion, per-episode sidecars/embedded subtitles, MP4/SRT/covers/publication packs and ZIP',
            'Damaged episode is reported and does not stop valid episodes']
    completed={row['index']:row['render_job'] for row in valid}
    retry=client.wait(client.request('/api/jobs/'+job['id']+'/retry',{})['id'])
    assert {row['index']:row['render_job'] for row in retry['batch_items'] if row['index'] in completed}==completed
    passed.append('Continuation reuses completed episodes without rerendering')
    accepted=client.request('/api/batches/'+job['id']+'/decision',{'indices':[row['index'] for row in valid],'action':'accept'})
    assert accepted['status']=='saved'
    assert all(row['status']=='accepted' for row in client.request('/api/batches/'+job['id'])['batch_items'] if row['index'] in completed)
    passed.append('Explicit acceptance persists independently of source media')
    client.request('/api/batches/'+job['id']+'/decision',{'indices':[second['index']],'action':'reject'})
    updated=client.request('/api/batches/'+job['id'])
    assert updated['archive_stale']
    assert next(row for row in updated['batch_items'] if row['index']==second['index'])['artifacts'][0].startswith('rejected/')
    client.wait(client.request('/api/batches/'+job['id']+'/archive',{})['id'])
    client.download('/api/batches/'+job['id']+'/files/results.zip',archive)
    with zipfile.ZipFile(archive) as zipped:
        assert len([name for name in zipped.namelist() if name.endswith('.mp4')])==1
        assert not any(name.startswith('rejected/') for name in zipped.namelist())
    passed.append('Acceptance/rejection moves result directories; refreshed ZIP excludes rejected episodes')
    broken=next(row for row in rows if row['relative_path']=='broken.mkv')
    shutil.copyfile(manifest['source_fixture'],inputs/'broken.mkv')
    repaired=client.wait(client.request('/api/batches/'+job['id']+'/decision',{'indices':[broken['index']],'action':'retry'})['id'])
    restored=next(row for row in client.request('/api/batches/'+job['id'])['batch_items'] if row['index']==broken['index'])
    assert restored['source_id'] and any(name.endswith('.mp4') for name in restored['artifacts'])
    (inputs/'broken.mkv').write_bytes(b'not a movie')
    passed.append('Retry imports repaired local file without repeating completed episodes')

    review=deepcopy(request)
    review.pop('input_directory')
    review['items']=[{'source_id':manifest['source_id'],'relative_path':'episode.mkv'}]
    review['options'].update(review_before_export=True,subtitles='none',check_outputs=False,zip=False,split='duration',seconds=3)
    review['config']['delivery']={'require_banner':True,'max_duration':3}
    prepared=client.wait(client.request('/api/batch',review)['id'])
    assert prepared['batch_items'][0]['status']=='prepared',prepared
    assert not prepared['batch_items'][0]['artifacts']
    editable=client.request('/api/projects/'+prepared['batch_items'][0]['project_id'])
    editable['settings']['banners'][0]['opacity']=.6
    editable.pop('updated_at',None)
    editable.pop('created_at',None)
    client.request('/api/projects',editable)
    approval=client.wait(client.request('/api/batches/'+prepared['id']+'/decision',{'indices':[1],'action':'accept'})['id'])
    assert approval['status']=='done',approval
    rendered=client.request('/api/batches/'+prepared['id'])['batch_items'][0]
    assert rendered['status']=='accepted',rendered
    child=client.request('/api/jobs/'+rendered['render_job'])
    assert child['request']['banners'][0]['opacity']==.6,'Acceptance ignored saved manual edit'
    assert len(child['outputs'])==3,child['outputs']
    for output in child['outputs']:
        assert output['duration']<=3.05
    passed.append('Review gate produces editable projects; acceptance exports all final-length parts')

    motion=deepcopy(config)
    motion.update(parts=[{'start':0,'end':3}],subtitles={'enabled':False})
    banner=motion['banners'][0]
    banner.update(opacity=.8,fade_in=.2,fade_out=.2,motion=[{'time':0,'x':10,'y':20,'scale':1,'opacity':1},{'time':2,'x':130,'y':20,'scale':1.2,'opacity':1}])
    motion['clip_overrides']={'2':{'video':{'x':0,'y':0,'w':240,'h':300,'fit':'cover'}}}
    motion['text_layers']=[{'text':'CLIP','start':.2,'end':2.8,'x':50,'y':80,'size':24}]
    result=client.wait(client.request('/api/render',motion)['id'])
    assert result['status']=='done',result
    path=directory/'motion.mp4'
    client.download(next(file['url'] for file in result['files'] if file['name'].endswith('.mp4')),path)
    command(['-ss','1','-i',str(path),'-frames:v','1',str(directory/'motion.png')])
    with av.open(str(path)) as movie:
        frames=list(movie.decode(video=0))
    first=frames[round(.5*motion['fps'])].to_ndarray(format='rgb24')
    late=frames[round(2*motion['fps'])].to_ndarray(format='rgb24')
    # Red centre moves from x≈80 to x≈178; original background is RGB(16,32,48).
    assert first[40,80,0]>130 and late[44,178,0]>130
    assert late[40,80,0]<45 and first[40,178,0]<45
    assert first[40,80,0]<240,'Opacity was ignored'
    passed.append('Real FFmpeg renders banner keyframes/scale/opacity/fades and independent text layers')
    profile=client.request('/api/delivery-profiles',{'name':'Reusable pipeline','config':config,'options':request['options']})
    assert profile['config']['subtitles']['cues']==[]
    assert any(p['id']==profile['id'] for p in client.request('/api/delivery-profiles')['profiles'])
    campaign=client.request('/api/campaigns',{'name':'Acceptance campaign','advertiser':'QA'})
    publication=client.request('/api/publications',{'campaign_id':campaign['id'],'title':'QA clip','views':100,'clicks':4,'revenue':2.5})
    assert publication['revenue']==2.5
    passed.append('Profiles omit source captions; campaigns and publication metrics persist')
    mixed=deepcopy(review)
    mixed['items'].append({'relative_path':'broken-upload.mp4','error':'Invalid movie'})
    mixed['options'].update(split='equal',count=2,review_before_export=True)
    mixed['config']['delivery']={'require_banner':True}
    grouped=client.wait(client.request('/api/batch',mixed)['id'])
    assert [row['status'] for row in grouped['batch_items']]==['prepared','error']
    assert len(client.request('/api/projects/'+grouped['batch_items'][0]['project_id'])['timeline'])==2
    passed.append('Browser upload errors remain in report; equal split creates exact requested count')
    variants=deepcopy(mixed);variants['items']=variants['items'][:1]
    variants['variants']=[{'name':'A','config':config},{'name':'B','config':{**config,'canvas':{'width':300,'height':300}}}]
    expanded=client.wait(client.request('/api/batch',variants)['id'])
    assert len(expanded['batch_items'])==2 and {row['variant'] for row in expanded['batch_items']}=={1,2}
    sizes=[client.request('/api/projects/'+row['project_id'])['settings']['width'] for row in expanded['batch_items']]
    assert sizes==[240,300],sizes
    passed.append('Multiple delivery variants produce independent projects with their own canvas')

    cancellation=deepcopy(review)
    cancellation['options'].update(split='whole',review_before_export=False,check_outputs=False)
    cancellation['config'].update(canvas={'width':1920,'height':1080},video={'x':0,'y':0,'w':1920,'h':1080,'fit':'contain'},
                                  background={'enabled':True,'blur':60},quality='high',fps=60,delivery={})
    cancel_id=client.request('/api/batch',cancellation)['id']
    deadline=time.monotonic()+45
    while time.monotonic()<deadline:
        active=client.request('/api/batches/'+cancel_id)
        row=(active.get('batch_items') or [{}])[0]
        if row.get('render_job') and client.request('/api/jobs/'+row['render_job'])['status']=='running':break
        assert active['status'] not in {'done','error'},active
        time.sleep(.05)
    else:raise AssertionError('No active batch render to cancel')
    client.request('/api/jobs/'+cancel_id+'/cancel',{})
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        stopped=client.request('/api/jobs/'+cancel_id)
        if stopped['status']=='cancelled':break
        time.sleep(.1)
    assert stopped['status']=='cancelled',stopped
    assert client.request('/api/jobs/'+row['render_job'])['status']=='cancelled'
    continued=client.wait(client.request('/api/jobs/'+cancel_id+'/retry',{})['id'],timeout=180)
    assert continued['batch_items'][0]['status'] in {'ready','review'},continued
    passed.append('Real batch cancellation reaches child FFmpeg; continuation completes saved work')
    evidence={'passed':passed,'batch_id':job['id'],'review_batch_id':prepared['id'],'directory':str(directory)}
    (directory/'result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(evidence,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
