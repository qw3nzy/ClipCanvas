"""Real FFmpeg acceptance for cuts, timed alpha banners and shared sample/full output.

Run after integration_smoke.py --keep-server, using its result.json.
All fixtures and outputs remain inside that isolated .runtime/qa directory.
"""
from copy import deepcopy
import json
from pathlib import Path
import struct
import sys
import zlib

from integration_smoke import Client, command
import av
import numpy as np


def png(path, pixels):
    def chunk(kind, data):
        return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data))
    height, width, _ = pixels.shape
    raw = b''.join(b'\0'+row.tobytes() for row in pixels)
    path.write_bytes(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))+
                     chunk(b'IDAT', zlib.compress(raw))+chunk(b'IEND', b''))


def frames(path):
    with av.open(str(path)) as container:
        assert container.streams.audio, 'Audio stream lost'
        return [(float(frame.time), frame.to_ndarray(format='rgb24')) for frame in container.decode(video=0)]


def main():
    manifest = json.loads(Path(sys.argv[1]).read_text('utf-8'))
    client = Client(manifest['base'])
    directory = Path(manifest['directory'])/'composition'
    directory.mkdir(exist_ok=True)
    pixels = np.zeros((40, 80, 4), np.uint8)
    pixels[:] = [0, 255, 0, 255]
    pixels[10:30, 25:55] = [255, 0, 0, 255]
    pixels[:8, :8] = [255, 255, 255, 0]
    banner_path = directory/'key-alpha.png'
    png(banner_path, pixels)
    banner = client.upload(banner_path, 'banner')
    passed = []
    payload = {
        'schema_version': 2, 'source_id': manifest['source_id'], 'audio_stream_index': 2,
        'canvas': {'width': 240, 'height': 300},
        'video': {'x': 0, 'y': 65, 'w': 240, 'h': 135, 'fit': 'contain'},
        'background': {'enabled': False, 'blur': 0, 'color': '#112233'},
        'parts': [{'start': 0, 'end': 8}],
        'cuts': [{'start': 0, 'end': 1}, {'start': 3, 'end': 4}, {'start': 7, 'end': 8}],
        'banners': [{'id': 'first', 'enabled': True, 'asset_id': banner['id'], 'start': 2, 'end': 6,
                     'timebase': 'source', 'geometry': {'x': 10, 'y': 10, 'w': 80, 'h': 40},
                     'chromakey': {'enabled': True, 'color': '#00ff00', 'similarity': .1, 'blend': .02}},
                    {'id': 'second', 'enabled': True, 'asset_id': banner['id'], 'start': 5, 'end': 7,
                     'timebase': 'source', 'geometry': {'x': 145, 'y': 10, 'w': 80, 'h': 40}}],
        'subtitles': {'enabled': True, 'font': 'Arial', 'size': 20, 'x': 50, 'y': 82, 'width': 85,
                      'bold': True, 'italic': False, 'align': 'center', 'line_spacing': 1.4,
                      'outline': 1, 'shadow': 2, 'background': True, 'background_opacity': .4,
                      'cues': [{'start': .5, 'end': 5.5, 'text': 'Первая строка\nВторая строка',
                                'render_lines': ['Первая строка', 'Вторая строка'],
                                'appearance': {'color': '#ffff00', 'x': 48}},
                               {'start': 6, 'end': 7.5, 'text': 'Конец', 'render_lines': ['Конец']}]},
        'quality': 'standard', 'encoder': 'cpu', 'fps': 24, 'zip': False,
    }
    resolved = client.request('/api/composition/resolve', payload)['parts'][0]
    assert abs(resolved['duration']-5) < .001, resolved
    assert [(c['start'], c['end']) for c in resolved['cues']] == [(0, 2), (2, 3.5), (4, 5)], resolved['cues']
    first = [b for b in resolved['banners'] if b['id'] == 'first']
    assert [(b['start'], b['end']) for b in first] == [(1, 2), (2, 4)], first
    passed.append('Cuts remap crossing subtitles and banners at every retained boundary')
    sample = client.wait(client.request('/api/sample', payload)['id'])
    assert len(sample['outputs']) == 1
    full_payload = deepcopy(payload)
    full_payload['parts'].append({'start': 1, 'end': 6})
    full = client.wait(client.request('/api/render', full_payload)['id'])
    assert len(full['outputs']) == 2
    paths = []
    for name, job in [('sample', sample), ('full', full)]:
        movie = next(f for f in job['files'] if f['name'].endswith('.mp4'))
        path = directory/(name+'.mp4')
        client.download(movie['url'], path)
        paths.append(path)
    sample_frames, full_frames = [frames(path) for path in paths]
    assert len(sample_frames) == len(full_frames) == 120
    assert all(np.array_equal(a[1], b[1]) for a, b in zip(sample_frames, full_frames)), 'Sample/full pixels diverge'
    passed.append('Sample renders one clip; full renders two; matching clip has 120 identical decoded frames and sound')
    at = lambda t: min(sample_frames, key=lambda pair: abs(pair[0]-t))[1]
    for t, visible in [(.5, False), (1.25, True), (2.5, True), (3.75, True), (4.5, False)]:
        pixel = at(t)[30, 50].astype(int)
        assert bool(pixel[0] > 180 and pixel[1] < 50) == visible, (t, pixel)
    for t in [1.25, 2.5, 3.75]:
        image = at(t)
        for y, x in [(15, 30), (14, 14)]:
            assert np.max(np.abs(image[y, x].astype(int)-[17, 34, 51])) < 15, (t, y, x, image[y, x])
    assert at(3.5)[15, 160, 1] > 180, 'Second banner independent interval'
    passed.append('Banner intervals match MP4 pixels, green key is transparent, original PNG alpha survives')
    keyed = client.request('/api/banners/preview', {'asset_id':banner['id'],
                           'chromakey':payload['banners'][0]['chromakey']})
    preview_path = directory/'server-key-preview.png'
    client.download(keyed['url'], preview_path)
    with av.open(str(preview_path)) as container:
        preview_rgba = next(container.decode(video=0)).to_ndarray(format='rgba')
    expected_alpha = pixels[:,:,3].copy()
    expected_alpha[np.all(pixels[:,:,:3] == [0,255,0],axis=2)] = 0
    assert np.array_equal(preview_rgba[:,:,3],expected_alpha), 'Preview key changes pre-existing alpha'
    assert np.max(np.abs(preview_rgba[20,40,:3].astype(int)-at(1.25)[30,50].astype(int))) < 15
    cached = client.request('/api/banners/preview', {'asset_id':banner['id'],
                           'chromakey':payload['banners'][0]['chromakey']})
    assert cached['cached'] and cached['url'] == keyed['url']
    original_preview = client.request('/api/banners/preview', {'asset_id':banner['id'], 'chromakey':{'enabled':False}})
    assert original_preview['url'] == '/api/media/'+banner['id']
    passed.append('Cached server PNG preview matches exported key and exactly preserves original alpha; key can be disabled')
    srt = next(f for f in sample['files'] if f['name'].endswith('.srt'))
    client.download(srt['url'], directory/'sample.srt')
    assert '00:00:03,500' in (directory/'sample.srt').read_text('utf-8-sig')
    original = client.request('/api/assets/'+manifest['source_id'])
    assert original['duration'] >= 8
    restored = deepcopy(payload)
    restored['cuts'] = []
    assert client.request('/api/composition/resolve', restored)['parts'][0]['duration'] == 8
    passed.append('Removal is reversible and original source remains intact')
    style = client.request('/api/templates', {'name': 'QA extended style', 'settings': {
        'width': 240, 'height': 300, 'subtitles': payload['subtitles'],
        'geometry': {'video': payload['video']}, 'background': payload['background']}})
    assert style['settings']['subtitles']['line_spacing'] == 1.4
    assert 'cues' not in style['settings']['subtitles']
    passed.append('Extended layout template retains style without source dialogue')
    pattern_path = directory/'blur-source.mp4'
    command(['-f','lavfi','-i','testsrc2=s=320x180:r=24:d=1', '-c:v','libx264','-preset','ultrafast',
             '-pix_fmt','yuv420p',str(pattern_path)])
    pattern = client.upload(pattern_path,'video')
    blur_request = deepcopy(payload)
    blur_request.update(source_id=pattern['id'],audio_stream_index=None,cuts=[],banners=[],
                        subtitles={'enabled':False},parts=[{'start':0,'end':1}],
                        quality='high',
                        video={'x':60,'y':100,'w':120,'h':68,'fit':'contain'},
                        background={'enabled':True,'blur':0,'color':'#112233'})
    blur_images, blur_jobs = [], []
    for intensity in [0,20]:
        blur_request['background']['blur'] = intensity
        job = client.wait(client.request('/api/sample',blur_request)['id'])
        path = directory/f'blur-{intensity}.mp4'
        client.download(next(f['url'] for f in job['files'] if f['name'].endswith('.mp4')),path)
        blur_images.append(frames(path)[12][1].astype(float))
        blur_jobs.append(job['id'])
    first, second = blur_images
    # Interior of the foreground remains sharp despite a heavily changed backdrop.
    foreground_difference = float(np.mean(np.abs(first[108:160,68:172]-second[108:160,68:172])))
    background_difference = float(np.mean(np.abs(first[:80]-second[:80])))
    crisp_edges = float(np.mean(np.abs(np.diff(first[:80],axis=1))))
    blurred_edges = float(np.mean(np.abs(np.diff(second[:80],axis=1))))
    assert foreground_difference < 4, foreground_difference
    assert background_difference > 5, background_difference
    assert blurred_edges < crisp_edges*.75, (crisp_edges,blurred_edges)
    passed.append('Blur 0/20 changes only the background; foreground pixel difference remains below codec tolerance')
    report = {'passed': passed, 'sample_job': sample['id'], 'full_job': full['id'],
              'banner_id': banner['id'], 'render_payload': payload, 'directory': str(directory),
              'blur_jobs':blur_jobs,'blur_metrics':{'foreground_mean_difference':foreground_difference,
                  'background_mean_difference':background_difference,'sharp_edge_mean':crisp_edges,'blurred_edge_mean':blurred_edges}}
    (directory/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps({'passed': passed, 'directory': str(directory)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
