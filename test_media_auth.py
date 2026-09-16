import ast, base64, io, json, os, sys, tempfile, zipfile
from pathlib import Path
from http.cookiejar import Cookie, CookieJar, MozillaCookieJar
from unittest.mock import patch
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
    def probe(stream, fmt):
        class P: returncode=0; stdout=json.dumps({'streams':[stream]})
        with patch.object(m.subprocess, 'run', return_value=P()): m.probe_format({},fmt)
    video={'url':'https://example.test/v','vcodec':'hevc'}
    probe({'codec_type':'video','avg_frame_rate':'60000/1001','pix_fmt':'yuv420p10le','color_transfer':'smpte2084'},video)
    assert abs(video['fps']-59.94)<.001 and video['bit_depth']==10 and video['dynamic_range']=='HDR10 (PQ)'
    probe({'codec_type':'video','avg_frame_rate':'60/1','pix_fmt':'yuv420p10le','side_data_list':[{'side_data_type':'DOVI configuration record'}]},video)
    assert video['dolby']=='Dolby Vision'
    audio={'url':'https://example.test/a','vcodec':'none'}
    probe({'codec_type':'audio','codec_name':'eac3','sample_rate':'48000','channels':6},audio)
    assert audio['asr']==48000 and audio['audio_channels']==6 and audio['dolby']=='Dolby Digital Plus'
    assert ns['formats']({'formats':[video,audio]})['formats'][0]['bit_depth']==10
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
