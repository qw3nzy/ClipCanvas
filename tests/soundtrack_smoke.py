"""Real PCM verification for replacement, mixing, looping, cuts and ads."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from integration_smoke import Client,command,tone_energy


def main():
    manifest=json.loads(Path(sys.argv[1]).read_text('utf-8'));client=Client(manifest['base'])
    folder=Path(manifest['directory'])/'soundtrack';folder.mkdir(exist_ok=True)
    source_file=folder/'source.mp4';music_file=folder/'two-tones.wav';banner_file=folder/'voice-banner.mp4'
    command(['-f','lavfi','-i','color=c=blue:s=240x300:r=24:d=6',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=6',
        '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(source_file)])
    command(['-f','lavfi','-i','sine=frequency=660:sample_rate=48000:duration=1',
        '-f','lavfi','-i','sine=frequency=990:sample_rate=48000:duration=1',
        '-filter_complex','[0:a][1:a]concat=n=2:v=0:a=1[a]','-map','[a]','-c:a','pcm_s16le',str(music_file)])
    command(['-f','lavfi','-i','color=c=white:s=80x40:r=24:d=1',
        '-f','lavfi','-i','sine=frequency=1760:sample_rate=48000:duration=1',
        '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(banner_file)])
    source=client.upload(source_file,'video');music=client.upload(music_file,'audio');banner=client.upload(banner_file,'banner')
    formats=[]
    for extension,codec in [('mp3','libmp3lame'),('flac','flac'),('m4a','aac')]:
        file=folder/('two-tones.'+extension);command(['-i',str(music_file),'-c:a',codec,str(file)])
        record=client.upload(file,'audio');assert record['media_type']=='audio' and record['audio']
        formats.append({'id':record['id'],'name':record['name']})
    proxy=folder/'browser.m4a';client.download('/api/audio/media/'+music['id'],proxy)
    assert tone_energy(proxy,.2,660)>1e8
    config={'source_id':source['id'],'audio_stream_index':source['audio_streams'][0]['index'],
        'canvas':{'width':240,'height':300},'video':{'x':0,'y':0,'w':240,'h':300,'fit':'stretch'},
        'background':{'enabled':False},'subtitles':{'enabled':False},'encoder':'cpu','fps':24,
        'parts':[{'start':0,'end':6}],'audio':{'mute_source':True,'limiter':False},
        'soundtrack':{'enabled':True,'asset_id':music['id'],'start':1,'offset':.25,'volume':1},
        'banners':[{'id':'voice','enabled':True,'asset_id':banner['id'],'start':0,'end':.5,'volume':1,
            'geometry':{'x':0,'y':0,'w':80,'h':40},'schedule':{'mode':'each','start':.4,'duration':.4},'loop':False}]}
    def render(name,payload,endpoint='/api/render'):
        job=client.wait(client.request(endpoint,payload)['id']);movie=folder/(name+'.mp4')
        file=next(f for f in job['files'] if f['name'].endswith('.mp4') and not f.get('internal'))
        client.download(file['url'],movie);return movie,job
    replace,job=render('replace',config)
    assert tone_energy(replace,.45,1760)>1e8
    assert tone_energy(replace,1.1,660)>1e8 and tone_energy(replace,2.1,990)>1e8
    assert tone_energy(replace,1.1,440)<tone_energy(replace,1.1,660)*.0001
    pcm=command(['-ss','3.5','-i',str(replace),'-t','0.25','-f','s16le','-ac','1','-ar','16000','pipe:1'])
    assert not any(pcm),'Non-looping song should leave silence after it ends'
    payload=deepcopy(config);payload['audio']['mute_source']=False
    mixed,_=render('mixed',payload);assert tone_energy(mixed,1.1,440)>1e8 and tone_energy(mixed,1.1,660)>1e8
    payload=deepcopy(config);payload['soundtrack']['volume']=.5
    quiet,_=render('quiet',payload);ratio=tone_energy(quiet,1.1,660)/tone_energy(replace,1.1,660);assert .2<ratio<.3,ratio
    payload=deepcopy(config);payload['soundtrack']['loop']=True;payload['parts'].append({'start':0,'end':3})
    loop,loop_job=render('loop',payload)
    assert tone_energy(loop,3.1,660)>1e8 and tone_energy(loop,4.1,990)>1e8
    second=folder/'loop-second.mp4';client.download('/api/jobs/'+loop_job['id']+'/files/part-02.mp4',second)
    assert .8<tone_energy(second,1.1,660)/tone_energy(loop,1.1,660)<1.2
    sample,_=render('sample',config,'/api/sample')
    assert .8<tone_energy(sample,1.1,660)/tone_energy(replace,1.1,660)<1.2
    payload=deepcopy(config);payload['cuts']=[{'start':1,'end':2}];payload['banners']=[]
    payload['soundtrack'].update(start=.5,offset=0)
    payload['advertising']={'enabled':True,'asset_id':manifest['advert_id'],'profile':'custom','start':1,'repeat':False}
    paused,_=render('ad-pause',payload)
    assert tone_energy(paused,1.2,1320)>1e8
    assert tone_energy(paused,1.2,660)<tone_energy(paused,.6,660)*.0001
    assert tone_energy(paused,2.1,660)>1e8 and tone_energy(paused,2.7,990)>1e8
    payload['soundtrack'].update(start=0,pause_for_ads=False)
    over_ad,_=render('over-ad',payload)
    assert tone_energy(over_ad,1.2,1320)>1e8 and tone_energy(over_ad,1.2,990)>1e8
    result={'source_id':source['id'],'music_id':music['id'],'banner_id':banner['id'],'formats':formats,'job_id':job['id'],
        'volume_energy_ratio':ratio,'passed':['WAV, MP3, FLAC and M4A upload; actual browser audio proxy decodes',
        'Source replacement, original/music mix and 50% music volume verified with real PCM; voiced banner remains audible',
        'Start, file offset, end silence, loop and independent restart per clip; sample/full audio match',
        'Cuts do not skip music; default ad pause resumes same file position; optional music during ads mixes correctly']}
    (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
