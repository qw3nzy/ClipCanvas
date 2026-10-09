"""Compare cropped MP4 pixels, audio, focus, and independent advertising."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from integration_smoke import Client, command, tone_energy
from video_effects_smoke import pixel


def main():
    manifest=json.loads(Path(sys.argv[1]).read_text('utf-8'));client=Client(manifest['base'])
    folder=Path(manifest['directory'])/'video-crop';folder.mkdir(exist_ok=True)
    source_file=folder/'source.mp4'
    command(['-f','lavfi','-i','color=c=red:s=320x180:r=24:d=3',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=3',
        '-vf','drawbox=x=160:y=0:w=80:h=180:color=green:t=fill,drawbox=x=240:y=0:w=80:h=180:color=blue:t=fill,drawbox=x=0:y=0:w=320:h=45:color=white:t=fill,drawbox=x=0:y=135:w=320:h=45:color=yellow:t=fill',
        '-c:v','libx264','-preset','ultrafast','-crf','12','-c:a','aac','-shortest',str(source_file)])
    advert_file=folder/'advert.mp4'
    command(['-f','lavfi','-i','color=c=yellow:s=160x100:r=24:d=1',
        '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=1',
        '-vf','drawbox=x=80:y=0:w=80:h=100:color=cyan:t=fill','-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(advert_file)])
    banner_file=folder/'banner.png';command(['-f','lavfi','-i','color=c=white:s=24x24','-frames:v','1',str(banner_file)])
    source=client.upload(source_file,'video');advert=client.upload(advert_file,'advert');banner=client.upload(banner_file,'banner')
    config={'source_id':source['id'],'audio_stream_index':source['audio_streams'][0]['index'],
        'canvas':{'width':240,'height':300},'video':{'x':20,'y':50,'w':200,'h':120,'fit':'stretch'},
        'video_crop':{'enabled':True,'x':.5,'y':.25,'w':.5,'h':.5},
        'background':{'enabled':False,'color':'#111827'},'encoder':'cpu','fps':24,'parts':[{'start':0,'end':3}],
        'subtitles':{'enabled':False},'banners':[{'id':'white','enabled':True,'asset_id':banner['id'],'start':0,'end':3,
        'geometry':{'x':28,'y':70,'w':24,'h':24,'fit':'stretch'},'schedule':{'mode':'each','start':0,'duration':3}}]}
    outputs={}
    def render(name,payload,endpoint='/api/render'):
        job=client.wait(client.request(endpoint,payload)['id'])
        movie=folder/(name+'.mp4');file=next(f for f in job['files'] if f['name'].endswith('.mp4') and not f.get('internal'))
        client.download(file['url'],movie);return movie
    for fit in ('stretch','contain','cover'):
        payload=deepcopy(config);payload['video']['fit']=fit;movie=render(fit,payload)
        left,right=pixel(movie,.5,60,110),pixel(movie,.5,180,110)
        assert left[1]>80 and left[0]<30 and right[2]>200,(fit,left,right)
        assert min(pixel(movie,.5,36,80))>230
        assert tone_energy(movie,.5,440)>1e8
        outputs[fit]={'left':left,'right':right}
    sample=render('sample',config,'/api/sample')
    assert all(abs(a-b)<=4 for a,b in zip(pixel(sample,.5,60,110),outputs['stretch']['left']))
    payload=deepcopy(config);payload['video_crop']['enabled']=False;movie=render('disabled',payload)
    assert pixel(movie,.5,60,110)[0]>200
    payload=deepcopy(config);payload['video_effects']={'mirror':True};movie=render('mirror',payload)
    assert pixel(movie,.5,60,110)[2]>200 and pixel(movie,.5,180,110)[1]>80
    payload=deepcopy(config);payload['video']={'x':0,'y':0,'w':240,'h':300,'fit':'cover'}
    payload['banners']=[];payload['framing']={'mode':'center','x':.625,'y':.5}
    movie=render('focus',payload);center=pixel(movie,.5,120,150)
    assert center[1]>80 and center[2]<30,center
    payload=deepcopy(config);payload['background']={'enabled':True,'blur':8}
    payload['advertising']={'enabled':True,'asset_id':advert['id'],'profile':'custom','start':1,'repeat':False,
        'audio_stream_index':advert['audio_streams'][0]['index'],'geometry':{'x':60,'y':180,'w':120,'h':80,'fit':'stretch'}}
    movie=render('advert',payload)
    for t in (.5,1.5,2.5):
        left,right=pixel(movie,t,60,110),pixel(movie,t,180,110)
        assert left[1]>80 and left[0]<30 and right[2]>200,(t,left,right)
        assert pixel(movie,t,120,280)[0]<40
    left,right=pixel(movie,1.5,80,210),pixel(movie,1.5,160,210)
    assert left[0]>180 and left[1]>180 and left[2]<50,left
    assert right[0]<50 and right[1]>180 and right[2]>180,right
    assert tone_energy(movie,1.5,880)>tone_energy(movie,.5,880)*20
    result={'source_id':source['id'],'banner_id':banner['id'],'advert_id':advert['id'],'outputs':outputs,
        'passed':['Cropped pixels agree in stretch/contain/cover and sample/full MP4; source audio is retained',
                  'Reset keeps original pixels; mirror follows crop; original focus points map into the retained region',
                  'Blurred and frozen source frames are cropped; banner and advertising retain their own pixels and sound']}
    (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
