"""Real FFmpeg and optional installed Ollama, against an existing isolated QA server."""
import argparse
import json
from pathlib import Path
import sys
import time

from integration_smoke import Client


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest')
    parser.add_argument('--ai', action='store_true')
    args = parser.parse_args()
    manifest = json.loads(Path(args.manifest).read_text('utf-8'))
    client = Client(manifest['base'])
    folder = Path(manifest['directory'])
    source_id = manifest['source_id']
    source = client.request('/api/assets/'+source_id)
    passed = []

    def wait(job_id, seconds=900):
        start = time.monotonic()
        while time.monotonic()-start < seconds:
            job = client.request('/api/jobs/'+job_id)
            if job['status'] == 'done':
                return job
            if job['status'] in {'error', 'cancelled'}:
                raise AssertionError(job.get('error', job['status']))
            time.sleep(.4)
        client.request('/api/jobs/'+job_id+'/cancel', {})
        raise AssertionError('Job timeout: '+job_id)

    overview = wait(client.request('/api/studio/overview', {'source_id': source_id, 'audio_stream_index': 2})['id'])
    view = overview['overview']
    assert view['duration'] == source['duration'] and view['waveform'] and view['thumbnails'], view
    assert all(0 <= x <= source['duration'] for x in view['scenes'])
    assert all(f['internal'] for f in overview['files'])
    assert all((folder/'data/jobs'/overview['id']/f['name']).is_file() for f in overview['files'])
    second = wait(client.request('/api/studio/overview', {'source_id': source_id, 'audio_stream_index': 2})['id'])
    assert second['overview']['job_id'] == view['job_id']
    passed.append('real timeline thumbnails, waveform, scene boundaries and cache reuse')

    cues = [{'start': 0.5, 'end': 3.2, 'text': 'Это достаточно длинная реплика персонажа для проверки разбивки на строки.'},
            {'start': 4, 'end': 7.5, 'text': 'Привет как дела'}]
    reviewed = wait(client.request('/api/ai/subtitles', {'source_id': source_id, 'cues': cues, 'mode': 'format'})['id'])
    assert [(c['start'], c['end']) for c in reviewed['reviewed_cues']] == [(.5, 3.2), (4, 7.5)]
    assert '\n' in reviewed['reviewed_cues'][0]['text']
    passed.append('subtitle formatting preserves real phrase boundaries')
    template = client.request('/api/templates', {'name': 'Smoke template', 'settings': {'width': 1080, 'height': 1920,
        'subtitles': {'size': 52, 'cues': cues}, 'sourceId': source_id}})
    assert 'cues' not in template['settings']['subtitles'] and 'sourceId' not in template['settings']
    assert any(t['id'] == template['id'] for t in client.request('/api/templates')['templates'])
    passed.append('validated templates exclude source material and captions')

    render = client.request('/api/jobs/'+manifest['job_id'])
    check = client.request('/api/studio/preflight', render['request'])
    assert check['parts'] == len(render['request']['parts'])
    file = next(f for f in render['files'] if f['name'].endswith('.mp4'))
    checked = wait(client.request('/api/studio/check-output', {'job_id': render['id'], 'filename': file['name']})['id'])
    assert checked['metadata']['audio'] and checked['metadata']['width'] == 240
    passed.append('export preflight and real MP4 decoding/black/silence detection')

    brief = client.request('/api/jobs?summary=true')['jobs']
    assert all('request' not in job and 'cues' not in job for job in brief)
    assert 'request' not in json.loads((folder/'data/jobs'/render['id']/'job.json').read_text('utf-8'))
    assert (folder/'data/jobs'/render['id']/'request.json').is_file()
    passed.append('small history responses and immutable request storage')
    evidence = {'passed': passed, 'overview_id': overview['id'], 'review_id': reviewed['id'], 'check_id': checked['id']}

    if args.ai:
        assert client.request('/api/ai/start', {})['available']
        start = time.monotonic()
        result = wait(client.request('/api/ai/highlights', {'source_id': source_id, 'audio_stream_index': 2,
            'start': 0, 'end': 8, 'min_duration': 2, 'max_duration': 7, 'count': 4, 'mode': 'funny',
            'transcribe': False, 'cues': [{'start': 1, 'end': 3, 'text': 'Как тебе удалось взломать секретную систему?'},
                                         {'start': 3.5, 'end': 6.5, 'text': 'Я ввёл пароль: пароль. Они даже не сменили его!'}]})['id'])
        assert result['coverage'] == [{'start': 0, 'end': 8}]
        assert all(0 <= c['start'] < c['end'] <= 8 and 2 <= c['end']-c['start'] <= 7 for c in result['clips'])
        assert any(c['start'] <= 1 and c['end'] >= 6.5 for c in result['clips']), 'A joke must include its setup and punchline: '+str(result['clips'])
        evidence['ai_seconds'] = round(time.monotonic()-start, 2)
        evidence['ai_job_id'] = result['id']
        evidence['ai_clips'] = result['clips']
        corrected = wait(client.request('/api/ai/subtitles', {'source_id': source_id, 'mode': 'correct', 'cues': cues})['id'])
        assert all(0 <= change['index'] < len(cues) for change in corrected['changes'])
        evidence['ai_review_id'] = corrected['id']
        evidence['ai_changes'] = corrected['changes']
        passed.append('actual Qwen3.5-4B vision + dialogue analysis and structured caption corrections')
    (folder/'studio-result.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(evidence, ensure_ascii=True, indent=2), flush=True)


if __name__ == '__main__':
    main()
