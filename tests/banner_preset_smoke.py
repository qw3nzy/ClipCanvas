"""Seed a synthetic transparent voiced preset and inspect its actual MP4 export."""
import json
import sys
from pathlib import Path
from integration_smoke import Client, command, center_pixel, tone_energy


def main():
    manifest=json.loads(Path(sys.argv[1]).read_text('utf-8')); client=Client(manifest['base'])
    folder=Path(manifest['directory'])/'banner-preset';folder.mkdir(exist_ok=True)
    command(['-f','lavfi','-i','color=c=blue:s=128x72:r=12:d=181',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=181',
        '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(folder/'long-source.mp4')])
    fixture=folder/'synthetic-banner.mov'
    command(['-f','lavfi','-i','color=c=blue:s=1248x496:r=12:d=5',
        '-f','lavfi','-i','sine=frequency=1320:sample_rate=48000:duration=5',
        '-vf','drawbox=x=12:y=12:w=1224:h=472:color=green:t=fill,format=rgba,colorkey=0x0000FF:0.23:0',
        '-c:v','qtrle','-pix_fmt','argb','-c:a','pcm_s16le','-shortest',str(fixture)])
    media=client.upload(fixture,'banner');assert media['has_alpha'] and media['audio']
    template=client.request('/api/banner-templates',{'name':'БубаVPN','asset_id':media['id'],'width':1080,'height':1920,
        'geometry':{'x':0,'y':700.8,'w':1080,'h':518.4,'fit':'stretch'},'chromakey':{'enabled':False},
        'duration':5,'loop':False,'speed':1,'volume':1,'schedule':{'mode':'bubavpn','duration':5},
        'instructions':'Не менее 25% площади экрана, ровно по центру. Основной показ в середине клипа; дополнительный показ за полную минуту после первой. Фон удалён, озвучка обязательна.',
        'layout_rules':{'movable':False,'resizable':False,'zone':'center','center_reference':'canvas'},'locked':True})
    source_fixture=folder/'red-source.mp4'
    command(['-f','lavfi','-i','color=c=red:s=256x144:r=24:d=8',
        '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=8',
        '-c:v','libx264','-preset','ultrafast','-c:a','aac','-shortest',str(source_fixture)])
    source=client.upload(source_fixture,'video')
    config={'source_id':source['id'],'audio_stream_index':source['audio_streams'][0]['index'],
        'canvas':{'width':240,'height':300},'video':{'x':0,'y':0,'w':240,'h':300,'fit':'stretch'},
        'background':{'enabled':False},'subtitles':{'enabled':False},'encoder':'cpu','fps':24,
        'parts':[{'start':0,'end':8}], 'banners':[{'id':'buba','name':'БубаVPN','enabled':True,'asset_id':media['id'],
        'start':0,'end':5,'geometry':{'x':0,'y':109.5,'w':240,'h':81,'fit':'stretch'},
        'schedule':{'mode':'bubavpn','duration':5},'volume':1,'loop':False}]}
    job=client.wait(client.request('/api/render',config)['id']);movie=folder/'preset.mp4'
    client.download('/api/jobs/'+job['id']+'/files/part-01.mp4',movie)
    before,center,after=[center_pixel(movie,t) for t in (.5,4,7.5)]
    assert before[0]>200 and after[0]>200 and center[1]>80 and center[2]<30,(before,center,after)
    corner=command(['-ss','4','-i',str(movie),'-frames:v','1','-vf','crop=2:2:0:110','-pix_fmt','rgb24','-f','rawvideo','pipe:1'])[:3]
    assert corner[0]>180 and corner[1]<30 and corner[2]<30,tuple(corner)
    assert tone_energy(movie,4,1320)>tone_energy(movie,.5,1320)*100
    result={'passed':['Preset template includes real alpha video and enabled sound','Actual MP4 shows the centered banner only around the clip midpoint','Transparent keyed borders reveal the source; banner voice is audible in actual PCM'],
        'template_id':template['id'],'asset_id':media['id'],'job_id':job['id']}
    (folder/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8');print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
