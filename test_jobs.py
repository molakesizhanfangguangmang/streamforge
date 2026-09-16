"""任务状态与 jobs.json 的回归测试。

锁住两件事：
  1. 启动时要把上次被中断的任务（容器重启留下的 running / paused）放回队列 ——
     否则 scheduler 只挑 queued、并发额度又被它们占着，僵尸任务会让下载停摆；
  2. jobs.json / state.json 损坏不能让服务起不来。
"""
import json, os, tempfile
from pathlib import Path
from testkit import Checks, load_server

checks = Checks()

with tempfile.TemporaryDirectory() as tmp:
    data, downloads = Path(tmp) / 'data', Path(tmp) / 'downloads'
    data.mkdir()

    def job(jid, status, **extra):
        return {'id': jid, 'url': 'https://example.com/' + jid, 'format': '1+2', 'platform': 'bilibili',
                'title': jid, 'status': status, 'percent': 42, 'created_at': 0, 'log': '旧日志', **extra}

    (data / 'jobs.json').write_text(json.dumps([
        job('interrupted', 'running'),
        job('paused-one', 'paused'),
        job('cancel-pending', 'running', cancel_requested=True),
        job('waiting-one', 'waiting'),
        job('queued-one', 'queued'),
        job('done-one', 'done'),
    ], ensure_ascii=False))
    (data / 'state.json').write_text('{ 这不是 JSON')

    ns = load_server(data, downloads)
    jobs = ns['jobs']

    checks.check('中断的 running 放回队列', jobs['interrupted']['status'] == 'queued', jobs['interrupted']['status'])
    checks.check('paused 也放回队列', jobs['paused-one']['status'] == 'queued', jobs['paused-one']['status'])
    checks.check('请求过取消的落成 cancelled', jobs['cancel-pending']['status'] == 'cancelled', jobs['cancel-pending']['status'])
    checks.check('waiting 不动（等用户按开始）', jobs['waiting-one']['status'] == 'waiting', jobs['waiting-one']['status'])
    checks.check('queued 不动', jobs['queued-one']['status'] == 'queued')
    checks.check('done 不动', jobs['done-one']['status'] == 'done')
    checks.check('重排的任务留下说明', '[服务重启]' in jobs['interrupted']['log'], jobs['interrupted']['log'])
    checks.check('重排计数可查', ns['RECONCILED_JOBS'] == 3, ns['RECONCILED_JOBS'])
    checks.check('重排结果落盘', json.loads((data / 'jobs.json').read_text())[0]['status'] == 'queued')
    checks.check('state.json 损坏不影响启动', isinstance(ns['config'], dict) and ns['config'].get('concurrency') == 2, ns['config'].get('concurrency'))

    # --- 二次启动不应再动已经排好的任务 ---
    ns2 = load_server(data, downloads)
    checks.check('再启动一次无重排', ns2['RECONCILED_JOBS'] == 0, ns2['RECONCILED_JOBS'])
    checks.check('再启动后 status 不变', ns2['jobs']['interrupted']['status'] == 'queued')

    # --- 写盘：原子替换，不留 .tmp ---
    ns2['save_jobs']()
    leftovers = sorted(p.name for p in data.iterdir() if p.name.endswith('.tmp'))
    checks.check('写 jobs.json 不留临时文件', leftovers == [], leftovers)

    # --- 空 jobs.json 也要能起 ---
    (data / 'jobs.json').write_text('')
    ns3 = load_server(data, downloads)
    checks.check('jobs.json 为空不影响启动', ns3['jobs'] == {} and ns3['RECONCILED_JOBS'] == 0)

    # --- 进度节流：一秒钟里几十行 yt-dlp 输出不应落盘几十次 ---
    ns4 = load_server(Path(tmp) / 'run' / 'data', Path(tmp) / 'run' / 'downloads')
    lines = ['[download] %5.1f%% of 1.00MiB at 1.00MiB/s' % (i / 2) for i in range(200)]

    class FakeProcess:
        def __init__(self): self.stdout = iter(lines)
        def wait(self): return 0
        def terminate(self): pass

    class FakeSubprocess:
        PIPE, STDOUT = -1, -2
        @staticmethod
        def Popen(*_args, **_kwargs): return FakeProcess()

    writes = []
    ns4['subprocess'] = FakeSubprocess
    ns4['atomic_json'] = lambda path, value: writes.append(value)
    job = {'id': 'throttle', 'url': 'https://example.com/x', 'format': '1+2', 'platform': 'bilibili',
           'title': '节流', 'metadata': False, 'status': 'queued', 'percent': 0, 'log': ''}
    ns4['jobs'][job['id']] = job
    ns4['run_download'](job)
    checks.check('200 行输出只落盘个位数次', 2 <= len(writes) <= 5, len(writes))
    checks.check('进度仍记到最后一行', job['percent'] == 99.5, job['percent'])
    checks.check('跑完状态为 done', job['status'] == 'done', job['status'])
    checks.check('进程句柄已释放', ns4['processes'] == {}, ns4['processes'])

    # --- 构建戳：只有镜像里带的（CI --build-arg）才算可查，本地构建与热部署一律「未知」 ---
    os.environ['STREAMFORGE_BUILD_SHA'] = 'abcdef1234567890abcdef1234567890abcdef12'
    os.environ['STREAMFORGE_BUILD_TIME'] = '2026-09-16 15:27:00Z'
    os.environ['STREAMFORGE_VERSION'] = '1.0.7'
    ns5 = load_server(Path(tmp) / 'stamp' / 'data', Path(tmp) / 'stamp' / 'downloads')
    checks.check('CI 构建戳可查（取前 12 位）', ns5['build_stamp']() == {'sha': 'abcdef123456', 'time': '2026-09-16 15:27:00Z', 'tracked': True}, ns5['build_stamp']())
    checks.check('版本号读环境变量', ns5['APP_VERSION'] == '1.0.7', ns5['APP_VERSION'])
    for name in ('STREAMFORGE_BUILD_SHA', 'STREAMFORGE_BUILD_TIME', 'STREAMFORGE_VERSION'):
        os.environ.pop(name, None)
    ns6 = load_server(Path(tmp) / 'local' / 'data', Path(tmp) / 'local' / 'downloads')
    checks.check('没带构建戳时如实报未知', ns6['build_stamp']() == {'sha': '', 'time': '', 'tracked': False}, ns6['build_stamp']())
    os.environ['STREAMFORGE_BUILD_SHA'] = 'unknown'
    ns7 = load_server(Path(tmp) / 'unknown' / 'data', Path(tmp) / 'unknown' / 'downloads')
    checks.check('Dockerfile 默认的 unknown 不算构建戳', ns7['build_stamp']()['tracked'] is False, ns7['build_stamp']())
    os.environ.pop('STREAMFORGE_BUILD_SHA', None)

checks.done()
