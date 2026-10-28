import io
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

import requests
from collector_core import Collector, Cancelled, prepare_runtime
from zcool_video import VideoSession, ProtectedVideo, media_url

URL = 'https://www.zcool.com.cn/work/ZNzQyNDUwOTY=.html'
TARGET = 'https://video.zcool.cn/alivod/source.mp4?k=private-video&t=expiry'
FIXTURE = Path(__file__).parent / 'electron/fixtures/zcool-native.mp4'


def context():
    return {'workId': 'ZNzQyNDUwOTY=', 'title': '测试站酷视频', 'author': '作者', 'images': [],
        'videos': [{'id': 123, 'url': TARGET, 'width': 720, 'height': 405,
            'cover': 'https://video.zcool.cn/alivod/source.mp4!vframeOffset0?k=cover'}]}


def service(data=None, **kwargs):
    return Collector({'url': URL, 'context': context() if data is None else data, **kwargs}, lambda *_: None)


def response(data=b'', status=200, headers=None):
    value=requests.Response();value.status_code=status;value.raw=io.BytesIO(data)
    value.headers.update({'Content-Type':'video/mp4','Content-Length':str(len(data)), **(headers or {})})
    return value


def serve(_url, **kwargs):
    data=FIXTURE.read_bytes();value=(kwargs.get('headers') or {}).get('Range')
    if value:
        match=re.fullmatch(r'bytes=(\d+)-(\d*)',value);start=int(match[1]);end=min(int(match[2]) if match[2] else len(data)-1,len(data)-1)
        return response(data[start:end+1],206,{'Content-Range':f'bytes {start}-{end}/{len(data)}'})
    return response(data)


class ZcoolVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): prepare_runtime()

    def test_only_native_mp4_hosts_and_full_signed_query_are_accepted(self):
        self.assertEqual(media_url(TARGET),TARGET)
        for value in ['file:///C:/private.mp4','https://127.0.0.1/a.mp4','https://video.zcool.cn.evil.invalid/a.mp4',
                'https://user:pass@video.zcool.cn/a.mp4','https://video.zcool.cn:8443/a.mp4','https://video.zcool.cn/a.m3u8']:
            with self.assertRaises(ValueError): media_url(value)

    def test_video_only_work_has_actual_metadata_and_no_stream_urls_in_formats(self):
        with mock.patch('requests.Session.get',side_effect=serve) as get:
            item=service().inspect()['items'][0]
        self.assertEqual(item['kind'],'video');self.assertEqual(item['mediaId'],'video-123')
        self.assertEqual((item['formats'][0]['width'],item['formats'][0]['height']),(64,48))
        self.assertAlmostEqual(item['duration'],1.2,places=1)
        self.assertEqual(item['formats'][0]['size'],FIXTURE.stat().st_size)
        self.assertTrue(item['formats'][0]['audio'])
        self.assertNotIn('private-video',json.dumps(item))
        self.assertTrue(all(call.kwargs['headers']['Referer']=='https://www.zcool.com.cn/' for call in get.call_args_list))

    def test_mixed_work_keeps_images_and_each_video_separate(self):
        data=context();data['images']=[{'url':'https://img.zcool.cn/community/a.jpg'}]
        data['videos'].append({**data['videos'][0],'id':124})
        with mock.patch('requests.Session.get',side_effect=serve): result=service(data).inspect()
        self.assertEqual([row['kind'] for row in result['items']],['images','video','video'])
        self.assertEqual([row['mediaId'] for row in result['items'][1:]],['video-123','video-124'])

    def test_metadata_after_a_large_mdat_is_read_by_range_without_fetching_the_movie(self):
        data=FIXTURE.read_bytes();boxes={};offset=0
        while offset+8<=len(data):
            length=int.from_bytes(data[offset:offset+4],'big');boxes[data[offset+4:offset+8]]=data[offset:offset+length];offset+=length
        remote=boxes[b'ftyp']+(400000).to_bytes(4,'big')+b'mdat'+bytes(399992)+boxes[b'moov']
        seen=[]
        def fetch(_url,**kwargs):
            start,end=map(int,re.fullmatch(r'bytes=(\d+)-(\d+)',kwargs['headers']['Range']).groups())
            seen.append((start,end));end=min(end,len(remote)-1)
            return response(remote[start:end+1],206,{'Content-Range':f'bytes {start}-{end}/{len(remote)}'})
        with mock.patch('requests.Session.get',side_effect=fetch): item=service().inspect()['items'][0]
        self.assertAlmostEqual(item['duration'],1.2,places=1)
        self.assertTrue(any(start>=400000 for start,_end in seen))
        self.assertLess(sum(end-start+1 for start,end in seen),300000)

    def test_invalid_cover_does_not_discard_a_valid_native_video(self):
        data=context();data['videos'][0]['cover']='https://video.zcool.cn:invalid/frame'
        with mock.patch('requests.Session.get',side_effect=serve): item=service(data).inspect()['items'][0]
        self.assertEqual(item['kind'],'video');self.assertIsNone(item['thumbnail'])

    def test_wrong_work_and_unknown_video_selection_never_issue_requests(self):
        with mock.patch('requests.Session.get',side_effect=AssertionError('unexpected request')):
            with self.assertRaises(ValueError): service({**context(),'workId':'Zother'}).inspect()
            with tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError): service(kind='video',mediaId='video-other',settings={'saveDir':folder}).download()

    def test_expired_access_keeps_images_and_reports_an_unavailable_video(self):
        data=context();data['images']=[{'url':'https://img.zcool.cn/community/a.jpg'}]
        with mock.patch('requests.Session.get',return_value=response(status=403)):
            result=service(data).inspect()
        self.assertEqual([row['kind'] for row in result['items']],['images','unavailable'])

    def test_temporary_metadata_failure_still_allows_a_video_without_inventing_dimensions(self):
        with mock.patch('requests.Session.get',side_effect=requests.Timeout): item=service().inspect()['items'][0]
        self.assertEqual(item['kind'],'video');self.assertIsNone(item['duration'])
        self.assertIsNone(item['formats'][0]['height']);self.assertIsNone(item['formats'][0]['size'])

    def test_redirect_to_local_or_foreign_hosts_is_rejected_before_second_request(self):
        for location in ('http://127.0.0.1/private.mp4','https://unrelated.invalid/a.mp4'):
            with mock.patch('requests.Session.get',return_value=response(status=302,headers={'Location':location})) as get:
                with VideoSession(service()) as session:
                    with self.assertRaises(ValueError): session.get(TARGET)
                self.assertEqual(get.call_count,1)

    def test_metadata_reads_are_bounded_even_when_range_is_ignored(self):
        class Stream(io.BytesIO):
            count=0
            def read(self,length=-1):
                self.count+=max(0,length)
                return super().read(length)
        data=b'\x00\x00\x00\x20ftyp'+bytes(2*1024*1024)
        value=response();value.raw=Stream(data)
        with mock.patch('requests.Session.get',return_value=value):
            with VideoSession(service()) as session: session.probe(TARGET)
        self.assertLessEqual(value.raw.count,256*1024)

    def test_real_yt_dlp_download_saves_the_exact_muxed_fixture(self):
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=serve):
            result=service(kind='video',mediaId='video-123',formatId='mp4',settings={'saveDir':folder}).download()
            files=list(Path(folder).glob('*.mp4'));self.assertEqual(len(files),1)
            self.assertEqual(files[0].read_bytes(),FIXTURE.read_bytes());self.assertEqual(result['count'],1)

    def test_audio_only_uses_existing_ffmpeg_postprocessor(self):
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=serve):
            service(kind='video',mediaId='video-123',settings={'saveDir':folder,'audioOnly':True}).download()
            self.assertEqual(len(list(Path(folder).glob('*.mp3'))),1)

    def test_html_responses_are_not_saved_as_completed_video(self):
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',return_value=response(b'<html>verify</html>',headers={'Content-Type':'text/html'})):
            with self.assertRaises(Exception): service(kind='video',mediaId='video-123',settings={'saveDir':folder}).download()
            self.assertFalse(list(Path(folder).glob('*.mp4')))

    def test_encrypted_metadata_is_not_downgraded_to_an_available_video(self):
        with mock.patch('requests.Session.get',side_effect=serve),mock.patch('zcool_video.probe_boxes',side_effect=ProtectedVideo('protected')):
            self.assertEqual(service().inspect()['items'][0]['kind'],'unavailable')

    def test_cancellation_is_not_a_video_failure_or_success(self):
        worker=service();worker.cancel.set()
        with self.assertRaises(Cancelled): worker.inspect()
        with VideoSession(worker) as session:
            with self.assertRaises(Cancelled): session.get(TARGET)

    def test_cancelling_a_running_download_never_produces_a_completed_mp4(self):
        with tempfile.TemporaryDirectory() as folder,mock.patch('requests.Session.get',side_effect=serve):
            worker=service(kind='video',mediaId='video-123',settings={'saveDir':folder})
            worker.emit=lambda kind,data:worker.cancel.set() if kind=='progress' and data.get('status')=='downloading' else None
            with self.assertRaises(Exception): worker.download()
            self.assertTrue(worker.cancel.is_set());self.assertFalse(list(Path(folder).glob('*.mp4')))


if __name__=='__main__': unittest.main()
