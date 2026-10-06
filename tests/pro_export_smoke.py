"""Real pixels and audio for framing, per-clip overrides, volume and ducking."""
from copy import deepcopy
import json
from pathlib import Path
import sys

from integration_smoke import Client, command, center_pixel, tone_energy


def main():
    manifest=json.loads(Path(sys.argv[1]).read_text('utf-8'))
    client=Client(manifest['base'])
    folder=Path(manifest['directory'])/'pro-export'
    folder.mkdir(exist_ok=True)
    fixture=folder/'halves.mp4'
    command(['-f','lavfi','-i','color=c=blue:s=640x360:r=24:d=4',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4',
        '-vf','drawbox=x=0:y=0:w=320:h=360:color=red:t=fill',
        '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(fixture)])
    source=client.upload(fixture,'video')
    config={'source_id':source['id'],'canvas':{'width':240,'height':300},
        'video':{'x':0,'y':0,'w':240,'h':300,'fit':'cover'},'background':{'enabled':False,'color':'#102030'},
        'parts':[{'start':0,'end':3},{'start':0,'end':3}], 'encoder':'cpu','fps':24,'zip':False,
        'framing':{'mode':'subject','points':[{'time':0,'x':.1,'y':.5},{'time':2,'x':.9,'y':.5}]},
        'subtitles':{'enabled':False,'cues':[{'start':0,'end':3,'text':'Test'}]},
        'clip_overrides':{'2':{'video':{'x':0,'y':100,'w':240,'h':135,'fit':'contain'},'subtitles_enabled':True,'subtitles':{'size':20,'y':85}}}}
    rendered=client.wait(client.request('/api/render',config)['id'])
    movies=[]
    for index,output in enumerate(rendered['outputs']):
        movie=folder/f'framing-{index+1}.mp4';client.download('/api/jobs/'+rendered['id']+'/files/'+output['name'],movie);movies.append(movie)
    early,late=center_pixel(movies[0],.15),center_pixel(movies[0],2.5)
    assert early[0]>220 and early[2]<30,(early,late)
    assert late[2]>220 and late[0]<30,(early,late)
    pixel=command(['-ss','1','-i',str(movies[1]),'-frames:v','1','-vf','crop=2:2:10:10','-pix_fmt','rgb24','-f','rawvideo','pipe:1'])[:3]
    assert max(abs(a-b) for a,b in zip(pixel,(16,32,48)))<8,tuple(pixel)
    assert any(f['name']=='part-02.srt' for f in rendered['files']) and not any(f['name']=='part-01.srt' for f in rendered['files'])
    passed=['Moving crop follows manual focus points in decoded MP4 pixels',
            'Second clip uses its own video geometry and enables its own SRT independently']

    def export_audio(audio,banners=None):
        payload=deepcopy(config);payload.update(parts=[{'start':0,'end':3}],clip_overrides={},subtitles={'enabled':False},audio=audio,banners=banners or [])
        job=client.wait(client.request('/api/render',payload)['id'])
        target=folder/(job['id']+'.mp4');client.download('/api/jobs/'+job['id']+'/files/part-01.mp4',target);return target
    quiet=export_audio({'source_volume':.5,'limiter':False})
    ratio=tone_energy(quiet,1,440)/tone_energy(movies[0],1,440)
    assert .20<ratio<.30,ratio
    faded=export_audio({'fade':1,'limiter':False})
    assert tone_energy(faded,.05,440)/tone_energy(faded,1.2,440)<.15
    banner=client.upload(Path(manifest['advert_fixture']),'banner')
    banner_config=[{'id':'sound','name':'Sound','enabled':True,'asset_id':banner['id'],'timebase':'output',
        'start':0,'end':3,'schedule':{'mode':'full'},'volume':1,'loop':True,
        'geometry':{'x':10,'y':10,'w':60,'h':40}}]
    ducked=export_audio({'ducking':True,'duck_volume':.25,'limiter':False},banner_config)
    ratio=tone_energy(ducked,1,440)/tone_energy(movies[0],1,440)
    assert .04<ratio<.09,ratio
    assert tone_energy(ducked,1,1320)>tone_energy(movies[0],1,1320)*100
    passed.append('Source volume, master fade, sounding banner mix and source ducking checked with actual PCM')
    normalized=export_audio({'normalize':True})
    assert tone_energy(normalized,1,440)>tone_energy(movies[0],1,440)*1.1
    passed.append('Whole-clip loudness normalization preserves audio/video and changes measured level')
    invalid=deepcopy(config);invalid.update(delivery={'require_banner':True})
    try:client.request('/api/render',invalid)
    except AssertionError as error:assert 'HTTP 400' in str(error)
    else:raise AssertionError('Mandatory banner was ignored')
    passed.append('Manual export refuses missing mandatory banner before rendering')
    result={'passed':passed,'source_id':source['id'],'directory':str(folder)}
    (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
