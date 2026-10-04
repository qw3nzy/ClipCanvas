"""Additional real FFmpeg checks on the isolated integration_smoke server."""
import json
from pathlib import Path
import sys

from integration_smoke import Client, center_pixel, command, tone_energy


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text('utf-8'))
    client = Client(manifest['base'])
    directory = Path(manifest['directory']) / 'variants'
    directory.mkdir(exist_ok=True)
    original = client.request('/api/jobs/' + manifest['job_id'])['request']
    checks = []
    buba = json.loads(json.dumps(original))
    buba['advertising'].update(profile='buba', placement='middle', repeat=False, speed=1.2,
                              chromakey={'enabled':True,'color':'#008000','similarity':.18,'blend':.05})
    result = client.wait(client.request('/api/render', buba)['id'])
    plan = result['outputs'][0]['timeline']
    ad = next(s for s in plan if s['kind'] == 'advert')
    assert ad['output_start'] == 3
    assert abs(ad['duration'] - 1/1.2) < .01
    output = directory / 'buba-chromakey.mp4'
    client.download(next(f['url'] for f in result['files'] if f['name'].endswith('.mp4')), output)
    pixel = center_pixel(output, 3.3)
    assert pixel[2] > 150 and pixel[1] < 60, pixel
    assert tone_energy(output, 3.3, 1320) > 10*tone_energy(output, 3.3, 880)
    checks.append('BUBA centered-time insertion, 1.2x speed, green removal reveals frozen source, ad audio')
    for style in ['word', 'cinema']:
        payload = json.loads(json.dumps(original))
        payload.update(advertising={'enabled':False}, parts=[{'start':0,'end':3}])
        payload['subtitles'].update(style=style, background=style=='cinema', y=75,
            cues=[{'start':.3,'end':2.8,'text':'Проверяем оформление текста','words':[
                {'start':.3,'end':1,'text':'Проверяем'},
                {'start':1,'end':1.9,'text':' оформление'},
                {'start':1.9,'end':2.8,'text':' текста'}]}])
        job = client.wait(client.request('/api/render', payload)['id'])
        movie = directory / (style + '.mp4')
        client.download(next(f['url'] for f in job['files'] if f['name'].endswith('.mp4')), movie)
        command(['-ss','1.4','-i',str(movie),'-frames:v','1',str(directory/(style+'.png'))])
        checks.append(style + ' ASS style renders Cyrillic and word timings')
    (directory/'result.json').write_text(json.dumps({'checks':checks},ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(checks,ensure_ascii=False))


if __name__ == '__main__':
    main()
