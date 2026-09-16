#!/usr/bin/env python3
import base64, binascii, collections, html, json, os, re, shutil, signal, socket, subprocess, threading, time, uuid, zipfile, tempfile, io
from xml.etree.ElementTree import Element, SubElement, ElementTree, ParseError, fromstring
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor, ProxyHandler, HTTPError, URLError
from http.cookiejar import MozillaCookieJar, CookieJar
from stream_metadata import enrich_formats, probe_best_audio
from urllib.parse import urlencode, parse_qs
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
PERSISTENT_PLUGINS = PLUGINS / 'persistent'
RUNTIME_PLUGINS = PLUGINS / 'runtime'
BACKUPS = ROOT / 'backups'
for d in (ROOT, DOWNLOADS, PLUGINS, PERSISTENT_PLUGINS, RUNTIME_PLUGINS, BACKUPS): d.mkdir(parents=True, exist_ok=True)
(ROOT / 'cookies').mkdir(parents=True, exist_ok=True)
STATE = ROOT / 'state.json'
JOBS_STATE = ROOT / 'jobs.json'
BILIBILI_AUTH = ROOT / 'bilibili-auth.json'
# Public key used by Bilibili's web cookie-refresh correspondence protocol.
BILIBILI_REFRESH_PUBLIC_KEY = '''-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----'''
LOCK = threading.RLock()
processes = {}
config = {'concurrency': 2, 'metadata': False, 'thumbnail_jpg': True, 'queue_mode': False, 'download_path': '/downloads',
          'cookies': {'bilibili': str(ROOT / 'cookies' / 'bilibili.txt'), 'youtube': ''},
          'proxies': {'bilibili': '', 'youtube': ''},
          'update_check_days': 7, 'update_cache': {}}
QR_SESSIONS = {}
jobs = {}
LOGS = []
def log_event(kind, message, level='info'):
    LOGS.append({'ts': time.time(), 'time': time.strftime('%H:%M:%S'), 'kind': kind, 'level': level, 'message': message})
    del LOGS[:-300]
log_event('system', 'Streamforge 服务已启动')
APP_VERSION = os.environ.get('STREAMFORGE_VERSION', '1.0.0')
APP_BUILD_SHA = os.environ.get('STREAMFORGE_BUILD_SHA', '').strip()
APP_BUILD_TIME = os.environ.get('STREAMFORGE_BUILD_TIME', '').strip()

def build_stamp():
    """报出跑着的这份代码是哪个构建。

    镜像由 CI 构建时带 --build-arg，本地 docker build 与 docker cp 热部署都不带；
    所以 tracked 为假就说明这份代码来路不可查，界面照实说，不假装是某个版本。
    """
    sha = APP_BUILD_SHA[:12]
    tracked = bool(sha) and sha.lower() != 'unknown'
    return {'sha': sha if tracked else '', 'time': APP_BUILD_TIME if tracked else '', 'tracked': tracked}


NODE_MODE = os.environ.get('STREAMFORGE_NODE', 'none')
UPDATE_AGENT_URL = os.environ.get('STREAMFORGE_UPDATE_AGENT_URL', '')
UPDATE_AGENT_TOKEN_FILE = os.environ.get('STREAMFORGE_UPDATE_AGENT_TOKEN_FILE', '')

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

def reconcile_jobs_on_startup():
    """收拾上次运行留下的中断任务。

    容器重启时下载进程全没了，jobs.json 里却还留着 running / paused。不收拾的后果：
    scheduler 只挑 queued，而并发额度又把这些行当成在跑的算 —— 僵尸任务白占下载位，
    前端还一直显示「正在下载」，两条僵尸就等于下载停摆。这里把它们放回队列
    （yt-dlp 接着 .part 续传）；已经请求过取消的直接落成 cancelled。
    """
    stale = [job for job in jobs.values() if job.get('status') in ('running', 'paused')]
    if not stale: return 0
    for job in stale:
        job['status'] = 'cancelled' if job.get('cancel_requested') else 'queued'
        job['log'] = ((job.get('log') or '') + '\n[服务重启] 上次运行被中断，已重新排队').strip()
        job['updated_at'] = time.time()
    save_jobs()
    log_event('download', '服务重启：%d 个中断的任务已重新排队' % len(stale))
    return len(stale)

RECONCILED_JOBS = 0
try:
    RECONCILED_JOBS = reconcile_jobs_on_startup()
except Exception as exc:  # 对账失败不该拦住服务启动
    log_event('download', '启动状态对账失败：%s' % exc, 'error')

def response(h, code, value):
    raw = json.dumps(value, ensure_ascii=False).encode()
    h.send_response(code); h.send_header('Content-Type', 'application/json; charset=utf-8')
    h.send_header('Content-Length', str(len(raw))); h.end_headers(); h.wfile.write(raw)

