"""插件 ZIP 的安全边界与安装回退的回归测试。

这些约束在重构 plugin manager 时最容易被静默放宽或删掉：一旦 `..`、绝对路径、
Windows 路径、symlink 能进 plugins 目录，ZIP 就能写到目录之外。测试只调用
server.py 里现成的 validate_plugin_zip / safe_plugin_id / install_plugin，不联网、
不碰真实 /data（数据目录指向临时目录）。
"""
import base64, io, json, os, tempfile, zipfile
from pathlib import Path
from testkit import Checks, load_server

checks = Checks()

def zip_bytes(entries, compress=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compress) as archive:
        for entry in entries:
            if isinstance(entry, tuple): archive.writestr(entry[0], entry[1])
            else: archive.writestr(entry, 'x')
    return buffer.getvalue()

def as_base64(raw): return base64.b64encode(raw).decode()

with tempfile.TemporaryDirectory() as tmp:
    ns = load_server(Path(tmp) / 'data', Path(tmp) / 'downloads')
    validate, safe_id, install = ns['validate_plugin_zip'], ns['safe_plugin_id'], ns['install_plugin']
    good = zip_bytes([('plugin.json', json.dumps({'name': '测试插件', 'version': '2.3'})), ('main.py', 'print(1)')])

    # --- 正常 ZIP ---
    raw, manifest = validate(as_base64(good))
    checks.check('正常 ZIP 接受', raw == good, len(raw))
    checks.check('manifest 解析出来', manifest.get('name') == '测试插件' and manifest.get('version') == '2.3', manifest)

    # --- 路径类：一律拒绝 ---
    checks.rejects('成员 ../escape.py', validate, as_base64(zip_bytes([('../escape.py', 'x'), ('main.py', 'x')])))
    checks.rejects('成员 a/b/../../x.py', validate, as_base64(zip_bytes([('a/b/../../x.py', 'x')])))
    checks.rejects('成员 绝对路径 /etc/x.py', validate, as_base64(zip_bytes([('/etc/x.py', 'x')])))
    checks.rejects('成员 Windows 盘符 C:/x.py', validate, as_base64(zip_bytes([('C:/x.py', 'x')])))
    checks.rejects('成员 反斜杠穿越 ..\\..\\x.py', validate, as_base64(zip_bytes([('..\\..\\x.py', 'x')])))
    checks.rejects('成员 反斜杠盘符 C:\\x.py', validate, as_base64(zip_bytes([('C:\\x.py', 'x')])))

    # --- symlink ---
    link = zipfile.ZipInfo('linked.py'); link.external_attr = 0o120777 << 16
    checks.rejects('成员 symlink', validate, as_base64(zip_bytes([link, ('main.py', 'x')])))

    # --- 体积 / 编码 / 归档本身 ---
    big = zip_bytes([('main.py', os.urandom(10 * 1024 * 1024 + 1))], zipfile.ZIP_STORED)
    checks.check('构造的样本确实超过 10 MiB', len(big) > ns['PLUGIN_MAX_BYTES'], len(big))
    checks.rejects('超过 10 MiB', validate, as_base64(big))
    near = zip_bytes([('main.py', os.urandom(10 * 1024 * 1024 - 8192))], zipfile.ZIP_STORED)
    checks.check('10 MiB 以内仍接受', len(validate(as_base64(near))[0]) == len(near), len(near))
    checks.rejects('非法 base64', validate, 'not-base64!!')
    checks.rejects('base64 合法但不是 ZIP', validate, as_base64(b'hello world'))
    checks.rejects('没有任何 .py', validate, as_base64(zip_bytes([('readme.txt', 'x')])))
    checks.rejects('只有目录条目 dir.py/', validate, as_base64(zip_bytes([('dir.py/', '')])))
    checks.rejects('plugin.json 不是合法 JSON', validate, as_base64(zip_bytes([('plugin.json', '{oops'), ('main.py', 'x')])))
    checks.rejects('plugin.json 不是 UTF-8', validate, as_base64(zip_bytes([('plugin.json', b'\xff\xfe\x00'), ('main.py', 'x')])))

    # --- 现状：没有 plugin.json 也能装，非对象 JSON 只是不被当 manifest ---
    checks.check('没有 plugin.json 仍接受（manifest 为空）', validate(as_base64(zip_bytes([('main.py', 'x')])))[1] == {})
    checks.check('plugin.json 是数组时忽略而非报错', validate(as_base64(zip_bytes([('plugin.json', '[1,2]'), ('main.py', 'x')])))[1] == {})

    # --- 插件名净化 ---
    checks.check('名字净化成 slug', safe_id('My Plugin!!') == 'my-plugin', safe_id('My Plugin!!'))
    checks.check('名字里的穿越路径被抹掉', safe_id('../etc') == 'etc', safe_id('../etc'))
    checks.rejects('净化后为空的非 ASCII 名字', safe_id, '中文')
    checks.rejects('空名字', safe_id, '   ')
    checks.rejects('非字符串名字', safe_id, 123)

    # --- 安装：落盘位置、manifest 覆盖、重装备份、临时目录清理 ---
    root = ns['PERSISTENT_PLUGINS']
    first = install('Demo Plugin', as_base64(good), True)
    checks.check('装到 plugins/persistent 下', (root / first['id']).is_dir() and first['persistent'] is True, first)
    checks.check('manifest 的名字/版本覆盖目录名', first['name'] == '测试插件' and first['version'] == '2.3', first)
    checks.check('安装没留下暂存目录', not [p for p in root.iterdir() if p.name.startswith('.install-')], [p.name for p in root.iterdir()])
    second = install('Demo Plugin', as_base64(zip_bytes([('plugin.json', json.dumps({'version': '3.0'})), ('main.py', 'print(2)')])), True)
    backups = sorted(p.name for p in ns['BACKUPS'].iterdir() if p.name.startswith('plugin-'))
    checks.check('重装前把旧版备份走', len(backups) == 1, backups)
    checks.check('旧版留在备份里', (ns['BACKUPS'] / backups[0] / 'main.py').read_text() == 'print(1)')
    checks.check('新版覆盖生效', second['version'] == '3.0' and (root / second['id'] / 'main.py').read_text() == 'print(2)')
    checks.check('列表里只有一份（不会重复出现）', [item['id'] for item in ns['list_plugins']()] == ['demo-plugin'], ns['list_plugins']())
    checks.rejects('安装非法 ZIP 被拒', install, 'bad', as_base64(zip_bytes([('../x.py', 'x')])), True)

checks.done()
