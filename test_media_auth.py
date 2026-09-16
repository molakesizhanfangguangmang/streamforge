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
    saved = MozillaCookieJar(str(ns['ROOT'] / 'cookies/bilibili.txt')); saved.load(ignore_discard=True)
    assert list(saved)[0].value == 'manual-last'
    try: ns['save_bilibili_text']('# Netscape HTTP Cookie File\n.bilibili.com\tTRUE\t/\tTRUE\t1900000000\tb_nut\tno-session\n')
    except ValueError: pass
    else: raise AssertionError('missing SESSDATA accepted')
    with patch.dict(ns, bilibili_request=lambda *a: io.BytesIO(json.dumps({'data': {'isLogin': True, 'mid': 123, 'uname': 'offline'}}).encode())):
        assert ns['bilibili_auth']()['bilibili_account']['name'] == 'offline'
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
print('PASS: cookie roundtrip/account, QR status mapping, media metadata, plugin ZIP validation/install')
