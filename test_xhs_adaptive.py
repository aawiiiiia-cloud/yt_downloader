from __future__ import annotations
import io,tempfile,unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock
from PIL import Image
from test_xhs_image_downloader import _Response
from xhs_image_downloader import XhsImageDownloader,XhsImage,XhsPost,XhsDownloadError,XhsCancelled

ORIGINS=['https://sns-na-i11.xhscdn.com','https://sns-na-i10.xhscdn.com','https://sns-na-i9.xhscdn.com']
PREVIEW='https://sns-webpic-qc.xhscdn.com/secret!thumbnail?token=private'

class Clock:
    now=0.0
    def tick(self,seconds):self.now+=seconds

class TimedResponse(_Response):
    def __init__(self,data,clock,rate,length=True):
        super().__init__(data,len(data) if length else None,'image/bmp')
        self.clock,self.rate=clock,rate
    def read(self,size=-1):
        data=super().read(size)
        if data:self.clock.tick(len(data)/self.rate)
        return data

class FakeYdl:
    def __init__(self,factory):self.factory=factory;self.requests=[]
    def urlopen(self,request):self.requests.append(request);return self.factory(request)

class AdaptiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);output=io.BytesIO()
        Image.new('RGB',(512,512),(42,104,208)).save(output,format='BMP');self.data=output.getvalue()
        self.clock=Clock();self.clock.now=0
        self.logs,self.events=[],[]
        self.writer=XhsImageDownloader(progress=self.logs.append,transfer_progress=self.events.append)
        self.post=XhsPost('fixture','originals','','','https://www.xiaohongshu.com/explore/fixture?xsec_token=secret',
                         (XhsImage(10,ORIGINS[0]+'/resource',(ORIGINS[1]+'/resource',ORIGINS[2]+'/resource',PREVIEW)),))
    def response(self,rate=2*1024*1024,length=True):return TimedResponse(self.data,self.clock,rate,length)
    def download(self,factory,post=None):
        post=post or self.post;ydl=FakeYdl(factory)
        with mock.patch('xhs_image_downloader.time.monotonic',side_effect=lambda:self.clock.now):
            target=self.writer._download_one(ydl,post,post.images[0],self.folder)
        return target,ydl
    def test_ten_ish_kib_stream_switches_to_other_original_and_resets_progress(self):
        target,ydl=self.download(lambda r:self.response(16*1024 if r.url.startswith(ORIGINS[0]) else 2*1024*1024))
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual([r.url for r in ydl.requests],[ORIGINS[0]+'/resource',ORIGINS[1]+'/resource'])
        self.assertTrue(any('[低速]' in l for l in self.logs))
        connections=[e for e in self.events if e['status']=='image-connecting']
        self.assertEqual(len(connections),2);self.assertTrue(all(e['downloadedBytes']==0 and e['speed'] is None for e in connections))
        self.assertFalse((self.folder/'.10.part').exists())
        self.assertNotIn('secret','\n'.join(self.logs));self.assertNotIn('private','\n'.join(self.logs))
    def test_timeout_switches_immediately_instead_of_retrying_same_url(self):
        def factory(r):
            if r.url.startswith(ORIGINS[0]):raise TimeoutError('read timed out')
            return self.response()
        target,ydl=self.download(factory)
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual(len(ydl.requests),2)
        self.assertTrue(all(r.extensions['timeout']==15 for r in ydl.requests))
    def test_all_originals_slow_return_to_best_original_without_preview_or_loop(self):
        rates=dict(zip(ORIGINS,[16*1024,24*1024,12*1024]))
        def factory(r):
            self.assertFalse(r.url.startswith('https://sns-webpic'))
            return self.response(rates[next(host for host in ORIGINS if r.url.startswith(host))])
        target,ydl=self.download(factory)
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual([r.url for r in ydl.requests],[host+'/resource' for host in [ORIGINS[0],ORIGINS[1],ORIGINS[2],ORIGINS[1]]])
        self.assertEqual(sum('[低速]' in l for l in self.logs),3)
        self.assertTrue(any('[继续]' in l for l in self.logs))
    def test_almost_finished_slow_original_is_kept(self):
        b=io.BytesIO();Image.new('RGB',(256,256)).save(b,format='BMP');self.data=b.getvalue()
        target,ydl=self.download(lambda r:self.response(16*1024))
        self.assertEqual(target.read_bytes(),self.data);self.assertEqual(len(ydl.requests),1)
    def test_unknown_length_original_can_switch(self):
        target,ydl=self.download(lambda r:self.response(16*1024 if r.url.startswith(ORIGINS[0]) else 2*1024*1024,False))
        self.assertEqual(target.read_bytes(),self.data);self.assertEqual(len(ydl.requests),2)
        self.assertTrue(all(e['percent'] is None for e in self.events if e['status']=='image-receiving'))
    def test_fast_image_has_no_extra_request(self):
        target,ydl=self.download(lambda r:self.response())
        self.assertEqual(len(ydl.requests),1);self.assertEqual(target.read_bytes(),self.data)
    def test_large_38_to_80_kib_streams_switch_without_changing_original_bytes(self):
        out=io.BytesIO();Image.new('RGB',(1024,1024),(42,104,208)).save(out,format='BMP')
        self.data=out.getvalue()
        for rate in (38,60,80):
            with self.subTest(rate=rate):
                self.clock.now=0;self.writer._origin_health.clear()
                target,ydl=self.download(lambda r:self.response(rate*1024 if r.url.startswith(ORIGINS[0]) else 2*1024*1024))
                self.assertEqual(target.read_bytes(),self.data)
                self.assertEqual([r.url for r in ydl.requests],[ORIGINS[0]+'/resource',ORIGINS[1]+'/resource'])
                self.assertLess(self.clock.now,15)
                self.assertNotIn(PREVIEW,[r.url for r in ydl.requests])
    def test_moderate_stream_that_is_almost_done_does_not_restart(self):
        target,ydl=self.download(lambda r:self.response(48*1024))
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual(len(ydl.requests),1)
    def test_96_kib_stream_does_not_switch_at_the_threshold(self):
        out=io.BytesIO();Image.new('RGB',(1024,1024)).save(out,format='BMP');self.data=out.getvalue()
        target,ydl=self.download(lambda r:self.response(96*1024))
        self.assertEqual(target.read_bytes(),self.data);self.assertEqual(len(ydl.requests),1)
    def test_all_moderate_sources_are_bounded_and_restore_the_fastest_original(self):
        out=io.BytesIO();Image.new('RGB',(1024,1024)).save(out,format='BMP');self.data=out.getvalue()
        rates=dict(zip(ORIGINS,[38*1024,60*1024,48*1024]))
        target,ydl=self.download(lambda r:self.response(rates[next(host for host in ORIGINS if r.url.startswith(host))]))
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual([r.url for r in ydl.requests],[host+'/resource' for host in [ORIGINS[0],ORIGINS[1],ORIGINS[2],ORIGINS[1]]])
    def test_small_slow_image_does_not_switch(self):
        b=io.BytesIO();Image.new('RGB',(32,24)).save(b,format='BMP');self.data=b.getvalue()
        target,ydl=self.download(lambda r:self.response(2*1024))
        self.assertEqual(len(ydl.requests),1);self.assertEqual(target.read_bytes(),self.data)
    def test_working_slow_original_is_restored_before_any_display_fallback(self):
        def factory(r):
            if r.url.startswith(ORIGINS[0]):return self.response(16*1024)
            raise OSError('HTTP 403')
        target,ydl=self.download(factory)
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual(len(ydl.requests),4)
        self.assertEqual(ydl.requests[-1].url,ORIGINS[0]+'/resource')
    def test_fallback_is_used_only_when_originals_really_fail(self):
        def factory(r):
            if r.url.startswith('https://sns-webpic'):return self.response()
            raise OSError('HTTP 403 https://example.com/path?token=private')
        target,ydl=self.download(factory)
        self.assertEqual(target.read_bytes(),self.data);self.assertEqual(len(ydl.requests),7)
        self.assertNotIn('private','\n'.join(self.logs))
    def test_failed_and_corrupt_sources_do_not_overwrite_existing_image(self):
        old=self.folder/'10.bmp';old.write_bytes(b'old-image')
        with self.assertRaises(XhsDownloadError):self.download(lambda r:_Response(b'corrupt',7,'image/bmp'))
        self.assertEqual(old.read_bytes(),b'old-image');self.assertFalse((self.folder/'.10.part').exists())
    def test_cancel_from_progress_callback_wins_over_source_switch(self):
        import threading
        self.writer.cancel_event=threading.Event()
        def report(e):
            if e['downloadedBytes']:self.writer.cancel_event.set()
        self.writer.transfer_progress=report
        with self.assertRaises(XhsCancelled):self.download(lambda r:self.response(16*1024))
        self.assertFalse((self.folder/'10.bmp').exists());self.assertFalse((self.folder/'.10.part').exists())
    def test_original_group_excludes_other_resource_query_and_display_transform(self):
        image=replace(self.post.images[0],fallback_urls=(ORIGINS[1]+'/resource',ORIGINS[2]+'/other',ORIGINS[2]+'/resource!small',ORIGINS[2]+'/resource?token=x',PREVIEW))
        self.assertEqual(self.writer._original_candidate_indices(image),(0,1))
    def test_successful_original_rank_is_reused_for_next_image_in_same_job(self):
        self.download(lambda r:self.response(16*1024 if r.url.startswith(ORIGINS[0]) else 2*1024*1024))
        image=XhsImage(11,ORIGINS[0]+'/next',(ORIGINS[1]+'/next',ORIGINS[2]+'/next',PREVIEW))
        target,ydl=self.download(lambda r:self.response(),replace(self.post,images=(image,)))
        self.assertEqual(ydl.requests[0].url,ORIGINS[1]+'/next');self.assertEqual(target.name,'11.bmp')
    def test_unrelated_site_keeps_its_original_and_preview_order(self):
        image=XhsImage(10,'https://images.example/original',('https://images.example/preview',))
        target,ydl=self.download(lambda r:self.response(16*1024),replace(self.post,images=(image,)))
        self.assertEqual(len(ydl.requests),1);self.assertEqual(target.read_bytes(),self.data)
    def test_last_original_is_not_mislabelled_as_web_preview(self):
        image=replace(self.post.images[0],fallback_urls=(ORIGINS[1]+'/resource',))
        self.assertEqual(self.writer._source_label(image,1,2),'原始素材 CDN')

