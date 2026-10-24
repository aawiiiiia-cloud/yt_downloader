from __future__ import annotations
import tempfile
import unittest
from unittest import mock
from collector_core import Collector, checked_url, vimeo_player_url, selected_video_format, browser_douyin, normalise_video
from download_core import site_for_url
from edge_login import has_site_auth_cookie, get_site_cookies


class NewSites(unittest.TestCase):
    def fixture(self):
        return {'videoId':'123','detail':{'aweme_id':'123','desc':'受控抖音视频','author':{'nickname':'作者'},'video':{
            'duration':58000,'width':640,'height':360,'playAddr':[{'src':'https://fixture.douyinvod.com/v.mp4'}],
            'cover':'https://p3.douyinpic.com/cover.jpg'}}}

    def test_douyin_browser_data_reuses_ytdlp_formats_without_api_requests(self):
        import yt_dlp
        with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
            info=browser_douyin('https://www.douyin.com/video/123',self.fixture(),ydl)
            item=normalise_video(info,'https://www.douyin.com/video/123')
        self.assertEqual(item['duration'],58);self.assertEqual(item['formats'][0]['height'],360)
        self.assertEqual(item['thumbnail'],'https://p3.douyinpic.com/cover.jpg')
        self.assertNotIn('v.mp4',str(item));self.assertEqual(item['author'],'作者')

    def test_douyin_stale_ids_and_untrusted_streams_cannot_be_used(self):
        self.assertIsNone(browser_douyin('https://www.douyin.com/video/456',self.fixture(),None))
        for bad in ('http://127.0.0.1/private','https://douyinvod.com.evil.invalid/v.mp4','https://user:pass@v3.douyinvod.com/v.mp4'):
            data=self.fixture();data['detail']['video']['playAddr']=[{'src':bad}]
            with self.assertRaises(ValueError):browser_douyin('https://www.douyin.com/video/123',data,None)

    def test_douyin_browser_bitrates_keep_sizes_codecs_and_skip_unpaired_dash(self):
        import yt_dlp
        data=self.fixture();video=data['detail']['video'];video['width']=2560;video['height']=1440
        video['playAddr']=[{'src':'https://v3-dy-o.zjcdn.com/1080.mp4'}]
        video['bitRateList']=[
            {'gearName':'normal_1080_0','playAddr':video['playAddr'],'width':1920,'height':1080,'dataSize':8265803,'fps':30,'format':'mp4'},
            {'gearName':'normal_1080_0','playAddr':[{'src':'https://v3-dy-o.zjcdn.com/video-only.mp4'}],'format':'dash'},
            {'gearName':'adapt_1440_1','playAddr':[{'src':'https://v3-dy-o.zjcdn.com/1440.mp4'}],'width':2560,'height':1440,'isH265':1,'dataSize':7173724,'format':'mp4'},
        ]
        with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
            info=browser_douyin('https://www.douyin.com/video/123',data,ydl)
        self.assertEqual(len(info['formats']),2)
        first,second=info['formats'];self.assertEqual((first['height'],first['filesize'],first['fps']),(1080,8265803,30))
        self.assertEqual((second['height'],second['vcodec']),(1440,'h265'))
        self.assertNotIn('video-only',str(info['formats']))

    def test_douyin_missing_browser_data_falls_back_with_actionable_error(self):
        service=Collector({'url':'https://www.douyin.com/video/123'},lambda *_:None)
        with self.assertRaisesRegex(RuntimeError,'左侧'):
            service.error('Fresh cookies (not necessarily logged in) are needed')

    def test_routes_and_canonical_douyin_modal(self):
        self.assertEqual(site_for_url('https://player.vimeo.com/video/123'), 'vimeo')
        self.assertEqual(checked_url('https://www.douyin.com/?modal_id=123&from=feed'), 'https://www.douyin.com/video/123')
        self.assertEqual(checked_url('https://www.iesdouyin.com/share/video/123/'), 'https://www.douyin.com/video/123')
        with self.assertRaisesRegex(ValueError, '图集'):
            checked_url('https://www.douyin.com/note/123')
        for url in ('https://vimeo.com.evil.invalid/123','https://douyin.com.evil.invalid/video/123'):
            with self.assertRaises(ValueError): checked_url(url)

    def test_vimeo_player_preserves_unlisted_access_hash(self):
        self.assertEqual(vimeo_player_url('https://vimeo.com/123'), 'https://player.vimeo.com/video/123')
        self.assertEqual(vimeo_player_url('https://vimeo.com/123/ab12cd34'), 'https://player.vimeo.com/video/123?h=ab12cd34')
        self.assertEqual(vimeo_player_url('https://vimeo.com/channels/staffpicks/123?h=ab12'), 'https://player.vimeo.com/video/123?h=ab12')
        self.assertEqual(vimeo_player_url('https://vimeo.com/showcase/123'), 'https://vimeo.com/showcase/123')

    def test_vimeo_audio_without_codec_is_retained_and_same_cdn_is_preferred(self):
        import yt_dlp
        video={'format_id':'hls-akfire-443','url':'https://fixture.vimeocdn.com/v.mp4','vcodec':'h264','acodec':'none','height':240,'ext':'mp4'}
        audio={'format_id':'hls-akfire-audio-high-English','url':'https://fixture.vimeocdn.com/a.mp4','vcodec':'none','acodec':None,'ext':'m4a'}
        other={**audio,'format_id':'hls-other-audio-high-English'}
        normal=normalise_video({'id':'123','formats':[video,audio]},'https://vimeo.com/123')
        self.assertEqual(len(normal['formats']),2);self.assertTrue(normal['formats'][1]['audio'])
        self.assertFalse(normal['formats'][1]['video'])
        with yt_dlp.YoutubeDL({'quiet':True}) as ydl:
            selector=ydl.build_format_selector(selected_video_format('https://vimeo.com/123',video['format_id'],False))
            for tracks,expected in (([audio,other,video],audio['format_id']),([other,video],other['format_id'])):
                result=list(selector({'formats':tracks,'incomplete_formats':False,'has_merged_format':False}))
                self.assertEqual(result[0]['requested_formats'][1]['format_id'],expected)

    def test_vimeo_inspection_and_download_use_the_same_player(self):
        url='https://vimeo.com/123/ab12'; target='https://player.vimeo.com/video/123?h=ab12'
        with tempfile.TemporaryDirectory() as directory:
            service=Collector({'url':url,'settings':{'saveDir':directory}},lambda *_:None)
            with mock.patch.object(service,'options',return_value={}),mock.patch('yt_dlp.YoutubeDL') as factory:
                ydl=factory.return_value.__enter__.return_value
                ydl.extract_info.return_value={'id':'123','title':'公开播放器', 'formats':[{'format_id':'http-360p','url':'https://fixture.vimeocdn.com/v.mp4','vcodec':'h264','acodec':'aac','height':360}]}
                result=service.inspect();ydl.extract_info.assert_called_once_with(target,download=False)
                self.assertEqual(result['site'],'vimeo');self.assertEqual(result['items'][0]['url'],url)
                ydl.download.return_value=0
                service.download();ydl.download.assert_called_once_with([target])

    def test_new_edge_login_configs_do_not_accept_anonymous_cookies(self):
        self.assertFalse(has_site_auth_cookie([{'domain':'.douyin.com','name':'ttwid','value':'anonymous'}],'douyin'))
        self.assertTrue(has_site_auth_cookie([{'domain':'.douyin.com','name':'sessionid','value':'controlled'}],'douyin'))
        self.assertTrue(has_site_auth_cookie([{'domain':'.vimeo.com','name':'vimeo','value':'controlled'}],'vimeo'))
        self.assertEqual(get_site_cookies([{'domain':'.douyin.com','name':'ttwid'},{'domain':'.vimeo.com','name':'vimeo'}],'vimeo'),[{'domain':'.vimeo.com','name':'vimeo'}])

    def test_login_cookies_reject_expired_empty_and_lookalike_domains(self):
        for site, name in (('vimeo','vimeo'),('douyin','sessionid')):
            base={'domain':'.'+site+'.com','name':name,'value':'controlled'}
            for patch in ({'expires':123},{'value':''},{'domain':site+'.com.evil.invalid'}):
                self.assertFalse(has_site_auth_cookie([{**base,**patch}],site))
            self.assertTrue(has_site_auth_cookie([{**base,'expires':-1}],site))
            self.assertEqual(get_site_cookies([{**base,'domain':site+'.com.evil.invalid'}],site),[])


if __name__=='__main__': unittest.main()
