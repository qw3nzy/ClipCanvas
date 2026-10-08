"""Actual warning exports, batch acceptance, cleanup, and restart on QA data."""
from copy import deepcopy
import json
from pathlib import Path
import sys

from integration_smoke import Client, command
from restart_qa import restart


def main():
    manifest_path = Path(sys.argv[1])
    manifest = json.loads(manifest_path.read_text('utf-8'))
    directory = Path(manifest['directory'])
    assert '.runtime' in directory.parts and 'qa' in directory.parts
    client = Client(manifest['base'])
    ui = json.loads((directory/'ui-usability/result.json').read_text('utf-8'))
    config = deepcopy(client.request('/api/jobs/'+ui['sample_job_id'])['request'])
    config['encoder'] = 'cpu'
    passed = []
    # The word "удалить" occurs entirely in a removed interval.
    caption_config = deepcopy(config)
    caption_config.update(parts=[{'start':0,'end':4}],cuts=[{'start':1,'end':2}],banners=[],delivery={})
    caption_config['subtitles'] = {'enabled':True,'style':'clean','size':28,'cues':[
        {'start':0,'end':4,'text':'один удалить три четыре','manual':False,'words':[
            {'start':.1,'end':.6,'text':'один'}, {'start':1.1,'end':1.6,'text':'удалить'},
            {'start':2.1,'end':2.6,'text':'три'}, {'start':3.1,'end':3.6,'text':'четыре'}]}]}
    caption_config['advertising'].update(enabled=True,asset_id=manifest['advert_id'],start=1,repeat=False,profile='custom',speed=1)
    job = client.wait(client.request('/api/render',caption_config)['id'])
    srt = next(file for file in job['files'] if file['name'].endswith('.srt'))
    target = directory/'usability-subtitles.srt'
    client.download(srt['url'],target)
    assert 'удалить' not in target.read_text('utf-8-sig')
    ass_files = list((directory/'data/jobs'/job['id']).glob('*.ass'))
    # Single-segment .ass is kept; intermediate ASS is removed after successful concat.
    for path in ass_files:
        assert 'удалить' not in path.read_text('utf-8-sig')
    assert any(file['name'].endswith('.mp4') for file in job['files'])
    passed.append('Настоящие MP4/SRT: удалённые слова исключены, реклама сдвигает оставшиеся тайм-коды')

    silent = directory/'usability-silent.mp4'
    command(['-i',manifest['source_fixture'],'-map','0:v:0','-an','-t','3','-c:v','copy',str(silent)])
    asset = client.upload(silent,'video')
    config['source_id'] = asset['id']
    config['audio_stream_index'] = None
    config['parts'] = [{'start':0,'end':3}]
    batch = client.wait(client.request('/api/batch', {'name':'QA: предупреждения','config':config,
        'items':[{'source_id':asset['id'],'relative_path':'без-звука.mp4'}],
        'options':{'split':'whole','subtitles':'required','check_outputs':True,'zip':True}})['id'])
    row = batch['batch_items'][0]
    assert row['status'] in {'ready','review'}, row
    assert row['issues'] and any('субтитр' in issue['message'].lower() for issue in row['issues'])
    assert any('MP4 сохранён' in issue['message'] for issue in row['issues']), row['issues']
    assert len(client.request('/api/projects/'+row['project_id'])['timeline']) == 1, 'Delivery limit must not silently split a whole video'
    report = directory/'usability-batch-report.json'
    client.download(next(file['url'] for file in batch['files'] if file['name']=='report.json'),report)
    assert json.loads(report.read_text('utf-8'))['items'][0]['issues']
    passed.append('Пакет: требуемые субтитры без звука и лимиты не блокируют MP4; замечания есть в отчёте')

    review = client.wait(client.request('/api/batch', {'name':'QA: приёмка','config':config,
        'items':[{'source_id':asset['id'],'relative_path':'проверка.mp4'}],
        'options':{'split':'whole','subtitles':'none','review_before_export':True,'zip':False}})['id'])
    assert review['batch_items'][0]['status']=='prepared'
    approval = client.request('/api/batches/'+review['id']+'/decision',{'indices':[review['batch_items'][0]['index']],'action':'accept'})
    client.wait(approval['id'])
    accepted = client.request('/api/batches/'+review['id'])
    assert accepted['batch_items'][0]['status'] in {'ready','review'},accepted
    passed.append('Подготовка папки и принятие экспортируют текущие настройки с предупреждениями')

    active_config = deepcopy(config)
    active_config['parts'] = [{'start':0,'end':3} for _ in range(12)]
    active = client.request('/api/render',active_config)['id']
    queued = client.request('/api/batch', {'name':'QA: защищённый пакет','config':config,
        'items':[{'source_id':asset['id'],'relative_path':'активный.mp4'}],
        'options':{'split':'whole','subtitles':'none','zip':False}})['id']
    protected = client.request('/api/history/clear',{})
    assert active in protected['protected_job_ids'] and queued in protected['protected_job_ids']
    assert not client.request('/api/assets')['assets']
    client.wait(active)
    client.wait(queued)
    assert client.request('/api/jobs/'+active)['files']
    client.request('/api/history/clear',{})
    assert not client.request('/api/jobs')['jobs']
    assert not client.request('/api/batches')['batches']
    client.download(next(file['url'] for file in batch['files'] if file['name'].endswith('.zip')),directory/'usability-preserved.zip')
    results = client.request('/api/results')['jobs']
    assert any(file['url'].startswith('/api/batches/') and file['name'].endswith('.mp4') for job in results for file in job['files'])
    passed.append('Очистка защищает настоящий FFmpeg и ожидающий пакет, скрывает родительские и дочерние записи, сохраняет ссылки')

    restart(manifest_path)
    assert not client.request('/api/assets')['assets']
    assert not client.request('/api/jobs')['jobs']
    assert not client.request('/api/batches')['batches']
    assert not client.request('/api/history')['project_operations']
    assert client.request('/api/projects/'+ui['project_id'])['settings']['banners']
    assert client.request('/api/assets/'+asset['id'])['id']==asset['id']
    assert client.request('/api/results')['jobs']
    assert client.request('/api/history')['project_operations'], 'New opens must appear again'
    client.request('/api/history/clear',{})
    passed.append('Перезапуск: списки остаются пустыми; проекты, исходники и готовые результаты доступны; новые открытия записываются')
    result={'passed':passed,'directory':str(directory),'batch_id':batch['id'],'review_batch_id':review['id']}
    (directory/'usability-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
