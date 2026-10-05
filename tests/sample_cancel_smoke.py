"""Cancel a real running sample FFmpeg process, verify cleanup, then retry.

Pass an isolated integration_smoke --keep-server result.json. No user data or
ordinary application process is touched. Analysis caches are fingerprinted.
"""
from copy import deepcopy
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from integration_smoke import Client, ROOT, NO_WINDOW


def fingerprints(folder):
    return {str(path.relative_to(folder)):hashlib.sha256(path.read_bytes()).hexdigest()
            for path in folder.rglob('*') if path.is_file()}


def ffmpeg_children(pid):
    script = (f"Get-CimInstance Win32_Process -Filter 'ParentProcessId = {int(pid)}' | "
              "Where-Object { $_.Name -like '*ffmpeg*' } | Select-Object -ExpandProperty ProcessId | ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe','-NoProfile','-Command',script],capture_output=True,
                            text=True,timeout=15,creationflags=NO_WINDOW)
    if not result.stdout.strip():
        return []
    value = json.loads(result.stdout)
    return value if isinstance(value,list) else [value]


def alive(pid):
    kernel = ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000,False,int(pid))
    if not handle:
        return False
    try:
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p,ctypes.c_uint32]
        return kernel.WaitForSingleObject(handle,0) == 0x102
    finally:
        kernel.CloseHandle(handle)


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text('utf-8'))
    directory = Path(manifest['directory']).resolve()
    assert (ROOT/'.runtime'/'qa').resolve() in directory.parents, 'Requires isolated QA data'
    client = Client(manifest['base'])
    health = client.request('/api/health')
    assert health['pid'] == manifest['pid'] and health['instance_id'] == manifest['health']['instance_id']
    source = client.request('/api/assets/'+manifest['source_id'])
    analysis = client.wait(client.request('/api/subtitles/extract',{
        'source_id':source['id'], 'subtitle_stream_index':source['recommended_subtitle_stream_index']})['id'])
    analysis_before = deepcopy(analysis)
    cache_before = fingerprints(directory/'data'/'cache')
    jobs_before = {job['id'] for job in client.request('/api/jobs?summary=true')['jobs']}
    request = {'source_id':source['id'],'canvas':{'width':1920,'height':1080},
               'video':{'x':480,'y':270,'w':960,'h':540,'fit':'contain'},
               'background':{'enabled':True,'blur':50}, 'cuts':[{'start':3,'end':4}],
               'parts':[{'start':0,'end':8}], 'subtitles':{'enabled':True,'cues':analysis['cues']},
               'quality':'high','fps':60,'encoder':'cpu'}
    job_id = client.request('/api/sample',request)['id']
    job_folder = directory/'data'/'jobs'/job_id
    deadline = time.monotonic()+40
    while time.monotonic()<deadline:
        job = client.request('/api/jobs/'+job_id)
        if job['status']=='running' and job['progress']>0:
            pids = ffmpeg_children(health['pid'])
            if pids:
                break
        if job['status'] in {'done','error','cancelled'}:
            raise AssertionError('Sample must still be rendering when cancelled: '+str(job['status']))
        time.sleep(.05)
    else:
        raise AssertionError('No running FFmpeg with real encoded-frame progress')
    progress_at_cancel = job['progress']
    assert any(job_folder.glob('*.mp4')), 'FFmpeg has not created a media fragment'
    started = time.monotonic()
    client.request('/api/jobs/'+job_id+'/cancel',{})
    while time.monotonic()-started<12:
        job = client.request('/api/jobs/'+job_id)
        leftovers = [path.name for pattern in ('*.mp4','*.ass','*.freeze.png','concat-*.txt','*.zip.tmp')
                     for path in job_folder.glob(pattern)]
        if job['status']=='cancelled' and not any(alive(pid) for pid in pids) and not leftovers:
            break
        time.sleep(.05)
    else:
        raise AssertionError({'status':job['status'],'remaining_pids':[pid for pid in pids if alive(pid)],'leftovers':leftovers})
    assert not job['files'] and not job['outputs'], 'Cancelled fragments must not be advertised as completed outputs'
    cancel_seconds = time.monotonic()-started
    assert fingerprints(directory/'data'/'cache') == cache_before, 'Cancellation changed prior analysis cache'
    assert client.request('/api/jobs/'+analysis['id']) == analysis_before
    retried = client.request('/api/jobs/'+job_id+'/retry',{})
    assert retried['id'] == job_id
    completed = client.wait(job_id,timeout=180)
    assert completed['type']=='sample' and len(completed['outputs'])==1
    assert len(completed['request']['parts'])==1
    assert abs(completed['outputs'][0]['planned_duration']-7)<.001
    assert completed['outputs'][0]['frames'] == 420, '24→60 fps export lost final frames at the source end'
    assert abs(completed['outputs'][0]['fps']-60)<.001, 'Output frame rate is not constant 60 fps'
    assert fingerprints(directory/'data'/'cache') == cache_before
    jobs_after = {job['id'] for job in client.request('/api/jobs?summary=true')['jobs']}
    assert jobs_after-jobs_before == {job_id}, 'Sample/retry unexpectedly started analysis or other clip jobs'
    assert not list(job_folder.glob('segment-*.mp4')) and not list(job_folder.glob('*.ass'))
    report = {'passed':['Real running FFmpeg sample cancelled after encoded-frame progress',
        'Owned FFmpeg process exited; unfinished MP4/ASS/concat temporary files removed',
        'Prior transcript job and analysis caches unchanged',
        'Retry reuses the same job and renders exactly one selected clip through shared renderer',
        '24 fps source resampled to 60 fps retains all 420 frames, including the source tail'],
        'sample_job':job_id,'analysis_job':analysis['id'],'cancelled_processes':pids,
        'progress_at_cancel':progress_at_cancel,'cancel_seconds':cancel_seconds,
        'retry_output_duration':completed['outputs'][0]['duration']}
    (directory/'sample-cancel-result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
