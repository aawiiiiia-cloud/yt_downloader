import copy,json,time,unittest
from unittest import mock
from external_media import external_url,validate_external,browser_vimeo
from collector_core import Collector,Cancelled
from yt_dlp import YoutubeDL

SOURCE='https://www.behance.net/gallery/123/Test'
TARGET='https://player.vimeo.com/video/456?h=abcdef'
def context():
    return {'projectId':'123','project':{'id':123,'modules':[{'id':8,'__typename':'EmbedModule','originalEmbed':f'<iframe src="{TARGET}">'}]},
        'externalSource':{'key':'8-0'},'vimeo':{'videoId':'456','capturedAt':time.time()*1000,
        'config':{'video':{'id':456,'title':'fixture','duration':6},'request':{'files':{'progressive':[{'url':'https://vod.vimeocdn.com/sample.mp4','quality':'240p','width':426,'height':240}]}}}}}
class ExternalMediaTests(unittest.TestCase):
    def test_external_binding_rejects_wrong_project_module_target_and_missing_consent(self):
        self.assertEqual(validate_external(TARGET,SOURCE,context())['id'],'456')
        for change in ({'projectId':'999'},{'externalSource':{'key':'wrong'}},{'externalSource':None}):
            data=context();data.update(change)
            with self.assertRaisesRegex(ValueError,'默认不下载'):validate_external(TARGET,SOURCE,data)
        with self.assertRaisesRegex(ValueError,'默认不下载'):validate_external(TARGET.replace('456','789'),SOURCE,context())
        with self.assertRaisesRegex(ValueError,'默认不下载'):validate_external(TARGET.replace('abcdef','fedcba'),SOURCE,context())
        for url in ('http://player.vimeo.com/video/456','https://vimeo.com.evil.invalid/456','https://user:secret@player.vimeo.com/video/456'):self.assertIsNone(external_url(url))

    def test_media_config_rejects_wrong_identity_expiry_and_untrusted_network_targets_before_requests(self):
        mutations=[lambda d:d.update(videoId='999'),lambda d:d.update(capturedAt=0),
            lambda d:d['config']['request']['files']['progressive'][0].update(url='http://127.0.0.1/private'),
            lambda d:d['config']['request']['files'].update(hls={'cdns':{'evil':{'url':'https://vimeo.com.evil.invalid/a.m3u8'}}})]
        for mutate in mutations:
            data=context();mutate(data['vimeo'])
            with mock.patch('vimeo_extractor.ParallelVimeoIE._parse_config') as parse:
                with self.assertRaises(ValueError):browser_vimeo(TARGET,data,mock.Mock())
                parse.assert_not_called()

    def test_real_vimeo_parser_normalises_browser_formats_without_player_requests_or_signed_urls_in_result(self):
        data=context();before=copy.deepcopy(data)
        with mock.patch.object(YoutubeDL,'urlopen',side_effect=AssertionError('unexpected network')):
            result=Collector({'url':TARGET,'referrer':SOURCE,'context':data},lambda *_:None).inspect()
        item=result['items'][0]
        self.assertEqual(item['formats'][0]['height'],240);self.assertEqual(item['duration'],6)
        self.assertNotIn('sample.mp4',json.dumps(result));self.assertEqual(data,before)

    def test_direct_legacy_external_download_is_rejected_before_any_network(self):
        for url in (TARGET,'https://www.youtube.com/watch?v=SmQd5BgLmd0'):
            worker=Collector({'url':url,'referrer':SOURCE,'settings':{}},lambda *_:None)
            with mock.patch.object(YoutubeDL,'urlopen',side_effect=AssertionError('unexpected network')):
                with self.assertRaisesRegex(ValueError,'默认不下载'):worker.download()

    def test_cancelled_browser_parse_never_fetches_manifests(self):
        with mock.patch('vimeo_extractor.ParallelVimeoIE._parse_config') as parse:
            with self.assertRaises(Cancelled):browser_vimeo(TARGET,context(),mock.Mock(),lambda:(_ for _ in ()).throw(Cancelled()))
            parse.assert_not_called()

    def test_explicit_embed_selection_cannot_bypass_youtube_login_verification(self):
        target='https://www.youtube.com/watch?v=SmQd5BgLmd0'
        data=context();data['project']['modules'][0]['originalEmbed']='<iframe src="https://www.youtube.com/embed/SmQd5BgLmd0">'
        self.assertEqual(validate_external(target,SOURCE,data)['provider'],'youtube')
        worker=Collector({'url':target,'referrer':SOURCE,'context':data,'youtubeVerified':False,'settings':{}},lambda *_:None)
        with mock.patch.object(YoutubeDL,'urlopen',side_effect=AssertionError('unexpected network')):
            with self.assertRaisesRegex(ValueError,'通过 Edge 登录并验证'):worker.download()

if __name__=='__main__':unittest.main()
