"""Read media stream headers without downloading or transcoding the full media."""
import concurrent.futures
import json
import re
import subprocess
from fractions import Fraction


def number(value):
    try:
        n = float(Fraction(str(value)))
        return n if n > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


def probe_format(info, fmt):
    if not fmt.get('url', '').startswith(('https://', 'http://')):
        return
    headers = dict(info.get('http_headers') or {})
    headers.update(fmt.get('http_headers') or {})
    args = ['ffprobe', '-v', 'error', '-rw_timeout', '10000000',
            '-analyzeduration', '2000000', '-probesize', '2000000']
    if headers:
        args += ['-headers', ''.join(f'{k}: {v}\r\n' for k, v in headers.items() if '\n' not in str(v) and '\r' not in str(v))]
    args += ['-show_streams', '-of', 'json', fmt['url']]
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=18)
        if p.returncode:
            return
        video = fmt.get('vcodec') not in (None, 'none')
        stream = next((s for s in json.loads(p.stdout).get('streams', [])
                       if s.get('codec_type') == ('video' if video else 'audio')), None)
        if not stream:
            return
        fmt['metadata_source'] = 'ffprobe'
        if video:
            fps = number(stream.get('avg_frame_rate')) or number(stream.get('r_frame_rate'))
            if fps:
                fmt['fps'] = fps
            pix = stream.get('pix_fmt', '')
            depth = number(stream.get('bits_per_raw_sample'))
            match = re.search(r'(?:p|gbrp|gray)(\d+)(?:le|be)?$', pix)
            if not depth and match:
                depth = int(match.group(1))
            if not depth and pix in ('yuv420p', 'yuv422p', 'yuv444p', 'yuvj420p', 'yuvj422p', 'yuvj444p', 'nv12', 'rgb24', 'bgr24', 'gbrp'):
                depth = 8
            fmt['bit_depth'] = int(depth) if depth else None
            side = stream.get('side_data_list') or []
            dovi = any('dovi' in s.get('side_data_type', '').lower() or 'dolby vision' in s.get('side_data_type', '').lower() for s in side)
            transfer = stream.get('color_transfer')
            if dovi:
                fmt['dynamic_range'] = 'Dolby Vision'
                fmt['dolby'] = 'Dolby Vision'
            elif transfer == 'smpte2084':
                fmt['dynamic_range'] = 'HDR10 (PQ)'
                fmt['dolby'] = '未检测到'
            elif transfer == 'arib-std-b67':
                fmt['dynamic_range'] = 'HLG'
                fmt['dolby'] = '未检测到'
            elif transfer in ('bt709', 'smpte170m', 'iec61966-2-1', 'gamma22', 'gamma28'):
                fmt['dynamic_range'] = 'SDR'
                fmt['dolby'] = '未检测到'
            elif fmt.get('dynamic_range') == 'SDR':
                # yt-dlp may supply SDR as a default without bitstream evidence.
                fmt['dynamic_range'] = None
        else:
            fmt['asr'] = number(stream.get('sample_rate'))
            fmt['audio_channels'] = stream.get('channels')
            fmt['bit_depth'] = number(stream.get('bits_per_raw_sample'))
            codec = stream.get('codec_name')
            fmt['dolby'] = {'ac3': 'Dolby Digital', 'eac3': 'Dolby Digital Plus', 'truehd': 'Dolby TrueHD'}.get(codec, '未检测到')
            profile = str(stream.get('profile', ''))
            if 'atmos' in profile.lower():
                fmt['dolby'] = (fmt.get('dolby') or 'Dolby') + ' / Atmos'
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return


def enrich_formats(info):
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda f: probe_format(info, f), info.get('formats', [])))
    return info
