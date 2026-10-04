"""Compare full CPU/NVENC exports on an isolated integration_smoke QA server.

Run integration_smoke.py --keep-server first, then pass its result.json here.
All generated video and benchmark evidence remain under the QA directory.
"""
import argparse
import copy
import json
import statistics
import time
from pathlib import Path

from integration_smoke import Client, command


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--seconds', type=int, default=8)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if args.seconds < 1 or args.repeats < 1:
        parser.error('seconds and repeats must be positive')
    manifest = json.loads(args.manifest.read_text('utf-8'))
    directory = Path(manifest['directory'])
    if directory.parent.name != 'qa' or directory.parent.parent.name != '.runtime':
        raise RuntimeError('Use an isolated integration_smoke QA manifest.')
    client = Client(manifest['base'])
    if 'nvidia' not in client.request('/api/health')['capabilities']['render_encoders']:
        raise RuntimeError('NVIDIA encoding is unavailable on this QA server.')
    source_path = directory/'benchmark-source.mp4'
    command(['-f', 'lavfi', '-i', f'testsrc2=size=1280x720:rate=30:duration={args.seconds}',
             '-f', 'lavfi', '-i', f'sine=frequency=880:sample_rate=48000:duration={args.seconds}',
             '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '20', '-c:a', 'aac', str(source_path)])
    asset = client.upload(source_path, 'video')
    payload = copy.deepcopy(client.request('/api/jobs/'+manifest['job_id'])['request'])
    payload.update(source_id=asset['id'],audio_stream_index=asset['recommended_audio_stream_index'],
        canvas={'width':1080,'height':1920},video={'x':0,'y':656,'w':1080,'h':608,'fit':'contain'},
        banner={'enabled':False},advertising={'enabled':False},parts=[{'start':0,'end':args.seconds}],
        fps=30,zip=False)
    payload['background'].update(enabled=True,blur=24)
    payload['subtitles'].update(size=52,y=75,
        cues=[{'start':0,'end':args.seconds,'text':'Проверка скорости рендера','words':[]}])
    samples = []
    for quality in ('standard', 'high'):
        for repeat in range(args.repeats):
            for encoder in (('cpu','nvidia') if repeat%2==0 else ('nvidia','cpu')):
                request = dict(payload,quality=quality,encoder=encoder)
                started = time.perf_counter()
                job_id = client.request('/api/render',request)['id']
                while True:
                    job = client.request('/api/jobs/'+job_id)
                    if job['status'] in {'done','error','cancelled'}:
                        break
                    if time.perf_counter()-started > 600:
                        raise TimeoutError('Benchmark render exceeded 10 minutes.')
                    time.sleep(.1)
                elapsed = time.perf_counter()-started
                assert job['status']=='done',job
                output = job['outputs'][0]
                assert output['encoder']==encoder,output
                assert abs(output['duration']-args.seconds)<.15,output
                assert abs(output['frames']-args.seconds*30)<=1,output
                assert output['width']==1080 and output['height']==1920,output
                sample = {'quality':quality,'encoder':encoder,'repeat':repeat+1,
                          'seconds':round(elapsed,3),'bytes':output['size'],'job_id':job_id}
                samples.append(sample)
                print(json.dumps(sample),flush=True)
    summary = {}
    for quality in ('standard', 'high'):
        timings = {encoder:statistics.median(x['seconds'] for x in samples
                   if x['encoder']==encoder and x['quality']==quality) for encoder in ('cpu','nvidia')}
        summary[quality] = {**timings,'speedup':round(timings['cpu']/timings['nvidia'],2)}
    result = {'fixture':'synthetic moving 720p source, 1080x1920 output, blur and Cyrillic subtitles',
              'source_seconds':args.seconds,'repeats':args.repeats,'summary':summary,'samples':samples}
    (directory/'benchmark-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
