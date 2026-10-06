"""Persisted package recovery and ownership of approval/archive descendants."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

from integration_smoke import Client
from restart_qa import restart


def main():
    path=Path(sys.argv[1]);manifest=json.loads(path.read_text('utf-8'))
    client=Client(manifest['base']);folder=Path(manifest['directory'])
    evidence=json.loads((folder/'batch-smoke/result.json').read_text('utf-8'))
    before=client.request('/api/batches/'+evidence['batch_id'])
    manifest=restart(path)
    after=client.request('/api/batches/'+before['id'])
    assert after['request']==before['request'] and after['batch_items']==before['batch_items']
    continued=client.wait(client.request('/api/jobs/'+before['id']+'/retry',{})['id'])
    assert [(r['status'],r.get('render_job')) for r in continued['batch_items']]==[(r['status'],r.get('render_job')) for r in before['batch_items']]
    passed=['Restart reloads package settings, stage IDs, results and acceptance; continuation preserves completed episodes']
    request=deepcopy(before['request']);request['input_directory']=None
    request['items']=[{'source_id':manifest['source_id'],'relative_path':'ownership.mkv'}]
    request['options'].update(review_before_export=True,subtitles='none',check_outputs=False,zip=False,split='whole')
    request['config'].update(canvas={'width':1920,'height':1080},video={'x':0,'y':0,'w':1920,'h':1080,'fit':'contain'},
                             background={'enabled':True,'blur':60},quality='high',fps=60,delivery={})
    prepared=client.wait(client.request('/api/batch',request)['id'])
    batch_id=prepared['id']
    approval_id=client.request('/api/batches/'+batch_id+'/decision',{'indices':[1],'action':'accept'})['id']
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        parent=client.request('/api/batches/'+batch_id)
        row=parent['batch_items'][0]
        child=client.request('/api/jobs/'+row['render_job']) if row.get('render_job') else {}
        if child.get('status')=='running' and child.get('progress',0)>0:break
        assert client.request('/api/jobs/'+approval_id)['status'] in {'queued','running'}
        time.sleep(.05)
    else:raise AssertionError('No running approval FFmpeg')
    for endpoint,method in [('/api/jobs/'+batch_id+'/retry','POST'),('/api/jobs/'+batch_id,'DELETE')]:
        try:client.request(endpoint,{} if method=='POST' else None,method=method)
        except AssertionError as error:assert 'HTTP 409' in str(error),error
        else:raise AssertionError('Concurrent mutation was allowed: '+endpoint)
    client.request('/api/jobs/'+batch_id+'/cancel',{})
    deadline=time.monotonic()+15
    while time.monotonic()<deadline:
        stopped=client.request('/api/jobs/'+approval_id)
        if stopped['status']=='cancelled':break
        time.sleep(.1)
    assert stopped['status']=='cancelled',stopped
    assert client.request('/api/jobs/'+row['render_job'])['status']=='cancelled'
    next_approval=client.wait(client.request('/api/batches/'+batch_id+'/decision',{'indices':[1],'action':'accept'})['id'],timeout=180)
    done=client.request('/api/batches/'+batch_id)
    assert done['batch_items'][0]['status']=='accepted',done['batch_items']
    assert client.request('/api/jobs/'+next_approval['id'])['parent_id']==batch_id
    passed.append('Cancelling parent reaches active approval FFmpeg; conflicts are rejected; reaccepting continues successfully')
    archive=client.wait(client.request('/api/batches/'+batch_id+'/archive',{})['id'])
    assert archive['parent_id']==batch_id
    row=client.request('/api/batches/'+batch_id)['batch_items'][0]
    project_id=row['project_id']
    owned={approval_id,next_approval['id'],archive['id'],row['render_job']}
    client.request('/api/jobs/'+batch_id,method='DELETE')
    assert not (folder/'data/batches'/batch_id).exists()
    assert all(not (folder/'data/jobs'/identity).exists() for identity in owned)
    assert client.request('/api/projects/'+project_id)['source_id']==manifest['source_id']
    assert client.request('/api/assets/'+manifest['source_id'])['id']==manifest['source_id']
    passed.append('Deleting package removes all approval/archive/render directories and preserves source and project')
    result={'passed':passed,'directory':str(folder),'deleted_test_batch':batch_id}
    (folder/'batch-restore-result.json').write_text(json.dumps(result,indent=2),'utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