def clean_name(s): return re.sub(r'[/\\:*?"<>|\x00-\x1f]', '_', s).strip()[:160] or 'untitled'
def command_version(command):
    try: return subprocess.run(command, capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception: return ''

PLUGIN_MAX_BYTES = 10 * 1024 * 1024
PLUGIN_ID_RE = re.compile(r'^[a-z0-9][a-z0-9._-]{0,63}$')

def safe_plugin_id(value):
    if not isinstance(value, str): raise ValueError('plugin name is required')
    slug = re.sub(r'[^a-z0-9._-]+', '-', value.strip().lower()).strip('.-')
    if not PLUGIN_ID_RE.fullmatch(slug): raise ValueError('plugin name must contain letters, numbers, dots, dashes, or underscores')
    return slug

def validate_plugin_zip(data):
    """Return validated ZIP metadata without writing any plugin files."""
    try: raw = base64.b64decode(data, validate=True)
    except (TypeError, ValueError, binascii.Error): raise ValueError('zip_base64 must be valid base64')
    if len(raw) > PLUGIN_MAX_BYTES: raise ValueError('plugin ZIP exceeds 10 MiB')
    try: archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile: raise ValueError('plugin file is not a valid ZIP archive')
    try:
        python_files = []
        manifest = {}
        for info in archive.infolist():
            name = info.filename.replace('\\', '/')
            parts = Path(name).parts
            if not name or name.startswith('/') or re.match(r'^[A-Za-z]:', name) or '..' in parts:
                raise ValueError('plugin ZIP contains an unsafe path')
            if (info.external_attr >> 16) & 0o170000 == 0o120000: raise ValueError('plugin ZIP contains a symbolic link')
            if not info.is_dir() and name.endswith('.py'): python_files.append(name)
            if not info.is_dir() and Path(name).name == 'plugin.json':
                try:
                    candidate = json.loads(archive.read(info).decode('utf-8'))
                    if isinstance(candidate, dict) and not manifest: manifest = candidate
                except (UnicodeDecodeError, json.JSONDecodeError): raise ValueError('plugin.json must contain a JSON object')
        if not python_files: raise ValueError('plugin ZIP must contain at least one .py file')
        return raw, manifest
    finally: archive.close()

def plugin_info(directory, persistent):
    manifest = {}
    for candidate in directory.rglob('plugin.json'):
        try:
            data = json.loads(candidate.read_text(encoding='utf-8'))
            if isinstance(data, dict): manifest = data; break
        except (OSError, UnicodeDecodeError, json.JSONDecodeError): pass
    return {'id': directory.name, 'name': str(manifest.get('name') or directory.name), 'version': str(manifest.get('version') or ''),
            'persistent': persistent, 'enabled': not (directory / '.disabled').exists(), 'path': str(directory)}

def list_plugins():
    items = []
    for persistent, root in ((True, PERSISTENT_PLUGINS), (False, RUNTIME_PLUGINS)):
        if root.is_dir(): items.extend(plugin_info(path, persistent) for path in root.iterdir() if path.is_dir())
    return sorted(items, key=lambda item: (item['id'], not item['persistent']))

def find_plugin(plugin_id):
    for persistent, root in ((True, PERSISTENT_PLUGINS), (False, RUNTIME_PLUGINS)):
        path = root / plugin_id
        if path.is_dir(): return path, persistent
    return None, None

def install_plugin(name, zip_base64, persistent):
    plugin_id = safe_plugin_id(name)
    raw, manifest = validate_plugin_zip(zip_base64)
    root = PERSISTENT_PLUGINS if persistent else RUNTIME_PLUGINS
    root.mkdir(parents=True, exist_ok=True)
    target = root / plugin_id
    stage = Path(tempfile.mkdtemp(prefix='.install-', dir=root))
    backup = None
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive: archive.extractall(stage)
        with LOCK:
            if target.exists():
                backup = BACKUPS / ('plugin-' + plugin_id + '-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
                target.replace(backup)
            try: stage.replace(target)
            except Exception:
                if backup and backup.exists(): backup.replace(target)
                raise
        result = plugin_info(target, persistent)
        if manifest.get('name'): result['name'] = str(manifest['name'])
        if manifest.get('version') is not None: result['version'] = str(manifest['version'])
        return result
    finally:
        if stage.exists(): shutil.rmtree(stage)

def bili_opener(jar=None):
    handlers = [HTTPCookieProcessor(jar if jar is not None else CookieJar())]
    proxy = config.get('proxies', {}).get('bilibili')
    if proxy: handlers.append(ProxyHandler({'http': proxy, 'https': proxy}))
    return build_opener(*handlers)

def bilibili_request(url, jar=None, data=None):
    req = Request(url, data=data, headers={'User-Agent': 'Mozilla/5.0', 'Referer': 'https://www.bilibili.com/'})
    return bili_opener(jar).open(req, timeout=20)

def bilibili_cookie_path(): return ROOT / 'cookies' / 'bilibili.txt'
def load_bilibili_auth():
    data = load_json(BILIBILI_AUTH, {})
    return data if isinstance(data, dict) else {}
def bilibili_refresh_capable(): return bool(load_bilibili_auth().get('refresh_token'))
def save_bilibili_auth(refresh_token):
    if not isinstance(refresh_token, str) or not refresh_token:
        raise ValueError('B站未返回刷新令牌，请重新扫码登录')
    atomic_json(BILIBILI_AUTH, {'cookie_path': str(bilibili_cookie_path()), 'refresh_token': refresh_token})
def csrf_from_jar(jar):
    for cookie in jar:
        if cookie.name == 'bili_jct' and cookie.value: return cookie.value
    raise ValueError('Cookie 中没有 bili_jct，无法刷新登录状态')
def load_bilibili_jar():
    path = bilibili_cookie_path()
    if not path.is_file(): raise ValueError('未找到 B站 Cookie，请重新扫码登录')
    jar = MozillaCookieJar(str(path)); jar.load(ignore_discard=True, ignore_expires=True)
    if not any(c.name == 'SESSDATA' and c.value for c in jar): raise ValueError('Cookie 中没有有效登录凭据，请重新扫码')
    return jar
def refresh_correspond_path(timestamp=None):
    stamp = int(time.time() * 1000) if timestamp is None else int(timestamp)
    with tempfile.TemporaryDirectory() as directory:
        key = Path(directory) / 'bilibili-refresh.pem'; plain = Path(directory) / 'plain'; encrypted = Path(directory) / 'encrypted'
        key.write_text(BILIBILI_REFRESH_PUBLIC_KEY, encoding='ascii'); plain.write_text(f'refresh_{stamp}', encoding='ascii')
        result = subprocess.run(['openssl', 'pkeyutl', '-encrypt', '-pubin', '-inkey', str(key), '-in', str(plain), '-out', str(encrypted), '-pkeyopt', 'rsa_padding_mode:oaep', '-pkeyopt', 'rsa_oaep_md:sha256'], capture_output=True, text=True, timeout=10)
        if result.returncode: raise RuntimeError('无法生成 B站刷新校验路径')
        return encrypted.read_bytes().hex()

def save_bilibili_jar(jar, backup=True):
    if not any(c.name == 'SESSDATA' and c.value for c in jar):
        raise ValueError('扫码确认未返回登录 Cookie，请重新扫码')
    target = bilibili_cookie_path()
    if backup and target.is_file():
        shutil.copy2(target, BACKUPS / ('bilibili-cookie-' + uuid.uuid4().hex + '.txt'))
    saved = MozillaCookieJar(str(target))
    for cookie in jar:
        if cookie.domain.lstrip('.') == 'bilibili.com' or cookie.domain.endswith('.bilibili.com'):
            saved.set_cookie(cookie)
    temp = str(target) + '.tmp'
    saved.save(temp, ignore_discard=True, ignore_expires=True)
    os.replace(temp, target)
    config.setdefault('cookies', {})['bilibili'] = str(target)
    save_config()
    return len(saved)

def save_bilibili_text(cookie_text):
    if not isinstance(cookie_text, str) or not cookie_text.strip():
        raise ValueError('Cookie 内容不能为空')
    target = ROOT / 'cookies' / 'bilibili.txt'
    candidate = target.with_suffix('.candidate')
    candidate.write_text(cookie_text.rstrip() + '\n', encoding='utf-8')
    try:
        jar = MozillaCookieJar(str(candidate))
        jar.load(ignore_discard=True, ignore_expires=True)
        if not any(c.name == 'SESSDATA' and c.value for c in jar):
            raise ValueError('Cookie 不是包含 SESSDATA 的 Netscape 格式文件')
        if target.is_file():
            shutil.copy2(target, BACKUPS / ('bilibili-cookie-' + uuid.uuid4().hex + '.txt'))
        os.replace(candidate, target)
        atomic_json(BILIBILI_AUTH, {})
        config.setdefault('cookies', {})['bilibili'] = str(target)
        save_config()
    finally:
        candidate.unlink(missing_ok=True)
    return bilibili_auth()

YOUTUBE_LOGIN_COOKIE_NAMES = ('SID', 'SAPISID', 'APISID', 'HSID', 'SSID', 'LOGIN_INFO',
                              '__Secure-1PSID', '__Secure-3PSID', '__Secure-1PAPISID', '__Secure-3PAPISID')

def youtube_cookie_path(): return ROOT / 'cookies' / 'youtube.txt'

def normalize_cookie_text(cookie_text):
    """Accept a Netscape cookie file or a raw `name=value; name2=value2` header string."""
    if not isinstance(cookie_text, str) or not cookie_text.strip():
        raise ValueError('Cookie 内容不能为空')
    header = '# Netscape HTTP Cookie File'
    lines = [line.rstrip('\r') for line in cookie_text.splitlines()]
    # `#HttpOnly_` is part of the domain field, not a comment: keep those rows.
    rows = [line for line in lines if line.strip() and (line.startswith('#HttpOnly_') or not line.lstrip().startswith('#'))]
    names = [line.split('\t')[5].strip() for line in rows if len(line.split('\t')) >= 7 and line.split('\t')[5].strip()]
    if names:
        return '\n'.join([header] + rows) + '\n', names
    pairs = []
    for part in cookie_text.replace('\n', ';').split(';'):
        name, sep, value = part.partition('=')
        if sep and name.strip(): pairs.append((name.strip(), value.strip()))
    if not pairs:
        raise ValueError('无法识别 Cookie 内容，请粘贴 Netscape 格式文件或 name=value; name2=value2 形式的字符串')
    lines = [header] + ['\t'.join(['.youtube.com', 'TRUE', '/', 'TRUE', '1900000000', name, value]) for name, value in pairs]
    return '\n'.join(lines) + '\n', [name for name, _ in pairs]

def youtube_auth(count=None):
    path = Path(config.get('cookies', {}).get('youtube', ''))
    result = {'youtube': False, 'youtube_configured': path.is_file(), 'youtube_cookies': 0, 'youtube_error': None,
              'youtube_valid': 0, 'youtube_expired': 0, 'youtube_login_present': [], 'youtube_login_missing': list(YOUTUBE_LOGIN_COOKIE_NAMES),
              'youtube_nearest_expiry': None, 'youtube_saved_at': None}
    if not path.is_file():
        if config.get('cookies', {}).get('youtube'): result['youtube_error'] = 'Cookie 文件已丢失，请重新粘贴'
        return result
    try:
        jar = MozillaCookieJar(str(path))
        jar.load(ignore_discard=True, ignore_expires=True)
    except Exception:
        result['youtube_error'] = 'Cookie 文件无法解析，请重新粘贴'
        return result
    now = time.time()
    cookies = [c for c in jar if c.value]
    result['youtube_cookies'] = len(cookies) if count is None else count
    live = {c.name for c in cookies if not c.expires or c.expires > now}
    result['youtube_valid'] = sum(1 for c in cookies if not c.expires or c.expires > now)
    result['youtube_expired'] = len(cookies) - result['youtube_valid']
    names = {c.name for c in cookies}
    result['youtube_login_present'] = [n for n in YOUTUBE_LOGIN_COOKIE_NAMES if n in names]
    result['youtube_login_missing'] = [n for n in YOUTUBE_LOGIN_COOKIE_NAMES if n not in names]
    result['youtube_login_expired'] = [n for n in YOUTUBE_LOGIN_COOKIE_NAMES if n in names and n not in live]
    expiries = [c.expires for c in cookies if c.name in YOUTUBE_LOGIN_COOKIE_NAMES and c.expires and c.expires > now]
    result['youtube_nearest_expiry'] = min(expiries) if expiries else None
    try: result['youtube_saved_at'] = path.stat().st_mtime
    except OSError: pass
    result['youtube'] = bool(result['youtube_login_present']) and not result['youtube_login_expired']
    if not result['youtube'] and not result['youtube_error']:
        result['youtube_error'] = '登录凭据已过期，请重新粘贴 Cookie' if result['youtube_login_expired'] else 'Cookie 中没有登录凭据，yt-dlp 无法通过 YouTube 的登录校验'
    return result

def save_youtube_text(cookie_text):
    content, names = normalize_cookie_text(cookie_text)
    if not set(names) & set(YOUTUBE_LOGIN_COOKIE_NAMES):
        raise ValueError('Cookie 中没有 YouTube 登录凭据（缺少 SID、SAPISID 或 LOGIN_INFO 等字段）')
    target = youtube_cookie_path()
    candidate = target.with_suffix('.candidate')
    candidate.write_text(content, encoding='utf-8')
    try:
        jar = MozillaCookieJar(str(candidate))
        jar.load(ignore_discard=True, ignore_expires=True)
        count = len(jar)
        if not count: raise ValueError('Cookie 文件没有可用条目')
        if target.is_file():
            shutil.copy2(target, BACKUPS / ('youtube-cookie-' + uuid.uuid4().hex + '.txt'))
        os.replace(candidate, target)
        config.setdefault('cookies', {})['youtube'] = str(target)
        save_config()
    finally:
        candidate.unlink(missing_ok=True)
    return youtube_auth(count)

ERROR_HINTS = (
    ('sign in to confirm', 'YouTube 要求确认登录：需要有效 Cookie；仍需登录时通常是出口网络不可达，需要代理'),
    ('login_required', 'YouTube 要求登录：Cookie 缺失或已失效'),
    ('not a bot', 'YouTube 判定为机器人请求：需要有效 Cookie 与可用的出口网络'),
    ('failed to extract any player response', '无法取得播放信息：多为网络不可达或被拦截，检查代理'),
    ('unable to download webpage', 'yt-dlp 无法访问该网页：检查网络或代理设置'),
    ('failed to resolve', 'DNS 解析失败：检查网络或代理设置'),
    ('urlopen error', '网络连接失败：检查代理设置'),
    ('http error 403', '服务器返回 403：Cookie 可能已失效或触发风控'),
    ('no such file or directory: \'/data/cookies', 'Cookie 文件不存在：请重新在设置中保存 Cookie'),
)

def friendly_error(text):
    text = str(text or '').strip()
    lowered = text.lower()
    hints = [hint for key, hint in ERROR_HINTS if key in lowered]
    if not hints: return text[-1500:]
    return (text[-1200:] + '\n提示：' + '；'.join(dict.fromkeys(hints)))[-1500:]

_COOKIE_WARNED = set()

def warn_missing_cookie(platform, path):
    key = (platform, str(path))
    if key in _COOKIE_WARNED: return
    _COOKIE_WARNED.add(key)
    log_event('error', f'{platform} 已配置 Cookie 但文件不存在，本次调用不带 --cookies：{path}', 'error')

PROXY_PATTERN = re.compile(r'^(https?|socks5h?|socks4a?)://[^\s/]+$', re.I)

def normalize_proxy(raw, label):
    if raw is None: return ''
    if not isinstance(raw, str):
        raise ValueError(f'{label}代理地址需要形如 127.0.0.1:7890 或 http://127.0.0.1:7890，支持 HTTP、HTTPS、SOCKS5')
    text = raw.strip().strip('"\'').strip()
    if not text: return ''
    if '://' not in text: text = 'http://' + text
    text = text.rstrip('/')
    match = PROXY_PATTERN.match(text)
    if not match:
        raise ValueError(f'{label}代理地址需要形如 127.0.0.1:7890 或 http://127.0.0.1:7890，支持 HTTP、HTTPS、SOCKS5')
    return match.group(1).lower() + text[len(match.group(1)):]

def normalize_proxies(value):
    if not isinstance(value, dict): raise ValueError('代理设置格式不正确')
    result = {}
    for key, raw in value.items():
        if key not in ('bilibili', 'youtube'): continue
        result[key] = normalize_proxy(raw, 'YouTube' if key == 'youtube' else 'B站')
    return result

PROXY_TEST_TARGETS = {'youtube': 'https://www.youtube.com/generate_204', 'bilibili': 'https://www.bilibili.com/'}
PROXY_DEFAULT_PORTS = {'http': 80, 'https': 443}

def test_proxy(platform, normalized):
    """Check the saved value, the proxy port, then one request through the proxy."""
    label = 'YouTube' if platform == 'youtube' else 'B站'
    effective = config.get('proxies', {}).get(platform, '')
    if not normalized:
        return {'platform': platform, 'input': '', 'effective': effective, 'in_effect': not effective, 'ok': False,
                'steps': [{'name': '保存生效', 'ok': False, 'detail': f'没有填写{label}代理'}]}
    in_effect = normalized == effective
    report = {'platform': platform, 'input': normalized, 'effective': effective, 'in_effect': in_effect, 'ok': False,
              'steps': [{'name': '保存生效', 'ok': in_effect,
                         'detail': f'与服务端当前生效值一致（{effective}）' if in_effect else
                                   f'尚未保存，服务端当前生效值是 {effective or "空"}；保存后才作用于解析与下载'}]}
    parsed = urlparse(normalized)
    host, port = parsed.hostname or '', parsed.port or PROXY_DEFAULT_PORTS.get(parsed.scheme, 1080)
    started = time.time()
    try:
        with socket.create_connection((host, port), timeout=5): pass
        report['steps'].append({'name': '代理端口', 'ok': True, 'detail': f'{host}:{port} 可连接', 'ms': int((time.time() - started) * 1000)})
    except OSError as exc:
        report['steps'].append({'name': '代理端口', 'ok': False, 'detail': f'{host}:{port} 连接失败：{exc}', 'ms': int((time.time() - started) * 1000)})
        return report
    target = PROXY_TEST_TARGETS.get(platform) or PROXY_TEST_TARGETS['bilibili']
    netloc = urlparse(target).netloc
    if parsed.scheme not in ('http', 'https'):
        report['steps'].append({'name': '访问目标站', 'ok': None,
                                'detail': f'{parsed.scheme} 代理无法在服务端验证目标站访问（未内置 SOCKS 客户端），仅确认端口连通'})
        return report
    started = time.time()
    try:
        opener = build_opener(ProxyHandler({'http': normalized, 'https': normalized}))
        with opener.open(Request(target, headers={'User-Agent': 'Mozilla/5.0', 'Cache-Control': 'no-cache'}), timeout=12) as resp:
            code = getattr(resp, 'status', None) or resp.getcode()
        report['steps'].append({'name': '访问目标站', 'ok': True, 'detail': f'{netloc} 返回 HTTP {code}', 'ms': int((time.time() - started) * 1000)})
        report['ok'] = True
    except HTTPError as exc:
        report['steps'].append({'name': '访问目标站', 'ok': exc.code < 500, 'detail': f'{netloc} 返回 HTTP {exc.code}', 'ms': int((time.time() - started) * 1000)})
        report['ok'] = exc.code < 500
    except (URLError, OSError, ValueError) as exc:
        report['steps'].append({'name': '访问目标站', 'ok': False, 'detail': f'{type(exc).__name__}: {exc}', 'ms': int((time.time() - started) * 1000)})
    return report

def bilibili_auth():
    path = Path(config.get('cookies', {}).get('bilibili', ''))
    result = {'bilibili': False, 'bilibili_configured': path.is_file(), 'bilibili_account': None, 'bilibili_refresh_capable': bilibili_refresh_capable()}
    if not path.is_file(): return result
    try:
        jar = MozillaCookieJar(str(path))
        jar.load(ignore_discard=True)
        if not any(c.name == 'SESSDATA' and c.value for c in jar):
            result['bilibili_error'] = 'Cookie 中没有有效登录凭据，请重新扫码'
            return result
        with bilibili_request('https://api.bilibili.com/x/web-interface/nav', jar) as r:
            body = json.load(r)
        data = body.get('data') or {}
        if data.get('isLogin'):
            result.update(bilibili=True, bilibili_account={'uid': data.get('mid'), 'name': data.get('uname'), 'face': data.get('face')})
        else: result['bilibili_error'] = '登录已失效，请重新扫码'
    except Exception:
        result['bilibili_error'] = '无法验证登录状态，请检查 Cookie 或网络'
    return result

def bilibili_refresh_login():
    old_token = load_bilibili_auth().get('refresh_token')
    if not old_token: raise ValueError('当前登录没有刷新令牌，请重新扫码登录')
    jar = load_bilibili_jar(); csrf = csrf_from_jar(jar)
    with bilibili_request('https://passport.bilibili.com/x/passport-login/web/cookie/info?' + urlencode({'csrf': csrf}), jar) as r:
        info = json.load(r)
    if info.get('code') != 0: raise ValueError(info.get('message') or '无法检查登录刷新状态')
    data = info.get('data') or {}
    if not data.get('refresh'):
        return {'ok': True, 'refreshed': False, 'message': '当前登录状态无需刷新', **bilibili_auth()}
    correspond = refresh_correspond_path(data.get('timestamp'))
    with bilibili_request('https://www.bilibili.com/correspond/1/' + correspond, jar) as r:
        html = r.read().decode('utf-8', 'replace')
    match = re.search(r'id=["\']1-name["\'][^>]*>\s*([^<\s]+)', html)
    if not match: raise ValueError('B站未返回刷新校验信息')
    payload = urlencode({'csrf': csrf, 'refresh_csrf': match.group(1), 'source': 'main_web', 'refresh_token': old_token}).encode()
    with bilibili_request('https://passport.bilibili.com/x/passport-login/web/cookie/refresh', jar, payload) as r:
        refreshed = json.load(r)
    if refreshed.get('code') != 0: raise ValueError(refreshed.get('message') or '刷新登录状态失败')
    new_token = (refreshed.get('data') or {}).get('refresh_token')
    if not new_token: raise ValueError('B站未返回新的刷新令牌')
    new_csrf = csrf_from_jar(jar)
    confirm = urlencode({'csrf': new_csrf, 'refresh_token': old_token}).encode()
    with bilibili_request('https://passport.bilibili.com/x/passport-login/web/confirm/refresh', jar, confirm) as r:
        confirmed = json.load(r)
    if confirmed.get('code') != 0: raise ValueError(confirmed.get('message') or 'B站未确认刷新结果')
    save_bilibili_jar(jar, backup=True); save_bilibili_auth(new_token)
    log_event('auth', 'B站登录状态已刷新')
    return {'ok': True, 'refreshed': True, 'message': '登录状态已刷新', **bilibili_auth()}

def bilibili_account():
    return bilibili_auth()['bilibili_account']

def qr_start():
    jar = CookieJar()
    with bilibili_request('https://passport.bilibili.com/x/passport-login/web/qrcode/generate', jar) as r:
        data = json.loads(r.read())['data']
    sid = uuid.uuid4().hex; QR_SESSIONS[sid] = {'key': data['qrcode_key'], 'url': data['url'], 'created': time.time(), 'jar': jar}
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
    with bilibili_request(url, item['jar']) as r:
        body = json.loads(r.read()); code = body.get('data', {}).get('code')
        if code == 0:
            refresh_token = body.get('data', {}).get('refresh_token')
            count = save_bilibili_jar(item['jar']); QR_SESSIONS.pop(session, None)
            if refresh_token:
                save_bilibili_auth(refresh_token)
                log_event('auth', f'B站扫码认证成功，获取 {count} 个 Cookie 和刷新能力')
            else:
                atomic_json(BILIBILI_AUTH, {})
                log_event('auth', f'B站扫码认证成功，获取 {count} 个 Cookie；未返回刷新令牌')
            return {'state': 'success', 'cookies': count, 'refresh_capable': bool(refresh_token), 'account': bilibili_account()}
        if code == 86090: return {'state': 'scanned'}
        if code == 86038: return {'state': 'expired'}
        return {'state': 'waiting'}

def refresh_bilibili_login():
    jar = load_bilibili_jar(); old_token = load_bilibili_auth().get('refresh_token')
    if not old_token: raise ValueError('当前登录不支持刷新，请重新扫码登录')
    csrf = csrf_from_jar(jar)
    info_url = 'https://passport.bilibili.com/x/passport-login/web/cookie/info?' + urlencode({'csrf': csrf})
    with bilibili_request(info_url, jar) as r: info = json.load(r)
    if info.get('code') != 0: raise ValueError('无法检查 B站登录刷新状态')
    if not (info.get('data') or {}).get('refresh'): return {'ok': True, 'requires_refresh': False}
    with bilibili_request('https://www.bilibili.com/correspond/1/' + refresh_correspond_path(), jar) as r:
        refresh_csrf = r.read().decode().strip()
    if not refresh_csrf: raise ValueError('无法取得 B站刷新校验')
    form = urlencode({'csrf': csrf, 'refresh_csrf': refresh_csrf, 'source': 'main_web'}).encode()
    with bilibili_request('https://passport.bilibili.com/x/passport-login/web/cookie/refresh', jar, form) as r: refreshed = json.load(r)
    new_token = (refreshed.get('data') or {}).get('refresh_token')
    if refreshed.get('code') != 0 or not new_token: raise ValueError('B站未能刷新登录状态')
    save_bilibili_jar(jar, backup=True)
    new_csrf = csrf_from_jar(jar)
    confirm = urlencode({'csrf': new_csrf, 'refresh_token': old_token}).encode()
    with bilibili_request('https://passport.bilibili.com/x/passport-login/web/confirm', jar, confirm) as r: confirmed = json.load(r)
    if confirmed.get('code') != 0: raise ValueError('B站刷新已完成，但旧令牌确认失败')
    save_bilibili_auth(new_token)
    log_event('auth', 'B站登录状态已刷新')
    return {'ok': True, 'requires_refresh': True, 'refreshed': True}


def normalized_version(value):
    return str(value or '').strip().lower().lstrip('v')

def ytdlp_local_version():
    try:
        return subprocess.run([ytdlp_binary(), '--version'], capture_output=True, text=True, timeout=15).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ''

UPDATE_CHECK_CHOICES = (0, 1, 7, 14)
UPDATE_WATCH_INTERVAL = 600

def update_check_days():
    try: days = int(config.get('update_check_days', 7))
    except (TypeError, ValueError): days = 7
    return days if days in UPDATE_CHECK_CHOICES else 7

def update_cache():
    cache = config.get('update_cache')
    return cache if isinstance(cache, dict) else {}

def decorate_update_status(data):
    release = data.get('release') if isinstance(data, dict) else None
    latest = normalized_version(release.get('tag')) if isinstance(release, dict) else ''
    data['current_version'] = APP_VERSION
    data['up_to_date'] = bool(latest and latest == normalized_version(APP_VERSION))
    yt = data.get('ytdlp')
    if isinstance(yt, dict) and yt.get('ok') and not yt.get('up_to_date'):
        local = ytdlp_local_version()
        if local:
            yt['current'] = local
            yt['up_to_date'] = normalized_version(yt.get('tag')) == normalized_version(local)
    return data

def update_status(fresh=False):
    days = update_check_days()
    checked_at = update_cache().get('checked_at')
    if not fresh:
        cached = update_cache().get('data')
        result = dict(cached) if isinstance(cached, dict) else {}
        result.update({'cached': True, 'checked': bool(cached), 'current_version': APP_VERSION,
                       'auto_check_days': days, 'checked_at': checked_at,
                       'next_check_at': (checked_at + days * 86400) if (checked_at and days) else None})
        return 200, result
    code, data = update_agent('status')
    data = decorate_update_status(data)
    stamp = time.time()
    with LOCK:
        config['update_cache'] = {'checked_at': stamp, 'code': code, 'data': data}
        save_config()
    return code, {**data, 'cached': False, 'checked': True, 'auto_check_days': days,
                  'checked_at': stamp, 'next_check_at': (stamp + days * 86400) if days else None}

def update_check_due(now=None, days=None):
    days = update_check_days() if days is None else days
    if days <= 0: return False
    last = float(update_cache().get('checked_at') or 0)
    return (time.time() if now is None else now) - last >= days * 86400

def update_watcher():
    while True:
        time.sleep(UPDATE_WATCH_INTERVAL)
        try:
            days = update_check_days()
            if update_check_due(days=days):
                _, data = update_status(fresh=True)
                release = data.get('release') or {}
                yt = data.get('ytdlp') or {}
                found = release.get('available') or yt.get('available')
                log_event('system', f'按设定周期（每 {days} 天）检查更新：' + ('发现可更新内容' if found else '当前均为最新版本'))
        except Exception as exc:
            log_event('error', f'自动检查更新失败：{exc}', 'error')

def update_agent(method):
    if not UPDATE_AGENT_URL or not UPDATE_AGENT_TOKEN_FILE:
        raise ValueError('宿主更新代理未配置')
    try: agent_token = Path(UPDATE_AGENT_TOKEN_FILE).read_text(encoding='ascii').strip()
    except OSError: raise ValueError('宿主更新代理令牌不可读')
    request = Request(UPDATE_AGENT_URL.rstrip('/') + '/v1/' + method, method='POST' if method == 'update' else 'GET', headers={'X-Streamforge-Update-Token': agent_token})
    try:
        with urlopen(request, timeout=20) as result: return result.status, json.load(result)
    except Exception as exc:
        raise ValueError(f'更新代理不可达：{exc}')

YT_DLP_MANAGED = ROOT / 'tools' / 'yt-dlp' / 'yt-dlp'

def ytdlp_binary():
    return str(YT_DLP_MANAGED) if YT_DLP_MANAGED.is_file() and os.access(YT_DLP_MANAGED, os.X_OK) else 'yt-dlp'

def ytdlp_args(args, platform='bilibili'):
    cmd = [ytdlp_binary(), '--no-warnings', '--newline']
    if NODE_PATH: cmd += ['--js-runtimes', f'node:{NODE_PATH}']
    for plugin_dir in (PERSISTENT_PLUGINS, RUNTIME_PLUGINS):
        if plugin_dir.is_dir(): cmd += ['--plugin-dirs', str(plugin_dir)]
    cookie = config.get('cookies', {}).get(platform, '')
    proxy = config.get('proxies', {}).get(platform, '')
    if cookie and Path(cookie).is_file(): cmd += ['--cookies', cookie]
    elif cookie: warn_missing_cookie(platform, cookie)
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
          'bit_depth': f.get('bit_depth'), 'dolby': f.get('dolby') or ('Dolby Vision' if f.get('dynamic_range') == 'DV' else None),
          'metadata_source': f.get('metadata_source', 'extractor'),
          'protocol': f.get('protocol'), 'kind': 'video' if has_video else 'audio' if has_audio else 'other'}
        if item['kind'] in ('video', 'audio'): out.append(item)
    return {'title': info.get('title'), 'id': info.get('id'), 'uploader': info.get('uploader'),
      'thumbnail': info.get('thumbnail'), 'webpage_url': info.get('webpage_url'),
      'duration': info.get('duration'), 'upload_date': info.get('upload_date'),
      'description': info.get('description'), 'formats': out}

def download_root():
    configured = Path(str(config.get('download_path') or DOWNLOADS))
    try:
        root = configured.resolve()
        allowed = DOWNLOADS.resolve()
        root.relative_to(allowed)
        return root
    except (OSError, ValueError):
        return DOWNLOADS.resolve()

def validate_download_path(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('下载目录不能为空')
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = DOWNLOADS / candidate
    try:
        candidate = candidate.resolve()
        candidate.relative_to(DOWNLOADS.resolve())
    except (OSError, ValueError):
        raise ValueError(f'目录不可用：只能使用已挂载下载目录 {DOWNLOADS} 及其子目录')
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ('.streamforge-write-check-' + uuid.uuid4().hex)
        probe.write_text('ok', encoding='ascii')
        probe.unlink()
    except OSError as exc:
        raise ValueError(f'目录不可写：{candidate}（{exc.strerror or exc}）')
    return candidate

MEDIA_EXTENSIONS = {'.mp4', '.m4v', '.mkv', '.webm', '.mov', '.avi', '.flv', '.ts', '.mpg', '.mpeg', '.wmv',
                    '.mp3', '.m4a', '.aac', '.flac', '.opus', '.ogg', '.wav'}
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}

def xml_text(value):
    return str(value).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

def xml_attribute(value):
    return xml_text(value).replace('"', '&quot;')

def cdata(value):
    # ']]>' cannot appear inside a CDATA section; split it into two sections.
    return '<![CDATA[' + str(value).replace(']]>', ']]]]><![CDATA[>') + ']]>'

def plot_markup(description, website, label):
    """Render the description the way bili-sync does: HTML inside CDATA, newlines as <br/>, links clickable."""
    text = html.unescape(str(description or '')).replace('\r\n', '\n').replace('\r', '\n').strip()
    text = xml_text(text)
    text = re.sub(r'(https?://[^\s<>"\']+)', lambda m: '<a href="%s">%s</a>' % (m.group(1), m.group(1)), text)
    text = text.replace('\n', '<br/>')
    prefix = '原始视频：<a href="%s">%s</a><br/><br/>' % (xml_attribute(website), xml_text(label)) if website else ''
    return prefix + text

def media_file(folder, tail):
    """Find the file yt-dlp ended up writing so sidecars can reuse its base name."""
    for line in reversed(list(tail or [])):
        match = re.search(r'Merging formats into "([^"]+)"', line)
        if match:
            candidate = Path(match.group(1))
            if candidate.is_file(): return candidate
    candidates = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_EXTENSIONS]
    return max(candidates, key=lambda p: p.stat().st_size) if candidates else None

