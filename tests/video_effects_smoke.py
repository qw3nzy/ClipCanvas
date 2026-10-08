"""Inspect real MP4 pixels, timing and audio using only synthetic fixtures."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from integration_smoke import Client, command, tone_energy


def pixel(movie, second, x, y):
    return list(command(['-ss',str(second),'-i',str(movie),'-frames:v','1',
        '-vf',f'crop=2:2:{x}:{y}','-pix_fmt','rgb24','-f','rawvideo','pipe:1'])[:3])


def main():
    manifest=json.loads(Path(sys.argv[1]).read_text('utf-8'));client=Client(manifest['base'])
    folder=Path(manifest['directory'])/'video-effects';folder.mkdir(exist_ok=True)
    source_file=folder/'asymmetric-source.mp4'
    command(['-f','lavfi','-i','color=c=red:s=320x180:r=24:d=3',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=3',
        '-vf','drawbox=x=160:y=0:w=160:h=180:color=blue:t=fill,drawbox=x=128:y=60:w=64:h=60:color=0x6080a0:t=fill',
        '-c:v','libx264','-preset','ultrafast','-crf','12','-c:a','aac','-shortest',str(source_file)])
    ad_file=folder/'asymmetric-advert.mp4'
    command(['-f','lavfi','-i','color=c=yellow:s=160x100:r=24:d=1',
        '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=1',
        '-vf','drawbox=x=80:y=0:w=80:h=100:color=cyan:t=fill',
        '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(ad_file)])
    banner_file=folder/'white-banner.png'
    command(['-f','lavfi','-i','color=c=white:s=24x24','-frames:v','1',str(banner_file)])
    source=client.upload(source_file,'video');advert=client.upload(ad_file,'advert');banner=client.upload(banner_file,'banner')
    config={'source_id':source['id'],'audio_stream_index':source['audio_streams'][0]['index'],
        'canvas':{'width':240,'height':300},'video':{'x':20,'y':50,'w':200,'h':120,'fit':'stretch'},
        'background':{'enabled':False,'color':'#111827'},'encoder':'cpu','fps':24,'parts':[{'start':0,'end':3}],
        'subtitles':{'enabled':False},'banners':[{'id':'white','enabled':True,'asset_id':banner['id'],'start':0,'end':3,
        'geometry':{'x':28,'y':70,'w':24,'h':24,'fit':'stretch'},'schedule':{'mode':'each','start':0,'duration':3}}]}
    outputs={}
    for name,effects in [('neutral',{}),('mirror',{'mirror':True}),('correction',{'mirror':True,'brightness':1.25,'contrast':1.1,'saturation':0})]:
        payload=deepcopy(config)
        if effects:payload['video_effects']=effects
        job=client.wait(client.request('/api/render',payload)['id']);movie=folder/(name+'.mp4')
        client.download('/api/jobs/'+job['id']+'/files/part-01.mp4',movie)
        outputs[name]={'left':pixel(movie,.5,60,120),'right':pixel(movie,.5,180,120),'gray':pixel(movie,.5,120,110),
            'banner':pixel(movie,.5,36,80),'background':pixel(movie,.5,100,220),'job_id':job['id']}
        assert min(outputs[name]['banner'])>230,outputs[name]
        assert max(outputs[name]['background'])<50,outputs[name]
        assert tone_energy(movie,.5,440)>1e8
    assert outputs['neutral']['left'][0]>200 and outputs['neutral']['right'][2]>200
    assert outputs['mirror']['left'][2]>200 and outputs['mirror']['right'][0]>200
    for key in ('left','right','gray'):
        assert max(outputs['correction'][key])-min(outputs['correction'][key])<6,outputs['correction']
    assert sum(outputs['correction']['gray'])>sum(outputs['neutral']['gray']),outputs
    sample_payload=deepcopy(config);sample_payload['video_effects']={'mirror':True,'brightness':1.25,'contrast':1.1,'saturation':0}
    sample=client.wait(client.request('/api/sample',sample_payload)['id'])
    sample_file=next(file for file in sample['files'] if file['name'].endswith('.mp4') and not file.get('internal'))
    sample_movie=folder/'sample.mp4';client.download(sample_file['url'],sample_movie)
    for key,x,y in [('left',60,120),('right',180,120),('gray',120,110)]:
        actual=pixel(sample_movie,.5,x,y);assert all(abs(a-b)<=4 for a,b in zip(actual,outputs['correction'][key])),actual
    payload=deepcopy(config);payload['video_effects']={'mirror':True,'saturation':0}
    payload['background']={'enabled':True,'blur':8,'color':'#111827'}
    payload['advertising']={'enabled':True,'asset_id':advert['id'],'profile':'custom','start':1,'repeat':False,
        'audio_stream_index':advert['audio_streams'][0]['index'],'geometry':{'x':60,'y':180,'w':120,'h':80,'fit':'stretch'}}
    job=client.wait(client.request('/api/render',payload)['id']);movie=folder/'with-advert.mp4'
    client.download('/api/jobs/'+job['id']+'/files/part-01.mp4',movie)
    ad_left,ad_right=pixel(movie,1.5,80,210),pixel(movie,1.5,160,210)
    assert ad_left[0]>180 and ad_left[1]>180 and ad_left[2]<50,ad_left
    assert ad_right[0]<50 and ad_right[1]>180 and ad_right[2]>180,ad_right
    for time in (.5,1.5,2.5):
        p=pixel(movie,time,60,120);assert max(p)-min(p)<6,p
        p=pixel(movie,time,40,280);assert max(p)-min(p)<6,p
    assert tone_energy(movie,1.5,880)>tone_energy(movie,.5,880)*20
    result={'source_id':source['id'],'advert_id':advert['id'],'banner_id':banner['id'],'outputs':outputs,
        'passed':['Mirror swaps actual source pixels; correction produces grayscale and changes brightness',
        'Banners and advertising keep their colors and position; source and blurred/frozen background are adjusted',
        'Source audio and exclusive advertising audio retain their timing; sample and full MP4 corrections match']}
    (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
