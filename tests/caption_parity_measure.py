"""Measure glyph boxes/centroids from caption_parity_smoke.cjs artifacts."""
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.runtime'/'deps'))
import av
import numpy as np


def metrics(image):
    mask=(image[:,:,0]>160)&(image[:,:,1]>160)&(image[:,:,2]>160)
    y,x=np.where(mask)
    rows=np.flatnonzero(mask.any(axis=1))
    groups=np.split(rows,np.flatnonzero(np.diff(rows)>3)+1)
    lines=[]
    for group in groups:
        _,line_x=np.where(mask[group[0]:group[-1]+1])
        lines.append([int(line_x.min()),int(group[0]),int(line_x.max()),int(group[-1])])
    return {'bbox':[int(x.min()),int(y.min()),int(x.max()),int(y.max())],
            'centroid':[float(x.mean()),float(y.mean())],'lines':lines}


def main():
    manifest=json.loads(Path(sys.argv[1]).read_text('utf-8'))
    folder=Path(manifest['directory'])/'caption-font-parity'
    results=[]
    for item in json.loads((folder/'requests.json').read_text('utf-8')):
        index=item['index']
        with av.open(str(folder/f'preview-{index}.png')) as container:
            preview=next(container.decode(video=0)).to_ndarray(format='rgb24')
        with av.open(str(folder/f'export-{index}.mp4')) as container:
            frames=[(float(frame.time),frame.to_ndarray(format='rgb24')) for frame in container.decode(video=0)]
        exported=min(frames,key=lambda row:abs(row[0]-.5))[1]
        pm,em=metrics(preview),metrics(exported)
        result={'font':item['font'],'preview':pm,'export':em,
                'synthetic_italic':item['font']=='Tahoma' and item['request']['subtitles']['cues'][0].get('appearance',{}).get('italic',False),
                'bbox_delta':np.subtract(em['bbox'],pm['bbox']).tolist(),
                'centroid_delta':np.subtract(em['centroid'],pm['centroid']).tolist()}
        results.append(result)
        stacked=np.concatenate([preview,exported],axis=1)
        with av.open(str(folder/f'compare-{index}.png'),'w') as output:
            stream=output.add_stream('png',rate=1);stream.width=480;stream.height=300;stream.pix_fmt='rgb24'
            for packet in stream.encode(av.VideoFrame.from_ndarray(stacked,format='rgb24')):output.mux(packet)
            for packet in stream.encode():output.mux(packet)
    (folder/'result.json').write_text(json.dumps(results,indent=2),'utf-8')
    print(json.dumps([{key:result[key] for key in ('font','bbox_delta','centroid_delta','synthetic_italic')}
                      for result in results],indent=2))
    for result in results:
        # Tahoma has no installed italic face: Chromium and libass synthesize
        # slightly different slants. Keep this explicit instead of hiding it in
        # a general tolerance or changing the requested font to another family.
        tolerance=3 if result['synthetic_italic'] else 2
        assert len(result['preview']['lines'])==len(result['export']['lines'])==2,result
        assert max(abs(value) for value in result['bbox_delta'])<=tolerance,result
        assert max(abs(value) for value in result['centroid_delta'])<=2,result
        for first,second in zip(result['preview']['lines'],result['export']['lines']):
            assert max(abs(value) for value in np.subtract(first,second))<=tolerance,result


if __name__=='__main__':main()
