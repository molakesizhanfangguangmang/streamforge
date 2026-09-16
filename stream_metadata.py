"""Derive per-stream display metadata from yt-dlp's own payload.

yt-dlp reports resolution, fps, dynamic_range, codecs, bitrates and sizes in a
single --dump-single-json call, so the format list itself needs no network I/O.
Only two fields are missing from that payload: bit depth and the Dolby family.
Both are inferred from dynamic_range and acodec, replacing the earlier pass that
probed every stream URL with ffprobe -- for a YouTube video that meant dozens of
network round trips and minutes of waiting before the format list could be sent.

The one thing the payload cannot tell is what an E-AC-3 stream carries inside:
an Atmos (JOC) layer and a plain 5.1 track look identical to the extractor. So
probe_best_audio() reads the bitstream of the single highest-quality audio
stream, and only that one.
"""

import json
import os
import subprocess
import time

DOLBY_CODECS = {
    'ac-3': 'Dolby Digital',
    'ac3': 'Dolby Digital',
    'ec-3': 'Dolby Digital Plus',
    'eac3': 'Dolby Digital Plus',
    'truehd': 'Dolby TrueHD',
}
HDR_MARKERS = ('hdr', 'hlg', 'dv', 'dolby vision')
NO_DOLBY = '无'
PROBE_TIMEOUT = 20


def normalized(value):
    return str(value or '').strip().lower()


def dynamic_range(info, fmt):
    return normalized(fmt.get('dynamic_range') or info.get('dynamic_range'))


def bit_depth_from_dynamic_range(value):
    """HDR profiles carry 10-bit samples on YouTube; plain SDR carries 8-bit."""
    value = normalized(value)
    if not value:
        return None
    if value == 'sdr':
        return 8
    return 10 if any(marker in value for marker in HDR_MARKERS) else None


def dolby_from_codec(value):
    """ac-3/ec-3/truehd are the Dolby family; AAC and Opus are not."""
    return DOLBY_CODECS.get(normalized(value), NO_DOLBY)


def fill_format(info, fmt):
    video = fmt.get('vcodec') not in (None, 'none')
    audio = fmt.get('acodec') not in (None, 'none')
    if video:
        depth = bit_depth_from_dynamic_range(dynamic_range(info, fmt))
        if depth:
            fmt['bit_depth'] = depth
    elif audio:
        fmt['dolby'] = dolby_from_codec(fmt.get('acodec'))


def enrich_formats(info):
    for fmt in info.get('formats', []):
        fill_format(info, fmt)
    return info


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return -1.0


def audio_rank(fmt):
    """The same ordering the format table uses: bitrate, channels, sample rate."""
    size = fmt.get('filesize') or fmt.get('filesize_approx') or 0
    return (number(fmt.get('abr')), number(fmt.get('audio_channels')), number(fmt.get('asr')), number(size))


def best_audio(info):
    audios = [fmt for fmt in info.get('formats', [])
              if fmt.get('vcodec') in (None, 'none') and fmt.get('acodec') not in (None, 'none')]
    return max(audios, key=audio_rank) if audios else None


def probe_args(info, fmt):
    headers = dict(info.get('http_headers') or {})
    headers.update(fmt.get('http_headers') or {})
    args = ['ffprobe', '-v', 'error', '-rw_timeout', '8000000',
            '-analyzeduration', '2000000', '-probesize', '2000000']
    if headers:
        args += ['-headers', ''.join(f'{k}: {v}\r\n' for k, v in headers.items()
                                     if '\n' not in str(v) and '\r' not in str(v))]
    return args + ['-show_streams', '-of', 'json', fmt['url']]


def dolby_from_stream(stream):
    dolby = DOLBY_CODECS.get(normalized(stream.get('codec_name')), NO_DOLBY)
    if 'atmos' in str(stream.get('profile') or '').lower():
        dolby = (dolby if dolby != NO_DOLBY else 'Dolby') + ' / Atmos'
    return dolby


def probe_best_audio(info, proxy=''):
    """Read the bitstream of the highest-quality audio stream only.

    Returns a short summary of what was found, or None when the stream could not
    be reached. On success the stream's dolby field is replaced with the value
    read from the bitstream.
    """
    fmt = best_audio(info)
    if not fmt or not str(fmt.get('url') or '').startswith(('http://', 'https://')):
        return None
    env = dict(os.environ)
    if proxy:
        env['http_proxy'] = env['https_proxy'] = proxy
    started = time.time()
    try:
        result = subprocess.run(probe_args(info, fmt), capture_output=True, text=True,
                                timeout=PROBE_TIMEOUT, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    try:
        streams = json.loads(result.stdout).get('streams') or []
    except ValueError:
        return None
    stream = next((item for item in streams if item.get('codec_type') == 'audio'), None)
    if not stream:
        return None
    fmt['dolby'] = dolby_from_stream(stream)
    fmt['metadata_source'] = 'ffprobe'
    return {'id': fmt.get('format_id'), 'dolby': fmt['dolby'],
            'codec': normalized(stream.get('codec_name')),
            'profile': str(stream.get('profile') or ''),
            'elapsed': round(time.time() - started, 1)}
