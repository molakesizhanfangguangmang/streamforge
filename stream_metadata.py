"""Derive per-stream display metadata from yt-dlp's own payload.

yt-dlp reports resolution, fps, dynamic_range, codecs, bitrates and sizes in a
single --dump-single-json call, so nothing in this module performs network I/O.
Only two fields are missing from that payload: bit depth and the Dolby family.
Both are inferred from dynamic_range and acodec, replacing the earlier pass that
probed every stream URL with ffprobe -- for a YouTube video that meant dozens of
network round trips and minutes of waiting before the format list could be sent.
"""

DOLBY_CODECS = {
    'ac-3': 'Dolby Digital',
    'ac3': 'Dolby Digital',
    'ec-3': 'Dolby Digital Plus',
    'eac3': 'Dolby Digital Plus',
    'truehd': 'Dolby TrueHD',
}
HDR_MARKERS = ('hdr', 'hlg', 'dv', 'dolby vision')
NO_DOLBY = '无'


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
