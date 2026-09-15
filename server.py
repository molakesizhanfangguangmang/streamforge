#!/usr/bin/env python3
import json, os, re, shutil, signal, subprocess, threading, time, uuid, zipfile, tempfile, io
from urllib.request import Request, urlopen
from urllib.parse import urlencode
try:
    import qrcode
except ImportError:
    qrcode = None
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(os.environ.get('STREAMFORGE_DATA', '/data'))
DOWNLOADS = Path(os.environ.get('STREAMFORGE_DOWNLOADS', '/downloads'))
STATIC = Path(__file__).parent
PLUGINS = ROOT / 'plugins'
BACKUPS = ROOT / 'backups'
for d in (ROOT, DOWNLOADS, PLUGINS, BACKUPS): d.mkdir(parents=True, exist_ok=True)
(ROOT / 'cookies').mkdir(parents=True, exist_ok=True)
STATE = ROOT / 'state.json'
JOBS_STATE = ROOT / 'jobs.json'
LOCK = threading.RLock()
processes = {}
config = {'concurrency': 2, 'metadata': False, 'queue_mode': False,
          'cookies': {'bilibili': str(ROOT / 'cookies' / 'bilibili.txt'), 'youtube': ''},
          'proxies': {'bilibili': '', 'youtube': ''}}
QR_SESSIONS = {}
jobs = {}
NODE_MODE = os.environ.get('STREAMFORGE_NODE', 'none')

def node_runtime():
    for p in ('/usr/local/bin/node', '/opt/node/bin/node', '/usr/bin/node'):
        if Path(p).is_file() and os.access(p, os.X_OK): return p
    return ''
NODE_PATH = node_runtime()

def load_json(path, default):
    try: return json.loads(path.read_text()) if path.exists() else default
    except Exception: return default
config.update(load_json(STATE, {}))
jobs.update({j['id']: j for j in load_json(JOBS_STATE, []) if isinstance(j, dict) and 'id' in j})

def atomic_json(path, value):
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    tmp.replace(path)

def save_config(): atomic_json(STATE, config)
def save_jobs():
    with LOCK: atomic_json(JOBS_STATE, list(jobs.values()))

def response(h, code, value):
    raw = json.dumps(value, ensure_ascii=False).encode()
    h.send_response(code); h.send_header('Content-Type', 'application/json; charset=utf-8')
    h.send_header('Content-Length', str(len(raw))); h.end_headers(); h.wfile.write(raw)

def clean_name(s): return re.sub(r'[/\\:*?"<>|\x00-\x1f]', '_', s).strip()[:160] or 'untitled'
def command_version(command):
    try: return subprocess.run(command, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception: return ''

def bilibili_request(url):
    req = Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})
    return urlopen(req, timeout=20)

def cookie_file_from_headers(headers):
    values = headers.get_all('Set-Cookie') or []
    pairs = {}
    for value in values:
        part = value.split(';', 1)[0]
        if '=' in part:
            key, val = part.split('=', 1); pairs[key] = val
    if not pairs: return 0
    target = ROOT / 'cookies' / 'bilibili.txt'; lines = ['# Netscape HTTP Cookie File']
    for key, val in sorted(pairs.items()):
        lines.append(f'.bilibili.com\\tTRUE\\t/\\tTRUE\\t0\\t{key}\\t{val}')
    target.write_text('\\n'.join(lines) + '\\n'); config.setdefault('cookies', {})['bilibili'] = str(target); save_config(); return len(pairs)

def qr_start():
    with bilibili_request('https://passport.bilibili.com/x/passport-login/web/qrcode/generate') as r:
        data = json.loads(r.read())['data']
    sid = uuid.uuid4().hex; QR_SESSIONS[sid] = {'key': data['qrcode_key'], 'url': data['url'], 'created': time.time()}
    return {'session': sid, 'url': data['url'], 'expires_in': 180}