def place_cover(folder, stem):
    """Rename the downloaded thumbnail to the poster/fanart sidecar names media libraries match on."""
    for candidate in sorted(folder.glob(stem + '.*')):
        if candidate.suffix.lower() not in IMAGE_EXTENSIONS or not candidate.is_file(): continue
        if candidate.stem.endswith(('-poster', '-fanart')): continue
        poster = folder / (stem + '-poster' + candidate.suffix.lower())
        fanart = folder / (stem + '-fanart' + candidate.suffix.lower())
        candidate.replace(poster)
        shutil.copyfile(poster, fanart)
        return poster, fanart
    return None, None

def write_nfo(folder, job, stem=None):
    info = job.get('source_info') if isinstance(job.get('source_info'), dict) else {}
    website = str(job.get('url') or '')
    lines = ['<?xml version="1.0" encoding="utf-8" standalone="yes"?>', '<movie>']
    for name, value in (('title', job.get('title')), ('originaltitle', job.get('title')), ('website', website),
                        ('premiered', info.get('upload_date')), ('studio', info.get('uploader'))):
        if value not in (None, ''): lines.append('  <%s>%s</%s>' % (name, xml_text(value), name))
    if info.get('id'):
        lines.append('  <uniqueid type="%s">%s</uniqueid>' % (xml_attribute(job.get('platform') or 'bilibili'), xml_text(info['id'])))
    plot = plot_markup(info.get('description'), website, info.get('id') or job.get('title'))
    if plot: lines.append('  <plot>%s</plot>' % cdata(plot))
    lines.append('  <streamforge_format>%s</streamforge_format>' % xml_text(job.get('format') or ''))
    lines.append('</movie>')
    document = '\n'.join(lines) + '\n'
    try:
        fromstring(document)
    except ParseError:
        # Never leave an unreadable nfo behind: fall back to the raw text without markup.
        plain = xml_text(html.unescape(str(info.get('description') or '')).strip())
        index = next((i for i, line in enumerate(lines) if '<plot>' in line), None)
        if index is not None:
            if plain: lines[index] = '  <plot>%s</plot>' % plain
            else: lines.pop(index)
        document = '\n'.join(lines) + '\n'
        fromstring(document)
    (folder / ((stem or 'metadata') + '.nfo')).write_text(document, encoding='utf-8')

