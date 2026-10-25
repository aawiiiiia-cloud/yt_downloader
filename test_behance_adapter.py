from __future__ import annotations
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from behance_adapter import BehanceProject, trusted_url, embed_url, resolve_video
from collector_core import Collector, Cancelled, checked_url
from test_xhs_image_downloader import _Response

URL = 'https://www.behance.net/gallery/123/Test'
FIXTURE = Path(__file__).parent / 'electron/fixtures/behance.json'

def project():
    return json.loads(FIXTURE.read_text(encoding='utf8'))

def service(data=None, **kwargs):
    return Collector({'url': URL, 'context': {'projectId': '123', 'project': data or project()}, **kwargs}, lambda *_: None)

def native_video():
    return {'id': 14, '__typename': 'VideoModule', 'videoData': {'renditions': [
        {'url': 'https://cdn-prod-ccv.adobe.com/fixture/video.mp4?signature=private-stream'}]}}

class BehanceTests(unittest.TestCase):
    def test_routes_require_a_project_and_remove_tracking(self):
        self.assertEqual(checked_url(URL + '?tracking_source=search&l=0'), URL)
        for url in ('https://www.behance.net/artist', 'https://www.behance.net/collection/123', 'https://user:pass@www.behance.net/gallery/123', 'https://behance.net.evil.invalid/gallery/123'):
            with self.assertRaises(ValueError): checked_url(url)

    def test_real_gallerydl_parser_uses_loaded_images_collections_and_external_youtube(self):
        with mock.patch('requests.Session.send', side_effect=AssertionError('network not allowed')):
            result = service().inspect()
        images, video = result['items']
        self.assertEqual([row['index'] for row in images['images']], [1, 2])
        self.assertIn('/source/photo.jpg', images['images'][0]['url'])
        self.assertIn('/disp/photo.jpg', images['images'][0]['preview_url'])
        self.assertIn('/source/animation.gif', images['images'][1]['url'])
        self.assertEqual(video['kind'], 'external')
        self.assertEqual(video['url'], 'https://www.youtube.com/watch?v=SmQd5BgLmd0')

    def test_stale_context_falls_back_to_the_requested_project(self):
        worker = service(); worker.request['context']['projectId'] = '456'
        data = project(); data['owners'] = ['测试作者']
        with mock.patch('gallery_dl.extractor.behance.BehanceGalleryExtractor.get_gallery_data', return_value=data) as fetch:
            self.assertEqual(worker.inspect()['items'][0]['id'], '123')
        fetch.assert_called_once()

    def test_browser_collection_size_order_does_not_break_gallerydl(self):
        data=project();component=data['modules'][1]['components'][0]
        size=component['imageSizes']['fs']
        component['imageSizes']={'allAvailable':[size], 'fs':size}
        result=service(data).inspect()
        self.assertTrue(result['items'][0]['images'][1]['url'].endswith('/source/animation.gif'))
        self.assertIn('allAvailable',component['imageSizes'])
        self.assertEqual(trusted_url('https://www-ccv.adobe.io/v1/player/ccv/id/embed'),'https://www-ccv.adobe.io/v1/player/ccv/id/embed')

    def test_gif_preview_uses_available_still_but_download_keeps_animation_source(self):
        data=project();component=data['modules'][1]['components'][0]
        component['imageSizes']['size_disp_still']={'url':'https://mir-s3-cdn-cf.behance.net/project_modules/disp_still/animation.gif'}
        image=service(data).inspect()['items'][0]['images'][1]
        self.assertIn('/source/',image['url']);self.assertIn('/disp_still/',image['preview_url'])
        self.assertNotIn('_still',str(image['fallback_urls']))

    def test_private_work_has_actionable_login_or_permission_errors(self):
        for access, message in [('logged-out','需要登录'), ('restricted-safe','无访问权限')]:
            data = project();data.update(modules=[], matureAccess=access)
            with self.assertRaisesRegex(ValueError, message): service(data).inspect()

    def test_image_urls_cannot_access_credentials_local_hosts_or_spoofed_domains(self):
        for url in ('http://127.0.0.1/private', 'https://behance.net.evil.invalid/a', 'https://user:secret@mir-s3-cdn-cf.behance.net/a'):
            with self.assertRaises(ValueError): trusted_url(url)
        data=project();data['modules'][0]['imageSizes']['allAvailable'][0]['url']='https://127.0.0.1/source/photo.jpg'
        with self.assertRaises(ValueError): service(data).inspect()

    def test_gallery_requests_guard_each_send_including_redirect_targets(self):
        import requests
        with mock.patch('requests.Session.send') as send:
            with BehanceProject(service(),URL) as parser:
                for url in ('http://127.0.0.1/a', 'https://adobe.com.evil.invalid/a'):
                    with self.assertRaises(ValueError): parser.extractor.session.send(requests.Request('GET', url).prepare())
            send.assert_not_called()

    def test_only_known_embed_players_are_navigable(self):
        self.assertEqual(embed_url('https://player.vimeo.com/video/123?h=abc&autoplay=1'),'https://player.vimeo.com/video/123?h=abc')
        self.assertEqual(embed_url('https://www.youtube-nocookie.com/embed/SmQd5BgLmd0'),'https://www.youtube.com/watch?v=SmQd5BgLmd0')
        for url in ('javascript:alert(1)', 'https://www.youtube.com.evil.invalid/embed/SmQd5BgLmd0', 'https://127.0.0.1/a'):
            self.assertIsNone(embed_url(url))

    def test_native_video_metadata_omits_signed_streams_and_uses_stable_module_identity(self):
        data=project();data['modules']=[native_video()]
        info={'id':'123_14-0','title':'作品视频','duration':58,'formats':[{'format_id':'http-360','height':360,'url':'https://cdn-prod-ccv.adobe.com/a?signature=secret','vcodec':'h264','acodec':'aac'}]}
        with mock.patch('behance_adapter.resolve_video',return_value=info): result=service(data).inspect()
        item=result['items'][0]
        self.assertEqual(item['mediaId'],'14-0');self.assertEqual(item['duration'],58)
        self.assertNotIn('signature',json.dumps(result));self.assertEqual(item['url'],URL)

    def test_video_failure_keeps_images_usable_and_never_offers_false_best_quality(self):
        data=project();data['modules'].append(native_video())
        with mock.patch('behance_adapter.resolve_video',side_effect=RuntimeError('HTTP Error 403')):
            result=service(data).inspect()
        self.assertEqual(result['items'][0]['kind'],'images')
        self.assertEqual(result['items'][-1]['kind'],'unavailable')
        self.assertNotIn('formats',result['items'][-1])

    def test_selected_gif_is_verified_preserved_and_progress_finishes_at_100(self):
        from PIL import Image
        buffer=io.BytesIO();Image.new('RGB',(8,6),'red').save(buffer,format='GIF',save_all=True,append_images=[Image.new('RGB',(8,6),'blue')],duration=80,loop=0)
        content=buffer.getvalue();events=[]
        with tempfile.TemporaryDirectory() as folder, mock.patch('yt_dlp.YoutubeDL') as factory:
            factory.return_value.__enter__.return_value.urlopen.side_effect=lambda _: _Response(content,len(content),'image/gif')
            worker=service(kind='images',indices=[2],settings={'saveDir':folder});worker.emit=lambda kind,data:events.append((kind,data))
            result=worker.download(); output=Path(result['folder'])/'02.gif'
            self.assertEqual(result['count'],1);self.assertEqual(output.read_bytes(),content)
            with Image.open(output) as image:self.assertEqual(image.n_frames,2)
            self.assertEqual([v['percent'] for k,v in events if k=='progress'][-1],100)
            self.assertFalse(list(Path(result['folder']).glob('*.part')))
            self.assertEqual(json.loads((Path(result['folder'])/'project.json').read_text(encoding='utf8'))['project_id'],'123')

    def test_corrupt_image_never_becomes_a_saved_image(self):
        with tempfile.TemporaryDirectory() as folder,mock.patch('yt_dlp.YoutubeDL') as factory:
            factory.return_value.__enter__.return_value.urlopen.side_effect=lambda _: _Response(b'not-an-image',12,'image/jpeg')
            with self.assertRaises(Exception):service(kind='images',indices=[1],settings={'saveDir':folder}).download()
            self.assertFalse([p for p in Path(folder).rglob('*') if p.is_file()])

    def test_cancelled_parse_does_not_make_network_requests(self):
        worker=service();worker.cancel.set()
        with mock.patch('requests.Session.send',side_effect=AssertionError('network not allowed')):
            with self.assertRaises(Cancelled):worker.inspect()

    def test_download_uses_frozen_module_not_a_renderer_stream_url(self):
        data=project();data['modules']=[native_video()]
        with tempfile.TemporaryDirectory() as folder,mock.patch('behance_adapter.resolve_video') as resolve:
            worker=service(data,kind='video',mediaId='14-0',settings={'saveDir':folder})
            self.assertEqual(worker.download()['count'],1);self.assertTrue(resolve.call_args.kwargs['download'])
            worker.request['mediaId']='arbitrary-url'
            with self.assertRaisesRegex(ValueError,'已变化'):worker.download()

    def test_youtube_cannot_be_downloaded_through_the_behance_video_adapter(self):
        with self.assertRaisesRegex(ValueError,'YouTube 分组'):
            resolve_video(service(),URL,'https://www.youtube.com/watch?v=SmQd5BgLmd0','id','title',download=True)

    def test_adobe_signed_manifest_urls_are_not_exposed_in_progress_logs(self):
        events=[];worker=service();worker.emit=lambda k,v:events.append((k,v))
        worker.log('Extracting https://cdn-prod-ccv.adobe.com/id/master.m3u8?hdnts=private-adobe-token')
        self.assertNotIn('private-adobe-token',str(events))

    def test_original_dimensions_are_not_inferred_from_a_rounded_small_preview(self):
        data=project();module=data['modules'][0]
        module.update(width=600,height=503)
        module['imageSizes']['allAvailable'][0].update(width=1920)
        module['imageSizes']['allAvailable'][0].pop('height')
        image=service(data).inspect()['items'][0]['images'][0]
        self.assertEqual(image['width'],1920);self.assertIsNone(image['height'])

    def test_adobe_player_poster_and_duration_are_reused_without_an_extra_request(self):
        import requests
        response=requests.Response();response.status_code=200
        response._content=b'{"posterframe":"https://cdn-prod-ccv.adobe.com/id/poster.jpg","duration":24.9}'
        with mock.patch('requests.Session.send',return_value=response) as send:
            with BehanceProject(service(),URL) as parser:
                parser.extractor.session.send(requests.Request('GET','https://www-ccv.adobe.io/v1/player/ccv/id/embed').prepare())
                self.assertEqual(parser.player_metadata['id']['duration'],24.9)
                self.assertTrue(parser.player_metadata['id']['thumbnail'].endswith('poster.jpg'))
        send.assert_called_once()

if __name__=='__main__':unittest.main()