def qr_image(session):
    item = QR_SESSIONS.get(session)
    if not item or qrcode is None: return None
    image = qrcode.make(item['url']); output = io.BytesIO(); image.save(output, format='PNG'); return output.getvalue()
def qr_poll(session):

    item = QR_SESSIONS.get(session)
    if not item: return {'state': 'missing'}
    if time.time() - item['created'] > 190: return {'state': 'expired'}
    url = 'https://passport.bilibili.com/x/passport-login/web/qrcode/poll?' + urlencode({'qrcode_key': item['key'], 'source': 'main_web'})
    with bilibili_request(url) as r:
        body = json.loads(r.read()); code = body.get('data', {}).get('code')
        if code == 0:
            count = cookie_file_from_headers(r.headers); QR_SESSIONS.pop(session, None)
            return {'state': 'success', 'cookies': count}
        if code == 86038: return {'state': 'scanned'}
        if code == 86090: return {'state': 'expired'}
        return {'state': 'waiting'}


def ytdlp_args(args, platform='bilibili'):
    cmd = ['yt-dlp', '--no-warnings', '--newline']
    if NODE_PATH: cmd += ['--js-runtimes', f'node:{NODE_PATH}']
    cookie = config.get('cookies', {}).get(platform, '')
    proxy = config.get('proxies', {}).get(platform, '')
    if cookie: cmd += ['--cookies', cookie]
    if proxy: cmd += ['--proxy', proxy]
    return cmd + args

def formats(info):
    out = []
    for f in info.get('formats', []):
        has_video = f.get('vcodec') not in (None, 'none')
        has_audio = f.get('acodec') not in (None, 'none')
        item = {'id': f.get('format_id'), 'ext': f.get('ext'),
          'resolution': f.get('resolution') or (f"{f.get('width')}x{f.get('height')}" if f.get('width') else None),
          'width': f.get('width'), 'height': f.get('height'), 'fps': f.get('fps'),
          'vcodec': f.get('vcodec'), 'acodec': f.get('acodec'), 'abr': f.get('abr'),
          'vbr': f.get('vbr'), 'tbr': f.get('tbr'),
          'size': f.get('filesize') or f.get('filesize_approx'), 'dynamic_range': f.get('dynamic_range'),
          'audio_channels': f.get('audio_channels'), 'asr': f.get('asr'),
          'protocol': f.get('protocol'), 'kind': 'video' if has_video else 'audio' if has_audio else 'other'}
        if item['kind'] in ('video', 'audio'): out.append(item)
    return {'title': info.get('title'), 'id': info.get('id'), 'uploader': info.get('uploader'),
      'thumbnail': info.get('thumbnail'), 'webpage_url': info.get('webpage_url'),
      'duration': info.get('duration'), 'upload_date': info.get('upload_date'),
      'description': info.get('description'), 'formats': out}

