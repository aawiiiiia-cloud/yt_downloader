"""Validate explicitly selected embeds and reuse media-only Vimeo browser config."""
from __future__ import annotations
import copy
import html
import json
import re
import time
from urllib.parse import urlparse, parse_qs, urlencode, urljoin


def external_url(value):
    try:
        u=urlparse(html.unescape(str(value)))
        if u.scheme!='https' or u.username or u.password or u.port not in (None,443): return None
        if u.hostname in ('www.youtube.com','youtube.com','www.youtube-nocookie.com','youtube-nocookie.com','youtu.be'):
            match=re.fullmatch(r'/embed/([\w-]{11})/?',u.path)
            identifier=u.path[1:] if u.hostname=='youtu.be' else match[1] if match else parse_qs(u.query).get('v',[''])[0]
            if re.fullmatch(r'[\w-]{11}',identifier): return {'provider':'youtube','id':identifier,'url':'https://www.youtube.com/watch?v='+identifier}
        if u.hostname in ('player.vimeo.com','vimeo.com','www.vimeo.com'):
            match=re.fullmatch(r'/(?:video/)?(\d+)(?:/([\da-f]+))?/?',u.path,re.I)
            access=(match[2] if match else '') or parse_qs(u.query).get('h',[''])[0]
            if match and (not access or re.fullmatch(r'[\da-f]{1,64}',access,re.I)):
                return {'provider':'vimeo','id':match[1],'url':'https://player.vimeo.com/video/'+match[1]+('?' + urlencode({'h':access}) if access else '')}
    except (ValueError,TypeError): pass
    return None


def validate_external(url,source_url,context):
    from behance_adapter import project_id
    try:
        identifier=project_id(source_url)
        project=context['project']; source=context['externalSource']
        if str(context['projectId'])!=identifier or str(project['id'])!=identifier: raise ValueError()
        target=external_url(url)
        if not target: raise ValueError()
        for index,module in enumerate(project.get('modules',[])[:300]):
            if module.get('__typename') not in ('EmbedModule','VideoModule'): continue
            key=f"{str(module.get('id') or 'module')[:80]}-{index}"
            embed=module.get('originalEmbed') or module.get('fluidEmbed') or module.get('embed') or ''
            match=re.search(r'\bsrc=["\']([^"\']+)',embed,re.I)
            if key==source.get('key') and match and external_url(match[1])==target: return target
    except (ValueError,KeyError,TypeError,AttributeError): pass
    raise ValueError('外部平台托管的视频默认不下载，请在作品页手动解析外部视频后再选择下载')


def media_url(value):
    u=urlparse(str(value));host=(u.hostname or '').lower()
    if (u.scheme!='https' or u.username or u.password or u.port not in (None,443)
            or not any(host==d or host.endswith('.'+d) for d in ('vimeocdn.com','vimeo.com','akamaized.net'))):
        raise ValueError('播放器媒体地址不受支持，请重新解析外部视频')
    return value


def browser_vimeo(url,context,ydl,check_cancel=lambda:None):
    data=(context or {}).get('vimeo')
    if not data:return None
    target=external_url(url)
    if not target or target['provider']!='vimeo': raise ValueError('播放器身份不匹配，请重新解析')
    if len(json.dumps(data))>1_500_000: raise ValueError('播放器数据过大，请重新解析')
    if str(data.get('videoId'))!=target['id'] or str((data.get('config') or {}).get('video',{}).get('id'))!=target['id']:
        raise ValueError('播放器内容已变化，请重新解析')
    captured=data.get('capturedAt')
    if not isinstance(captured,(int,float)) or not -30_000<time.time()*1000-captured<300_000:
        raise ValueError('外部视频地址已过期，请回到作品页重新解析外部视频')
    config=copy.deepcopy(data['config']);files=config.get('request',{}).get('files') or config.get('video',{}).get('files') or {}
    for row in files.get('progressive') or []:media_url(row.get('url'))
    for kind in ('hls','dash'):
        for row in (files.get(kind) or {}).get('cdns',{}).values():
            for name in ('url','avc_url'):
                if row.get(name):media_url(row[name])
    for track in config.get('request',{}).get('text_tracks') or []:
        media_url(urljoin('https://player.vimeo.com/',track.get('url') or ''))
    check_cancel()
    ydl.params.setdefault('http_headers',{})['Referer']='https://player.vimeo.com/'
    from vimeo_extractor import ParallelVimeoIE
    info=ParallelVimeoIE(ydl,check_cancel)._parse_config(config,target['id'])
    check_cancel()
    for row in info.get('formats') or []:media_url(row.get('url'))
    if not info.get('formats'):raise ValueError('播放器暂未返回可下载画质，请稍后重新解析外部视频')
    info.update(extractor='vimeo:browser',extractor_key='Vimeo',webpage_url=url)
    return info