def finalize_metadata(folder, job, tail):
    try:
        media = media_file(folder, tail)
        stem = media.stem if media else 'metadata'
        poster, _ = place_cover(folder, stem)
        write_nfo(folder, job, stem)
        log_event('download', '已写入封面 %s 与 %s.nfo' % (poster.name, stem) if poster else '已写入 %s.nfo' % stem)
    except Exception as exc:
        log_event('download', '封面或元数据写入失败：%s' % exc, 'error')

def run_download(job):
    jid = job['id']; job['status'] = 'running'; job['started_at'] = time.time(); save_jobs()
    try:
        folder = download_root() / clean_name(job.get('title') or jid); folder.mkdir(parents=True, exist_ok=True)
        output = str(folder / '%(title)s.%(ext)s')
        args = ['-f', job['format'], '-o', output]
        if job.get('metadata'):
            args += ['--write-info-json', '--write-thumbnail']
            if config.get('thumbnail_jpg', True): args += ['--convert-thumbnails', 'jpg']
        args += [job['url']]
        proc = subprocess.Popen(ytdlp_args(args, job.get('platform', 'bilibili')), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        with LOCK: processes[jid] = proc
        tail = collections.deque(maxlen=25)
        last_save = 0.0
        for line in proc.stdout:
            tail.append(line.strip())
            with LOCK: job['log'] = line.strip()[-800:]; job['updated_at'] = time.time()
            match = re.search(r'\[download\]\s+(\d+(?:\.\d+)?)%', line)
            if match: job['percent'] = float(match.group(1))
            if job.get('cancel_requested'): proc.terminate(); break
            # yt-dlp 一秒能吐几十行，每行都落盘等于把整份 jobs.json 重写几十遍（写放大）。
            # 进度按秒节流；状态变化与收尾仍由下面的 save_jobs() 立即写。
            if time.time() - last_save >= 1: save_jobs(); last_save = time.time()
        rc = proc.wait()
        with LOCK:
            job['status'] = 'cancelled' if job.get('cancel_requested') else 'done' if rc == 0 else 'error'
            job['returncode'] = rc
            if rc != 0 and not job.get('cancel_requested'): job['error'] = friendly_error('\n'.join(tail))
            if rc == 0 and job.get('metadata'): finalize_metadata(folder, job, tail)
        save_jobs()
    except Exception as exc:
        job['status'] = 'error'; job['error'] = friendly_error(str(exc)); save_jobs()
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
threading.Thread(target=update_watcher, daemon=True).start()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def body(self): return json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/health':
            return response(self, 200, {'ok': True, 'version': APP_VERSION, 'build': build_stamp(), 'node': {'mode': NODE_MODE, 'path': NODE_PATH, 'version': command_version([NODE_PATH, '--version']) if NODE_PATH else ''}, 'ytdlp': command_version(['yt-dlp', '--version']), 'ffmpeg': command_version(['ffmpeg', '-version']).splitlines()[0] if command_version(['ffmpeg', '-version']) else ''})
        if path == '/api/jobs':
            with LOCK: return response(self, 200, {'jobs': list(jobs.values()), 'config': config})
        if path == '/api/config': return response(self, 200, config)
        if path == '/api/updates':
            fresh = parse_qs(urlparse(self.path).query).get('fresh', ['0'])[0].lower() in ('1', 'true', 'yes')
            try:
                code, data = update_status(fresh=fresh)
                return response(self, code, data)
            except ValueError as exc: return response(self, 503, {'error': str(exc)})
        if path == '/api/logs': return response(self, 200, LOGS)
        if path == '/api/auth': return response(self, 200, {**bilibili_auth(), **youtube_auth(), 'proxies': {k: bool(v) for k, v in config.get('proxies', {}).items()}})
        if path.startswith('/api/bilibili/qr/status/'):
            return response(self, 200, qr_poll(path.rsplit('/', 1)[-1]))
        if path.startswith('/api/bilibili/qr/image/'):
            data = qr_image(path.rsplit('/', 1)[-1])
            if not data: return response(self, 404, {'error': 'qr unavailable'})
            self.send_response(200); self.send_header('Content-Type', 'image/png'); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
        if path == '/api/plugins': return response(self, 200, list_plugins())
        if path == '/api/backups': return response(self, 200, [{'name': p.name, 'size': p.stat().st_size} for p in sorted(BACKUPS.glob('*.zip'))])
        if path == '/': path = '/index.html'
        file = STATIC / path.lstrip('/')
        if file.is_file():
            data = file.read_bytes(); kind = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.png': 'image/png'}.get(file.suffix, 'application/octet-stream')
            self.send_response(200); self.send_header('Cache-Control', 'no-cache'); self.send_header('Content-Type', kind); self.send_header('Content-Length', str(len(data))); self.end_headers(); self.wfile.write(data); return
        response(self, 404, {'error': 'not found'})
    def do_POST(self):
        path = urlparse(self.path).path; body = self.body()
        if path == '/api/inspect':
            log_event('parse', '开始解析媒体地址')
            platform = body.get('platform', 'bilibili')
            try:
                result = subprocess.run(ytdlp_args(['--dump-single-json', '--skip-download', body['url']], platform), capture_output=True, text=True, timeout=180)
                if result.returncode == 0:
                    log_event('parse', '解析完成')
                    info = enrich_formats(json.loads(result.stdout))
                    probed = probe_best_audio(info, config.get('proxies', {}).get(platform, ''))
                    log_event('parse', f"最高音质 {probed['id']} 码流探测：{probed['dolby']}（{probed['elapsed']}s）" if probed else '最高音质码流探测未取到结果，沿用编码推导值')
                    return response(self, 200, formats(info))
                log_event('error', '解析失败', 'error')
                return response(self, 422, {'error': friendly_error(result.stderr)})
            except Exception as exc: return response(self, 500, {'error': str(exc)})
        if path == '/api/jobs':
            if not body.get('url') or not body.get('format'): return response(self, 400, {'error': 'url and format are required'})
            job = {'id': uuid.uuid4().hex[:12], 'url': body['url'], 'format': body['format'], 'platform': body.get('platform', 'bilibili'), 'title': body.get('title', 'untitled'), 'source_info': body.get('source_info') if isinstance(body.get('source_info'), dict) else {}, 'metadata': bool(body.get('metadata', config.get('metadata'))), 'status': 'waiting' if config.get('queue_mode') else 'queued', 'percent': 0, 'created_at': time.time(), 'log': ''}
            with LOCK: jobs[job['id']] = job
            save_jobs(); log_event('download', f'任务已加入队列：{job["id"]}'); return response(self, 202, job)
        if path == '/api/jobs/start':
            with LOCK:
                started = [j['id'] for j in jobs.values() if j.get('status') == 'waiting']
                for jid in started: jobs[jid]['status'] = 'queued'; jobs[jid]['updated_at'] = time.time()
                save_jobs()
            log_event('download', f'开始 {len(started)} 个等待任务')
            return response(self, 200, {'ok': True, 'started': started})
        if path == '/api/jobs/clear-finished':
            with LOCK:
                removed = [jid for jid, j in jobs.items() if j.get('status') in ('done', 'error', 'cancelled')]
                for jid in removed: jobs.pop(jid, None)
                save_jobs()
            return response(self, 200, {'ok': True, 'removed': removed})
        if path == '/api/proxy-test':
            platform = body.get('platform')
            if platform not in ('bilibili', 'youtube'): return response(self, 422, {'error': '只能检测 B站或 YouTube 代理'})
            label = 'YouTube' if platform == 'youtube' else 'B站'
            try: normalized = normalize_proxy(body.get('value'), label)
            except ValueError as exc: return response(self, 422, {'error': str(exc)})
            report = test_proxy(platform, normalized)
            summary = '；'.join(f'{s["name"]}{"通过" if s["ok"] else "未通过" if s["ok"] is False else "未验证"}：{s["detail"]}' for s in report['steps'])
            log_event('config', f'{label}代理检测：{summary}', 'info' if report['ok'] else 'error')
            return response(self, 200, report)
        if path == '/api/download-path':
            try:
                selected = validate_download_path(body.get('path'))
                config['download_path'] = str(selected)
                save_config()
                return response(self, 200, {'ok': True, 'path': str(selected), 'host_mount_required': False})
            except ValueError as exc: return response(self, 422, {'error': str(exc)})
        if path == '/api/config':
            with LOCK:
                if 'update_check_days' in body:
                    days = body['update_check_days']
                    if isinstance(days, bool) or not isinstance(days, int) or days not in UPDATE_CHECK_CHOICES:
                        return response(self, 422, {'error': '自动检查更新只能是每天、每 7 天、每 14 天或不自动检测'})
                    config['update_check_days'] = days
                if 'proxies' in body:
                    try: normalized = normalize_proxies(body['proxies'])
                    except ValueError as exc: return response(self, 422, {'error': str(exc)})
                    config.setdefault('proxies', {}).update(normalized)
                    for key, value in normalized.items():
                        label = 'YouTube' if key == 'youtube' else 'B站'
                        log_event('config', f'{label}代理已{"设为 " + value if value else "清除"}，之后的解析与下载立即生效')
                if 'thumbnail_jpg' in body:
                    if not isinstance(body['thumbnail_jpg'], bool):
                        return response(self, 422, {'error': '封面转换开关只能是开或关'})
                    config['thumbnail_jpg'] = body['thumbnail_jpg']
                config.update({k: v for k, v in body.items() if k in ('concurrency', 'metadata', 'queue_mode', 'download_path', 'cookies')}); save_config()
            return response(self, 200, config)
        if path == '/api/bilibili/qr/start':
            try: return response(self, 200, qr_start())
            except Exception as exc: return response(self, 502, {'error': str(exc)})
        if path == '/api/updates/start':
            try:
                _, status = update_status()
                if status.get('up_to_date'):
                    return response(self, 409, {'error': f'当前已是最新版本 v{APP_VERSION}'})
                release = status.get('release') or {}
                if not release.get('available'):
                    return response(self, 409, {'error': '最新 Release 缺少可校验的 ARM64 镜像资产'})
                code, data = update_agent('update')
                return response(self, code, data)
            except ValueError as exc: return response(self, 503, {'error': str(exc)})
        if path == '/api/updates/ytdlp':
            try:
                code, data = update_agent('ytdlp')
                return response(self, code, data)
            except ValueError as exc: return response(self, 503, {'error': str(exc)})
        if path == '/api/bilibili/refresh':
            try: return response(self, 200, bilibili_refresh_login())
            except ValueError as exc: return response(self, 422, {'error': str(exc)})
            except Exception: return response(self, 502, {'error': 'B站刷新请求失败，请稍后重试或重新扫码'})
        if path == '/api/auth':
            try:
                if 'bilibili_cookie_text' in body:
                    auth = save_bilibili_text(body['bilibili_cookie_text'])
                    log_event('auth', 'B站 Cookie 已由手动输入覆盖')
                    return response(self, 200, {'ok': True, **auth})
                if 'youtube_cookie_text' in body:
                    auth = save_youtube_text(body['youtube_cookie_text'])
                    log_event('auth', f'YouTube Cookie 已保存（{auth["youtube_cookies"]} 条）')
                    return response(self, 200, {'ok': True, **auth})
                with LOCK:
                    if isinstance(body.get('cookies'), dict): config.setdefault('cookies', {}).update(body['cookies'])
                    if isinstance(body.get('proxies'), dict): config.setdefault('proxies', {}).update(normalize_proxies(body['proxies']))
                    save_config()
                log_event('auth', '认证配置已保存')
                return response(self, 200, {'ok': True, 'restart_required': False})
            except ValueError as exc: return response(self, 422, {'error': str(exc)})
        if path == '/api/restart':
            response(self, 202, {'ok': True, 'message': '服务正在重启'})
            threading.Timer(0.2, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
            return
        if path == '/api/plugins/install':
            try:
                plugin = install_plugin(body.get('name'), body.get('zip_base64'), bool(body.get('persistent')))
                log_event('plugin', f'插件已安装：{plugin["id"]}')
                return response(self, 201, {'ok': True, 'plugin': plugin, 'restart_required': True})
            except ValueError as exc: return response(self, 422, {'error': str(exc)})
            except OSError as exc: return response(self, 500, {'error': f'plugin install failed: {exc}'})
        m = re.match(r'/api/plugins/([^/]+)/(enable|disable)$', path)
        if m:
            plugin_id, action = m.groups()
            try: plugin_id = safe_plugin_id(plugin_id)
            except ValueError: return response(self, 404, {'error': 'plugin not found'})
            target, persistent = find_plugin(plugin_id)
            if not target: return response(self, 404, {'error': 'plugin not found'})
            marker = target / '.disabled'
            if action == 'disable': marker.touch()
            else: marker.unlink(missing_ok=True)
            plugin = plugin_info(target, persistent)
            log_event('plugin', f'插件已{"禁用" if action == "disable" else "启用"}：{plugin_id}')
            return response(self, 200, {'ok': True, 'plugin': plugin, 'restart_required': True})
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
                    for key, value in config.get('cookies', {}).items():
                        source = Path(value) if isinstance(value, str) and value else None
                        if source and source.is_file(): z.write(source, f'cookies/{key}.txt')
                if 'proxies' in selected: z.writestr('proxies.json', json.dumps(config.get('proxies', {}), ensure_ascii=False, indent=2))
                if 'queue' in selected: z.writestr('jobs.json', json.dumps(list(jobs.values()), ensure_ascii=False, indent=2))
                if 'plugins' in selected:
                    for p in PLUGINS.rglob('*'):
                        if p.is_file(): z.write(p, 'plugins/' + str(p.relative_to(PLUGINS)))
            return response(self, 201, {'ok': True, 'name': name, 'restart_required': False})
        if path == '/api/restore':
            name = body.get('name', ''); selected = set(body.get('include', [])); source = BACKUPS / Path(name).name
            if not source.is_file(): return response(self, 404, {'error': 'backup not found'})
            try:
                with zipfile.ZipFile(source) as z:
                    if 'config' in selected and 'config.json' in z.namelist(): config.update(json.loads(z.read('config.json'))); save_config()
                    if 'cookies' in selected:
                        restored = []
                        for item in z.namelist():
                            if item.startswith('cookies/') and item.endswith('.txt') and not item.endswith('/'):
                                key = Path(item).stem
                                current = Path(config.get('cookies', {}).get(key, '') or '')
                                if current.is_file(): shutil.copy2(current, BACKUPS / (f'{key}-cookie-' + uuid.uuid4().hex + '.txt'))
                                target = current if current.name and current.suffix == '.txt' else ROOT / 'cookies' / f'{key}.txt'
                                target.parent.mkdir(parents=True, exist_ok=True)
                                target.write_bytes(z.read(item))
                                config.setdefault('cookies', {})[key] = str(target)
                                restored.append(key)
                        save_config()
                        log_event('auth', 'Cookie 已从备份恢复：' + (', '.join(sorted(restored)) or '无'))
                    if 'queue' in selected and 'jobs.json' in z.namelist(): jobs.clear(); jobs.update({j['id']: j for j in json.loads(z.read('jobs.json'))}); save_jobs()
                    if 'plugins' in selected:
                        for item in z.namelist():
                            if item.startswith('plugins/') and not item.endswith('/'): z.extract(item, ROOT)
            except Exception as exc:
                log_event('error', f'从备份恢复失败：{exc}', 'error')
                return response(self, 500, {'error': f'恢复失败：{exc}'})
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
        if self.path == '/api/logs':
            LOGS.clear(); log_event('system', '日志已清空'); return response(self, 200, {'ok': True})
        m = re.match(r'/api/plugins/([^/]+)$', urlparse(self.path).path)
        if m:
            try: plugin_id = safe_plugin_id(m.group(1))
            except ValueError: return response(self, 404, {'error': 'plugin not found'})
            target, _ = find_plugin(plugin_id)
            if not target: return response(self, 404, {'error': 'plugin not found'})
            backup = BACKUPS / ('plugin-' + plugin_id + '-deleted-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
            with LOCK: target.replace(backup)
            log_event('plugin', f'插件已删除：{plugin_id}')
            return response(self, 200, {'ok': True, 'id': plugin_id, 'restart_required': True})
        m = re.match(r'/api/jobs/([^/]+)$', urlparse(self.path).path)
        if not m: return response(self, 404, {'error': 'not found'})
        job = jobs.get(m.group(1))
        if not job: return response(self, 404, {'error': 'job not found'})
        if job.get('status') in ('waiting', 'queued', 'running', 'paused'): job['cancel_requested'] = True; job['status'] = 'cancelled' if job['status'] in ('waiting', 'queued') else job['status']; save_jobs()
        return response(self, 200, {'ok': True})

if __name__ == '__main__': ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('PORT', '8081'))), Handler).serve_forever()