class OriginalSelectionTests(unittest.TestCase):
    download = AdaptiveTests.download
    response = AdaptiveTests.response

    def setUp(self):
        AdaptiveTests.setUp(self)
        out=io.BytesIO();Image.new('RGB',(1800,1800),(42,104,208)).save(out,format='BMP')
        self.data=out.getvalue()

    def test_large_moderate_original_reuses_selected_response_without_mixing_bytes(self):
        other=io.BytesIO();Image.new('RGB',(1800,1800),(80,20,10)).save(other,format='BMP')
        alternate=other.getvalue()
        responses=[]
        def factory(request):
            body=self.data if request.url.startswith(ORIGINS[0]) else alternate
            response=TimedResponse(body,self.clock,160*1024 if request.url.startswith(ORIGINS[0]) else 2*1024*1024)
            responses.append(response)
            return response
        target,ydl=self.download(factory)
        self.assertEqual(target.read_bytes(),alternate)
        self.assertEqual([r.url for r in ydl.requests],[ORIGINS[0]+'/resource',ORIGINS[1]+'/resource'])
        self.assertLess(self.clock.now,10)
        self.assertTrue(all(response.closed for response in responses))
        self.assertEqual(len([e for e in self.events if e['status']=='image-connecting']),2)

    def test_large_fast_original_does_not_probe(self):
        target,ydl=self.download(lambda r:self.response(4*1024*1024))
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual(len(ydl.requests),1)

    def test_unhelpful_samples_are_bounded_and_keep_primary_connection(self):
        target,ydl=self.download(lambda r:self.response(160*1024))
        self.assertEqual(target.read_bytes(),self.data)
        self.assertEqual(len(ydl.requests),3)
        self.assertEqual(ydl.requests[0].url,ORIGINS[0]+'/resource')
        self.assertEqual(len([e for e in self.events if e['status']=='image-connecting']),1)

    def test_reachable_exact_original_hint_changes_only_trusted_candidate_order(self):
        self.writer._preferred_urls={ORIGINS[1]+'/resource',PREVIEW,ORIGINS[0]+'/other'}
        target,ydl=self.download(lambda r:self.response())
        self.assertEqual(ydl.requests[0].url,ORIGINS[1]+'/resource')
        self.assertEqual(target.read_bytes(),self.data)

    def test_cancel_during_probe_closes_both_connections_and_keeps_existing_output(self):
        import threading
        self.writer.cancel_event=threading.Event()
        existing=self.folder/'10.bmp';existing.write_bytes(b'old-complete')
        responses=[]
        def factory(request):
            response=self.response(160*1024)
            responses.append(response)
            if len(responses)>1:self.writer.cancel_event.set()
            return response
        with self.assertRaises(XhsCancelled):self.download(factory)
        self.assertTrue(all(response.closed for response in responses))
        self.assertEqual(existing.read_bytes(),b'old-complete')
        self.assertFalse((self.folder/'.10.part').exists())


if __name__=='__main__':unittest.main()
