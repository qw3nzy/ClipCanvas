"""Inspect localized blur, source clock, overlays, cropping, and actual MP4 audio."""
from copy import deepcopy
import json
from pathlib import Path
import statistics
import sys
from integration_smoke import Client, command, tone_energy
from video_effects_smoke import pixel


def variance(movie,time,x,y,w=32,h=32):
    data=command(['-ss',str(time),'-i',str(movie),'-frames:v','1','-vf',f'crop={w}:{h}:{x}:{y}',
                  '-pix_fmt','gray','-f','rawvideo','pipe:1'])
    return statistics.pvariance(data[:w*h])


def main():
    manifest=json.loads(Path(sys.argv[1]).read_text('utf-8'));client=Client(manifest['base'])
    folder=Path(manifest['directory'])/'video-blur';folder.mkdir(exist_ok=True)
    source_file=folder/'grid.mp4'
    command(['-f','lavfi','-i','color=c=white:s=320x180:r=24:d=4',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=4',
        '-vf','drawgrid=width=8:height=8:thickness=4:color=black',
        '-c:v','libx264','-preset','ultrafast','-crf','12','-c:a','aac','-shortest',str(source_file)])
    ad_file=folder/'grid-advert.mp4'
    command(['-f','lavfi','-i','color=c=white:s=160x60:r=24:d=3',
        '-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=3',
        '-vf','drawgrid=width=8:height=8:thickness=4:color=black',
        '-c:v','libx264','-preset','ultrafast','-crf','12','-c:a','aac','-shortest',str(ad_file)])
    banner_file=folder/'red-banner.png';command(['-f','lavfi','-i','color=c=red:s=30x30','-frames:v','1',str(banner_file)])
    source=client.upload(source_file,'video');advert=client.upload(ad_file,'advert');banner=client.upload(banner_file,'banner')
    region={'id':'one','name':'Центр','enabled':True,'x':.25,'y':.25,'w':.5,'h':.5,'strength':70,'start':1,'end':3}
    config={'source_id':source['id'],'audio_stream_index':source['audio_streams'][0]['index'],
        'canvas':{'width':320,'height':240},'video':{'x':0,'y':0,'w':320,'h':180,'fit':'stretch'},
        'background':{'enabled':False,'color':'#111827'},'encoder':'cpu','fps':24,'parts':[{'start':0,'end':4}],
        'video_blurs':[region], 'subtitles':{'enabled':False},
        'banners':[{'id':'red','enabled':True,'asset_id':banner['id'],'start':0,'end':4,
            'geometry':{'x':145,'y':75,'w':30,'h':30,'fit':'stretch'},'schedule':{'mode':'each','start':0,'duration':4}}]}
    def render(name,payload,endpoint='/api/render'):
        job=client.wait(client.request(endpoint,payload)['id'])
        movie=folder/(name+'.mp4');file=next(f for f in job['files'] if f['name'].endswith('.mp4') and not f.get('internal'))
        client.download(file['url'],movie);return movie
    movie=render('localized',config)
    before,inside,after=[variance(movie,t,100,65) for t in (.5,1.5,3.5)]
    outside=variance(movie,1.5,8,140)
    assert before>2000 and after>2000 and inside<before*.05 and outside>2000,(before,inside,after,outside)
    assert pixel(movie,1.5,152,82)[0]>220
    assert tone_energy(movie,1.5,440)>1e8
    sample=render('sample',config,'/api/sample');assert variance(sample,1.5,100,65)<before*.05
    payload=deepcopy(config);payload['video_blurs'][0]['enabled']=False
    assert variance(render('disabled',payload),1.5,100,65)>2000
    payload=deepcopy(config);payload['video_blurs'].append({'id':'two','x':.7,'y':.7,'w':.25,'h':.25,'strength':70,'start':2,'end':None})
    multiple=render('multiple',payload)
    assert variance(multiple,3.5,100,65)>2000 and variance(multiple,3.5,240,136,20,20)<100
    payload=deepcopy(config);payload['cuts']=[{'start':0,'end':.75},{'start':2,'end':2.5}]
    cut=render('cuts',payload)
    assert variance(cut,.1,100,65)>2000 and variance(cut,.5,100,65)<100
    assert variance(cut,1.4,100,65)<100 and variance(cut,2.2,100,65)>2000
    payload=deepcopy(config);payload['video_crop']={'enabled':True,'x':0,'y':0,'w':.75,'h':1};payload['video_effects']={'mirror':True}
    transformed=render('crop-mirror',payload)
    assert variance(transformed,1.5,32,65)<100 and variance(transformed,1.5,272,65)>1000
    payload=deepcopy(config);payload['background']={'enabled':True,'blur':8}
    payload['advertising']={'enabled':True,'asset_id':advert['id'],'profile':'custom','start':1.5,'repeat':False,
        'audio_stream_index':advert['audio_streams'][0]['index'],'geometry':{'x':0,'y':180,'w':160,'h':60,'fit':'stretch'}}
    with_advert=render('advert',payload)
    assert variance(with_advert,3.5,100,65)<100
    assert variance(with_advert,3.5,16,190,32,32)>2000
    assert tone_energy(with_advert,3.5,880)>tone_energy(with_advert,.5,880)*20
    payload['advertising']['start']=.5
    assert variance(render('frozen-before-start',payload),2,100,65)>2000
    result={'source_id':source['id'],'advert_id':advert['id'],'banner_id':banner['id'],
        'region':region,'variance':{'before':before,'inside':inside,'after':after,'outside':outside},
        'passed':['Only the selected rectangle blurs during its source interval; sample/full MP4 agree and audio is retained',
                  'Multiple regions, disabled region, cuts, crop, and mirror preserve independent source masks',
                  'Banners/advertising stay sharp; frozen source blur uses paused source time throughout advertising']}
    (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