def run_download(job):
    jid = job['id']; job['status'] = 'running'; job['started_at'] = time.time(); save_jobs()
    try:
        folder = DOWNLOADS / clean_name(job.get('title') or jid); folder.mkdir(parents=True, exist_ok=True)
        output = str(folder / '%(title)s.%(ext)s')
        args = ['-f', job['format'], '-o', output]
        if job.get('metadata'): args += ['--write-info-json']
        args += [job['url']]
        proc = subprocess.Popen(ytdlp_args(args, job.get('platform', 'bilibili')), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        with LOCK: processes[jid] = proc
        for line in proc.stdout:
            with LOCK: job['log'] = line.strip()[-800:]; job['updated_at'] = time.time()
            match = re.search(r'\[download\]\s+(\d+(?:\.\d+)?)%', line)
            if match: job['percent'] = float(match.group(1))
            if job.get('cancel_requested'): proc.terminate(); break
            save_jobs()
        rc = proc.wait()
        with LOCK:
            job['status'] = 'cancelled' if job.get('cancel_requested') else 'done' if rc == 0 else 'error'
            job['returncode'] = rc
        save_jobs()
    except Exception as exc:
        job['status'] = 'error'; job['error'] = str(exc); save_jobs()
    finally:
        with LOCK: processes.pop(jid, None)

def scheduler():
    while True:
        with LOCK:
            active = sum(j.get('status') == 'running' for j in jobs.values())
            waiting = [j for j in jobs.values() if j.get('status') == 'queued']
            limit = max(1, int(config.get('concurrency', 2)))
        for job in waiting[:max(0, limit - active)]:
            threading.Thread(target=run_download, args=(job,), daemon=True).start()
        time.sleep(.5)
threading.Thread(target=scheduler, daemon=True).start()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def body(self): return json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/health':
            return response(self, 200, {'ok': True, 'version': '0.1.0', 'node': {'mode': NODE_MODE, 'path': NODE_PATH, 'version': command_version([NODE_PATH, '--version']) if NODE_PATH else ''}, 'ytdlp': command_version(['yt-dlp', '--version']), 'ffmpeg': command_version(['ffmpeg', '-version']).splitlines()[0] if command_version(['ffmpeg', '-version']) else ''})
        if path == '/api/jobs':
            with LOCK: return response(self, 200, {'jobs': list(jobs.values()), 'config': config})
        if path == '/api/config': return response(self, 200, config)
        if path == '/api/auth': return response(self, 200, {'bilibili': Path(config.get('cookies', {}).get('bilibili', '')).is_file(), 'youtube': bool(config.get('cookies', {}).get('youtube')), 'proxies': {k: bool(v) for k, v in config.get('proxies', {}).items()}})
        if path.startswith('/api/bilibili/qr/status/'):
            return response(self, 200, qr_poll(path.rsplit('/', 1)[-1]))
        if path.startswith('/api/bilibili/qr/image/'):
            data = qr_image(path.rsplit('/', 1)[-1])
            if not data: return response(self, 404, {'error': 'qr unavailable'})
            self.send_response(200); self.send_header('Content-Type', 'image/png'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path == '/api/plugins':
            return response(self, 200, [{'id': p.name, 'persistent': (p / '.persistent').exists(), 'enabled': not (p / '.disabled').exists()} for p in PLUGINS.iterdir() if p.is_dir()])
        if path == '/api/backups': return response(self, 200, [{'name': p.name, 'size': p.stat().st_size} for p in sorted(BACKUPS.glob('*.zip'))])
        if path == '/': path = '/index.html'
        file = STATIC / path.lstrip('/')
        if file.is_file():
            data = file.read_bytes(); kind = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css'}.get(file.suffix, 'application/octet-stream')
            self.send_response(200); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
        response(self, 404, {'error': 'not found'})
    def do_POST(self):
        path = urlparse(self.path).path; body = self.body()
        if path == '/api/inspect':
            try:
                result = subprocess.run(ytdlp_args(['--dump-single-json', '--skip-download', body['url']], body.get('platform', 'bilibili')), capture_output=True, text=True, timeout=180)
                return response(self, 200, formats(json.loads(result.stdout))) if result.returncode == 0 else response(self, 422, {'error': result.stderr[-1500:]})
            except Exception as exc: return response(self, 500, {'error': str(exc)})
        if path == '/api/jobs':
            if not body.get('url') or not body.get('format'): return response(self, 400, {'error': 'url and format are required'})
            job = {'id': uuid.uuid4().hex[:12], 'url': body['url'], 'format': body['format'], 'platform': body.get('platform', 'bilibili'), 'title': body.get('title', 'untitled'), 'metadata': bool(body.get('metadata')), 'status': 'queued', 'percent': 0, 'created_at': time.time(), 'log': ''}
            with LOCK: jobs[job['id']] = job
            save_jobs(); return response(self, 202, job)
        if path == '/api/config':
            with LOCK: config.update({k: v for k, v in body.items() if k in ('concurrency', 'metadata', 'queue_mode', 'cookies', 'proxies')}); save_config()
            return response(self, 200, config)
        if path == '/api/bilibili/qr/start':
            try: return response(self, 200, qr_start())
            except Exception as exc: return response(self, 502, {'error': str(exc)})
        if path == '/api/auth':
            with LOCK:
                if isinstance(body.get('cookies'), dict): config.setdefault('cookies', {}).update(body['cookies'])
                if isinstance(body.get('proxies'), dict): config.setdefault('proxies', {}).update(body['proxies'])
                save_config()
            return response(self, 200, {'ok': True, 'restart_required': False})
        if path == '/api/restart':
            response(self, 202, {'ok': True, 'message': '服务正在重启'})
            threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
            return
        if path == '/api/plugins/persist':
            ids = body.get('plugins', [])
            for pid in ids:
                p = PLUGINS / clean_name(pid)
                if p.is_dir(): (p / '.persistent').touch()
            return response(self, 200, {'ok': True, 'persisted': ids, 'restart_required': False})
        if path == '/api/backup':
            selected = set(body.get('include', ['config', 'cookies', 'proxies', 'plugins']))
            name = 'backup-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:4] + '.zip'; target = BACKUPS / name
            with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
                if 'config' in selected: z.writestr('config.json', json.dumps(config, ensure_ascii=False, indent=2))
                if 'cookies' in selected:
                    for key, value in config.get('cookies', {}).items(): z.writestr(f'cookies/{key}.txt', value)
                if 'proxies' in selected: z.writestr('proxies.json', json.dumps(config.get('proxies', {}), ensure_ascii=False, indent=2))
                if 'queue' in selected: z.writestr('jobs.json', json.dumps(list(jobs.values()), ensure_ascii=False, indent=2))
                if 'plugins' in selected:
                    for p in PLUGINS.rglob('*'):
                        if p.is_file(): z.write(p, 'plugins/' + str(p.relative_to(PLUGINS)))
            return response(self, 201, {'ok': True, 'name': name, 'restart_required': False})
        if path == '/api/restore':
            name = body.get('name', ''); selected = set(body.get('include', [])); source = BACKUPS / Path(name).name
            if not source.is_file(): return response(self, 404, {'error': 'backup not found'})
            with zipfile.ZipFile(source) as z:
                if 'config' in selected and 'config.json' in z.namelist(): config.update(json.loads(z.read('config.json'))); save_config()
                if 'queue' in selected and 'jobs.json' in z.namelist(): jobs.clear(); jobs.update({j['id']: j for j in json.loads(z.read('jobs.json'))}); save_jobs()
                if 'plugins' in selected:
                    for item in z.namelist():
                        if item.startswith('plugins/') and not item.endswith('/'): z.extract(item, ROOT)
            return response(self, 200, {'ok': True, 'restart_required': bool({'config', 'plugins'} & selected)})
        response(self, 404, {'error': 'not found'})
    def do_PATCH(self):
        m = re.match(r'/api/jobs/([^/]+)/(pause|resume)$', urlparse(self.path).path)
        if not m: return response(self, 404, {'error': 'not found'})
        jid, action = m.groups(); job = jobs.get(jid)
        if not job: return response(self, 404, {'error': 'job not found'})
        proc = processes.get(jid)
        if not proc: return response(self, 409, {'error': 'job is not running'})
        proc.send_signal(signal.SIGSTOP if action == 'pause' else signal.SIGCONT); job['status'] = 'paused' if action == 'pause' else 'running'; save_jobs(); return response(self, 200, job)
    def do_DELETE(self):
        m = re.match(r'/api/jobs/([^/]+)$', urlparse(self.path).path)
        if not m: return response(self, 404, {'error': 'not found'})
        job = jobs.get(m.group(1))
        if not job: return response(self, 404, {'error': 'job not found'})
        if job.get('status') in ('queued', 'running', 'paused'): job['cancel_requested'] = True; job['status'] = 'cancelled' if job['status'] == 'queued' else job['status']; save_jobs()
        return response(self, 200, {'ok': True})

if __name__ == '__main__': ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8081'))), Handler).serve_forever()
