"""Compare the former and current ASR pipeline on an explicitly chosen recording.

Reference JSON: {"source":"C:/agreed/speech.mp4", "audio_stream_index":0,
"language":"ru", "model":"tiny", "cues":[{"start":.2,"end":1.5,"text":"..."}]}.
Uses already cached Whisper weights; never downloads a model or reads other media.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.runtime/deps'))
sys.path.insert(0,str(ROOT/'engine'))


def words(text):
    return re.findall(r'[^\W_]+',text.casefold(),re.UNICODE)


def metrics(reference, cues):
    expected = words(' '.join(cue['text'] for cue in reference))
    actual = words(' '.join(cue['text'] for cue in cues))
    row = list(range(len(actual)+1))
    for i, token in enumerate(expected,1):
        previous, row = row, [i]
        for j, other in enumerate(actual,1):
            row.append(min(row[-1]+1,previous[j]+1,previous[j-1]+(token!=other)))
    starts, ends, missing = [], [], 0
    for cue in reference:
        matching = [item for item in cues if item['start'] < cue['end'] and item['end'] > cue['start']]
        if not matching:
            missing += 1
            continue
        starts.append(abs(min(item['start'] for item in matching)-cue['start']))
        ends.append(abs(max(item['end'] for item in matching)-cue['end']))
    return {'word_error_rate':row[-1]/max(1,len(expected)), 'word_edits':row[-1],
            'start_mae_seconds':sum(starts)/len(starts) if starts else None,
            'end_mae_seconds':sum(ends)/len(ends) if ends else None, 'missing_phrases':missing}


def former_groups(segments, normalize, duration):
    cues = []
    for segment in segments:
        group = []
        def flush():
            if group:
                cues.append({'start':group[0].start,'end':max(group[-1].end,group[0].start+.01),
                    'text':''.join(word.word for word in group).strip(),'words':[]})
        if not segment.words:
            if segment.text.strip() and segment.end > segment.start:
                cues.append({'start':segment.start,'end':segment.end,'text':segment.text.strip(),'words':[]})
        for word in segment.words or []:
            if group and (len(group)>=6 or len(''.join(item.word for item in group))+len(word.word)>42 or word.end-group[0].start>3):
                flush()
                group=[]
            group.append(word)
        flush()
    return normalize(cues,duration)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--reference',required=True)
    parser.add_argument('--model-cache',default=str(ROOT/'data/models'))
    args = parser.parse_args()
    reference = json.loads(Path(args.reference).read_text('utf-8-sig'))
    source = Path(reference['source']).resolve(strict=True)
    folder = Path(tempfile.mkdtemp(prefix='speech-accuracy-',dir=ROOT/'.runtime/qa'))
    os.environ['CLIPCANVAS_DATA'] = str(folder/'data')
    import clipcanvas_server as server
    from faster_whisper import WhisperModel
    from integration_smoke import command
    cues = reference['cues']
    for cue in cues: server.Cue.model_validate(cue)
    inventory = server.probe(source)
    selected = reference.get('audio_stream_index',inventory['recommended_audio_stream_index'])
    track = next(item for item in inventory['audio_streams'] if item['index']==selected)
    offset = track['start_time']-inventory.get('video_start_time',0)
    filters = 'asetpts=PTS-STARTPTS'
    if offset < 0: filters += f',atrim=start={-offset},asetpts=PTS-STARTPTS'
    if offset > 0: filters += f',adelay={round(offset*1000)}:all=1'
    audio = folder/'speech.wav'
    command(['-i',str(source),'-map',f'0:{selected}','-vn','-af',filters,'-t',str(inventory['duration']),'-ac','1','-ar','16000',str(audio)])
    model = WhisperModel(reference.get('model','tiny'),device='cpu',compute_type='int8',
        download_root=args.model_cache,local_files_only=True)
    result = {'reference':str(Path(args.reference).resolve()),'audio_stream_index':selected,'model':reference.get('model','tiny')}
    try:
        for name, options in [('before',{}),('after',{'vad_parameters':{'min_silence_duration_ms':500,'speech_pad_ms':150},
            'condition_on_previous_text':False,'hallucination_silence_threshold':1.0})]:
            segments, _info = model.transcribe(str(audio),language=reference.get('language','ru'),beam_size=5,
                                              vad_filter=True,word_timestamps=True,**options)
            segments = list(segments)
            if name == 'before': recognized = former_groups(segments,server.normalize_cues,inventory['duration'])
            else:
                with patch.object(server,'cancelled',return_value=False),patch.object(server,'update'):
                    recognized = server.group_words(segments,'benchmark',inventory['duration'])
            server.atomic_json(folder/(name+'.json'),recognized)
            result[name] = metrics(cues,recognized)
    finally:
        model.model.unload_model()
    result['directory'] = str(folder)
    server.atomic_json(folder/'result.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
