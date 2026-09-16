import ast, base64, io, json, os, sys, tempfile, zipfile
from pathlib import Path
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from unittest.mock import patch
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent))
import stream_metadata as m
with tempfile.TemporaryDirectory() as tmp:
    os.environ['STREAMFORGE_DATA'] = tmp + '/data'
    os.environ['STREAMFORGE_DOWNLOADS'] = tmp + '/downloads'
    path = Path(__file__).with_name('server.py')
    tree = ast.parse(path.read_text())
    # Exercise real helpers without starting the scheduler or HTTP server.
    nodes = []
    for n in tree.body:
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and ast.unparse(n).startswith('threading.Thread('): break
        nodes.append(n)
    ns = {'__file__': str(path)}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), ns)
    jar = CookieJar()
    jar.set_cookie(Cookie(0, 'SESSDATA', 'offline-test', None, False, '.bilibili.com', True, True, '/', True, True, None, True, None, None, {'HttpOnly': None}, False))
    assert ns['save_bilibili_jar'](jar) == 1
    assert ns['save_bilibili_text']('# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tTRUE\t1900000000\tSESSDATA\tmanual-last\n')['bilibili_configured']
    saved = MozillaCookieJar(str(ns['ROOT'] / 'cookies/bilibili.txt')); saved.load(ignore_discard=True, ignore_expires=True)
    assert list(saved)[0].value == 'manual-last'
    try: ns['save_bilibili_text']('# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tTRUE\t1900000000\tb_nut\tno-session\n')
    except ValueError: pass
    else: raise AssertionError('missing SESSDATA accepted')
    with patch.dict(ns, bilibili_request=lambda *a: io.BytesIO(json.dumps({'data': {'isLogin': True, 'mid': 123, 'uname': 'offline'}}).encode())):
        assert ns['bilibili_auth']()['bilibili_account']['name'] == 'offline'
    youtube_file = ns['ROOT'] / 'cookies/youtube.txt'
    assert ns['save_youtube_text']('# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1900000000\tSID\tmanual\n.youtube.com\tTRUE\t/\tTRUE\t1900000000\tLOGIN_INFO\tx\n')['youtube_configured']
    saved_youtube = MozillaCookieJar(str(youtube_file)); saved_youtube.load(ignore_discard=True, ignore_expires=True)
    assert {c.name for c in saved_youtube} == {'SID', 'LOGIN_INFO'}
    assert '--cookies' in ns['ytdlp_args'](['--version'], 'youtube')
    auth_youtube = ns['youtube_auth']()
    assert auth_youtube['youtube'] is True and auth_youtube['youtube_cookies'] == 2
    header_form = ns['save_youtube_text']('SID=raw-value; SAPISID=raw-sapisid; PREF=tz=UTC')
    assert header_form['youtube'] is True
    converted = MozillaCookieJar(str(youtube_file)); converted.load(ignore_discard=True, ignore_expires=True)
    assert ('PREF' in {c.name for c in converted}) and all(c.domain == '.youtube.com' for c in converted) and len(converted) == 3
    for bad in ['', 'no tab and no equals', '# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1900000000\tPREF\tno-login\n']:
        try: ns['save_youtube_text'](bad)
        except ValueError: pass
        else: raise AssertionError('invalid YouTube cookie accepted: %r' % bad)
    detail = ns['youtube_auth']()
    assert detail['youtube_login_present'] == ['SID', 'SAPISID'] and detail['youtube_login_missing'] and not detail['youtube_login_expired']
    assert detail['youtube_valid'] == 3 and detail['youtube_expired'] == 0 and detail['youtube_saved_at'] > 0
    ns['save_youtube_text']('SID=%s; LOGIN_INFO=x; SAPISID=y' % ('a' * 8))
    ns['youtube_cookie_path']().write_text('# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t100\tSID\told\n.youtube.com\tTRUE\t/\tTRUE\t1900000000\tPREF\tkeep\n', encoding='utf-8')
    stale = ns['youtube_auth']()
    assert stale['youtube'] is False and stale['youtube_login_expired'] == ['SID'] and stale['youtube_expired'] == 1 and stale['youtube_valid'] == 1
    assert '已过期' in stale['youtube_error']
    youtube_file.unlink()
    assert '--cookies' not in ns['ytdlp_args'](['--version'], 'youtube')
    assert ns['youtube_auth']()['youtube_error'] == 'Cookie 文件已丢失，请重新粘贴'
    assert ns['friendly_error']('ERROR: Sign in to confirm you are not a bot. Use --cookies.') .count('提示：') == 1
    assert ns['friendly_error']('plain failure') == 'plain failure'
    assert ns['update_check_days']() == 7
    ns['config']['update_check_days'] = 14
    assert ns['update_check_days']() == 14
    ns['config']['update_check_days'] = 5
    assert ns['update_check_days']() == 7
    ns['config']['update_check_days'] = 0
    assert ns['update_check_days']() == 0
    ns['config']['update_cache'] = {}
    code, payload = ns['update_status'](fresh=False)
    assert code == 200 and payload['checked'] is False and payload['cached'] is True and payload['current_version']
    ns['config']['update_cache'] = {'checked_at': 1700000000, 'code': 200, 'data': {'release': {'ok': True, 'tag': 'v1.0.0', 'available': False}}}
    code, payload = ns['update_status'](fresh=False)
    assert payload['checked'] is True and payload['checked_at'] == 1700000000 and payload['next_check_at'] is None
    ns['config']['update_check_days'] = 7
    assert ns['update_status'](fresh=False)[1]['next_check_at'] == 1700000000 + 7 * 86400
    now = 1800000000.0
    due = ns['update_check_due']
    ns['config']['update_cache'] = {}
    assert due(now=now) is True
    ns['config']['update_cache'] = {'checked_at': now - 8 * 86400}
    assert due(now=now) is True and due(now=now - 2 * 86400) is False
    ns['config']['update_cache'] = {'checked_at': now - 3 * 86400}
    assert due(now=now) is False and due(now=now, days=1) is True
    ns['config']['update_check_days'] = 0
    assert due(now=now + 365 * 86400) is False
    proxies = ns['config']['proxies']
    proxies['bilibili'] = proxies['youtube'] = ''
    norm = ns['normalize_proxies']
    assert norm({'youtube': ' http://127.0.0.1:7890 ', 'bilibili': '', 'junk': 'x'}) == {'youtube': 'http://127.0.0.1:7890', 'bilibili': ''}
    assert norm({'bilibili': 'socks5h://10.0.0.1:1080'}) == {'bilibili': 'socks5h://10.0.0.1:1080'}
    assert norm({'youtube': '127.0.0.1:7890'}) == {'youtube': 'http://127.0.0.1:7890'}
    assert norm({'youtube': '"http://127.0.0.1:7890/"'}) == {'youtube': 'http://127.0.0.1:7890'}
    for bad in ({'youtube': 'ftp://127.0.0.1'}, {'bilibili': 'http://a b'}, {'bilibili': 'http://127.0.0.1:7890/path'}, {'youtube': 7890}, ['x']):
        try:
            norm(bad); raise AssertionError(f'未拦截：{bad}')
        except ValueError: pass
    args = ns['ytdlp_args'](['--url'], 'bilibili')
    assert '--proxy' not in args
    proxies['youtube'] = 'http://127.0.0.1:7890'
    proxied = ns['ytdlp_args'](['--url'], 'youtube')
    assert proxied[proxied.index('--proxy') + 1] == 'http://127.0.0.1:7890'
    captured = []

    class FakeOpener:
        def open(self, req, timeout=None): raise RuntimeError('offline')

    ns['build_opener'] = lambda *handlers: (captured.extend(handlers), FakeOpener())[1]
    jar = ns['MozillaCookieJar']()
    proxies['bilibili'] = ''
    try: ns['bilibili_request']('https://api.bilibili.com/x/web-interface/nav', jar)
    except Exception: pass
    assert not any(isinstance(h, ns['ProxyHandler']) for h in captured)
    captured.clear()
    proxies['bilibili'] = 'socks5://127.0.0.1:1080'
    try: ns['bilibili_request']('https://api.bilibili.com/x/web-interface/nav', jar)
    except Exception: pass
    handlers = [h for h in captured if isinstance(h, ns['ProxyHandler'])]
    assert len(handlers) == 1 and handlers[0].proxies == {'http': 'socks5://127.0.0.1:1080', 'https': 'socks5://127.0.0.1:1080'}
    proxies['bilibili'] = proxies['youtube'] = ''
    norm = ns['normalize_proxies']
    assert norm({'youtube': ' http://127.0.0.1:7890 ', 'bilibili': '', 'junk': 'x'}) == {'youtube': 'http://127.0.0.1:7890', 'bilibili': ''}
    assert norm({'bilibili': 'socks5h://10.0.0.1:1080'}) == {'bilibili': 'socks5h://10.0.0.1:1080'}
    assert norm({'youtube': '127.0.0.1:7890'}) == {'youtube': 'http://127.0.0.1:7890'}
    assert norm({'youtube': '"http://127.0.0.1:7890/"'}) == {'youtube': 'http://127.0.0.1:7890'}
    for bad in ({'youtube': 'ftp://127.0.0.1'}, {'bilibili': 'http://a b'}, {'bilibili': 'http://127.0.0.1:7890/path'}, {'youtube': 7890}, ['x']):
        try:
            norm(bad); raise AssertionError(f'未拦截：{bad}')
        except ValueError: pass
    for code, state in [(86090, 'scanned'), (86038, 'expired'), (86101, 'waiting')]:
        ns['QR_SESSIONS']['test'] = {'created': ns['time'].time(), 'key': 'test', 'jar': jar}
        with patch.dict(ns, bilibili_request=lambda *a, code=code: io.BytesIO(json.dumps({'data': {'code': code}}).encode())):
            assert ns['qr_poll']('test')['state'] == state
    assert ns['validate_download_path']('confirmed').is_dir()
    try: ns['validate_download_path']('/outside-mounted-downloads')
    except ValueError: pass
    else: raise AssertionError('outside download mount accepted')
    nfo_dir = ns['DOWNLOADS'] / 'nfo'
    nfo_dir.mkdir()
    ns['write_nfo'](nfo_dir, {'title': 'test', 'url': 'https://example.test/v', 'format': 'v+a', 'source_info': {'id': 'x', 'uploader': 'u'}})
    assert '<title>test</title>' in (nfo_dir / 'metadata.nfo').read_text()
    # Stream metadata comes from the extractor payload only: no ffprobe, no network.
    video={'url':'https://example.test/v','vcodec':'hevc','dynamic_range':'HDR10','fps':59.94}
    sdr={'url':'https://example.test/s','vcodec':'h264','dynamic_range':'SDR'}
    audio={'url':'https://example.test/a','vcodec':'none','acodec':'ec-3','asr':48000,'audio_channels':6}
    aac={'url':'https://example.test/b','vcodec':'none','acodec':'mp4a.40.2'}
    assert not hasattr(m,'probe_format')
    with patch.object(m,'subprocess') as fake_probe:
        fake_probe.run.side_effect = AssertionError('enrich_formats 不应调用 ffprobe')
        m.enrich_formats({'formats':[video,sdr,audio,aac]})
    assert video['bit_depth']==10 and video['fps']==59.94
    assert sdr['bit_depth']==8 and 'dolby' not in sdr
    assert audio['dolby']=='Dolby Digital Plus' and aac['dolby']=='无'
    assert m.bit_depth_from_dynamic_range('') is None and m.bit_depth_from_dynamic_range('HLG')==10
    assert m.dolby_from_codec('opus')=='无'
    # 只探最高音质那一条：码流里带 Atmos 才补 / Atmos 后缀。
    high={'url':'https://example.test/atmos','vcodec':'none','acodec':'ec-3','abr':256,'asr':48000,'audio_channels':6,'format_id':'380'}
    low={'url':'https://example.test/low','vcodec':'none','acodec':'mp4a.40.2','abr':128,'asr':44100,'audio_channels':2,'format_id':'140'}
    probe_info={'formats':[low,high]}
    m.enrich_formats(probe_info)
    seen={}
    def fake_ffprobe(args,**kwargs):
        seen['args']=args; seen['env']=kwargs.get('env') or {}
        return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'streams':[
            {'codec_type':'audio','codec_name':'eac3','profile':'Dolby Digital Plus + Dolby Atmos','channels':6,'sample_rate':'48000'}]}))
    with patch.object(m,'subprocess') as fake_probe:
        fake_probe.run.side_effect = fake_ffprobe
        summary = m.probe_best_audio(probe_info, 'http://127.0.0.1:7890')
    assert summary['id']=='380' and summary['dolby']=='Dolby Digital Plus / Atmos', summary
    assert high['dolby']=='Dolby Digital Plus / Atmos' and low['dolby']=='无'
    assert seen['args'][-1]==high['url'] and seen['env'].get('http_proxy')=='http://127.0.0.1:7890'
    assert m.best_audio(probe_info) is high
    with patch.object(m,'subprocess') as fake_probe:
        fake_probe.run.side_effect = lambda *a, **k: SimpleNamespace(returncode=1, stdout='', stderr='boom')
        assert m.probe_best_audio(probe_info) is None
        assert high['dolby']=='Dolby Digital Plus / Atmos'
        assert m.probe_best_audio({'formats':[{'acodec':'mp4a.40.2','vcodec':'none'}]}) is None
    assert ns['formats']({'formats':[video,audio]})['formats'][0]['bit_depth']==10
    ns['log_event']('system','offline self-test entry')
    assert isinstance(ns['LOGS'][-1]['ts'],float) and ns['LOGS'][-1]['time']
    assert ns['test_proxy']('youtube','')['ok'] is False
    refused=ns['test_proxy']('youtube','http://127.0.0.1:9')
    assert refused['ok'] is False and refused['steps'][-1]['name']=='代理端口'
    def plugin_zip(entries):
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for item, contents in entries:
                if isinstance(item, zipfile.ZipInfo): archive.writestr(item, contents)
                else: archive.writestr(item, contents)
        return base64.b64encode(output.getvalue()).decode()
    valid_zip = plugin_zip([('yt_dlp_plugins/extractor/demo.py', 'class Demo: pass\n'), ('plugin.json', '{"name":"Demo plugin","version":"1.2"}')])
    installed = ns['install_plugin']('Demo Plugin', valid_zip, True)
    assert installed['id'] == 'demo-plugin' and installed['name'] == 'Demo plugin' and installed['version'] == '1.2'
    assert (ns['PERSISTENT_PLUGINS'] / 'demo-plugin/yt_dlp_plugins/extractor/demo.py').is_file()
    assert ns['list_plugins']()[0]['persistent'] is True
    args = ns['ytdlp_args'](['--version'])
    assert args.count('--plugin-dirs') == 2 and str(ns['PERSISTENT_PLUGINS']) in args and str(ns['RUNTIME_PLUGINS']) in args
    for unsafe in [plugin_zip([('../escape.py', 'x')]), plugin_zip([('plugin.txt', 'x')])]:
        try: ns['validate_plugin_zip'](unsafe)
        except ValueError: pass
        else: raise AssertionError('unsafe or Python-free ZIP accepted')
    link = zipfile.ZipInfo('linked.py'); link.external_attr = 0o120777 << 16
    try: ns['validate_plugin_zip'](plugin_zip([(link, 'target')]))
    except ValueError: pass
    else: raise AssertionError('symbolic link ZIP member accepted')
print('PASS: cookie roundtrip/account, YouTube cookie save, QR status mapping, media metadata, plugin ZIP validation/install')
