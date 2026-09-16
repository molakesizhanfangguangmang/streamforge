import tempfile
from pathlib import Path
from xml.etree.ElementTree import fromstring
from testkit import Checks, load_server

checks = Checks()
check = checks.check

with tempfile.TemporaryDirectory() as tmp:
    ns = load_server(Path(tmp) / 'data', Path(tmp) / 'downloads')

    check('默认开启封面转 JPG', ns['config'].get('thumbnail_jpg') is True, ns['config'].get('thumbnail_jpg'))

    # --- plot 渲染：实体解一层、换行转 <br/>、链接可点、CDATA 可解析 ---
    description = 'Tom & Jerry &#39;cue&#39;\n第二行]]>结束\nhttps://example.com/a?x=1&y=2'
    markup = ns['plot_markup'](description, 'https://www.bilibili.com/video/BV1xx/', 'BV1xx')
    check('简介前缀指向原始视频', markup.startswith('原始视频：<a href="https://www.bilibili.com/video/BV1xx/">BV1xx</a><br/><br/>'), markup[:60])
    check('HTML 实体只解一层', "Tom &amp; Jerry 'cue'" in markup and '&amp;#39;' not in markup, markup[:120])
    check('换行转 <br/>', '<br/>第二行' in markup)
    check('裸链接包成 <a>', '<a href="https://example.com/a?x=1&amp;y=2">https://example.com/a?x=1&amp;y=2</a>' in markup, markup[-160:])
    check('CDATA 逃逸 ]]>', ']]]]><![CDATA[>' in ns['cdata']('a ]]> b'))

    # --- nfo：文件名与视频同名、可被解析、简介包裹在 CDATA 里 ---
    folder = Path(tmp) / 'downloads' / '示例标题'
    folder.mkdir(parents=True)
    media = folder / '示例标题.mp4'
    media.write_bytes(b'0' * 4096)
    thumb = folder / '示例标题.jpg'
    thumb.write_bytes(b'jpg')
    job = {'url': 'https://www.bilibili.com/video/BV1xx/', 'format': '30126+30250', 'platform': 'bilibili',
           'title': '示例标题', 'source_info': {'id': 'BV1xx', 'upload_date': '20260126', 'uploader': 'UP名', 'description': description}}
    stem = ns['media_file'](folder, []).stem
    check('media_file 取到视频基名', stem == '示例标题', stem)
    poster, fanart = ns['place_cover'](folder, stem)
    check('封面改名为 -poster.jpg', poster is not None and poster.name == '示例标题-poster.jpg', poster)
    check('生成 -fanart.jpg 副本', fanart is not None and fanart.is_file() and fanart.read_bytes() == b'jpg', fanart)
    check('原缩略图已改名（不再存在）', not thumb.exists())
    ns['write_nfo'](folder, job, stem)
    document_path = folder / '示例标题.nfo'
    check('nfo 与视频同名', document_path.is_file(), sorted(p.name for p in folder.iterdir()))
    root = fromstring(document_path.read_text())
    plot = (root.findtext('plot') or '')
    check('plot 为 CDATA 且含 HTML', '<a href=' in plot and '<br/>' in plot, plot[:80])
    check('nfo 里没有 &amp;#39; 这种双重转义', '&amp;#39;' not in document_path.read_text())
    check('uniqueid 带平台类型', root.find('uniqueid').get('type') == 'bilibili' and root.findtext('uniqueid') == 'BV1xx')
    check('title 保留', root.findtext('title') == '示例标题')

    # --- 图片是 webp 时（关闭转换）文件名要如实使用 webp ---
    webp_folder = Path(tmp) / 'downloads' / 'webp'
    webp_folder.mkdir(parents=True)
    (webp_folder / 'webp.mp4').write_bytes(b'0' * 2048)
    (webp_folder / 'webp.webp').write_bytes(b'webp')
    webp_poster, webp_fanart = ns['place_cover'](webp_folder, 'webp')
    check('webp 封面保持 webp 后缀', webp_poster.name == 'webp-poster.webp' and webp_fanart.name == 'webp-fanart.webp', webp_poster)

    # --- 没有缩略图时不应报错，nfo 仍然写出 ---
    bare_folder = Path(tmp) / 'downloads' / '无封面'
    bare_folder.mkdir(parents=True)
    (bare_folder / '无封面.mp4').write_bytes(b'0' * 1024)
    check('无缩略图时 place_cover 返回空', ns['place_cover'](bare_folder, '无封面') == (None, None))
    ns['finalize_metadata'](bare_folder, {**job, 'title': '无封面'}, [])
    check('无缩略图仍写出 nfo', (bare_folder / '无封面.nfo').is_file())

    # --- 合并任务用日志里的最终路径 ---
    merged = folder / '合并结果.mkv'
    merged.write_bytes(b'0' * 16)
    (folder / '临时.mp4').write_bytes(b'0' * 999999)
    found = ns['media_file'](folder, ['[Merger] Merging formats into "%s"' % merged])
    check('优先采用 Merger 行里的文件', found == merged, found)

checks.done()
