from __future__ import annotations
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock
from collector_core import Collector, Cancelled
from test_xhs_progress import _Response, _Ydl
from test_taobao_adapter import URL as TAOBAO_URL, context as taobao_context, response, image_bytes
from test_zcool_adapter import URL as ZCOOL_URL, context as zcool_context
from test_behance_adapter import URL as BEHANCE_URL, project
from xhs_image_downloader import XhsImage, XhsPost, XhsImageDownloader

class DownloadUXTests(unittest.TestCase):
    def test_byte_rate_measures_each_attempt_and_clears_before_validation(self):
        events=[];data=image_bytes()
        post=XhsPost('fixture','speed','','','https://www.xiaohongshu.com/explore/fixture',(XhsImage(7,'https://img.example/image'),))
        writer=XhsImageDownloader(progress=lambda _:None,transfer_progress=events.append)
        with tempfile.TemporaryDirectory() as folder, mock.patch('xhs_image_downloader.time.monotonic',side_effect=[100,100.5,101]):
            writer._download_one(_Ydl(lambda:_Response(data,len(data))),post,post.images[0],Path(folder))
        receiving=[e for e in events if e['status']=='image-receiving' and e['downloadedBytes']]
        self.assertEqual(receiving[0]['speed'],len(data)/.5)
        self.assertEqual(receiving[-1]['speed'],len(data))
        self.assertTrue(all(e['speed'] is None for e in events if e['status']!='image-receiving'))
    def test_unknown_content_length_still_reports_bytes_and_speed(self):
        events=[];data=image_bytes()
        post=XhsPost('fixture','speed','','','https://www.xiaohongshu.com/explore/fixture',(XhsImage(1,'https://img.example/image'),))
        writer=XhsImageDownloader(progress=lambda _:None,transfer_progress=events.append)
        with tempfile.TemporaryDirectory() as folder, mock.patch('xhs_image_downloader.time.monotonic',side_effect=[0,1,2]):
            writer._download_one(_Ydl(lambda:_Response(data)),post,post.images[0],Path(folder))
        receiving=[e for e in events if e['status']=='image-receiving']
        self.assertTrue(all(e['percent'] is None for e in receiving));self.assertGreater(receiving[-1]['speed'],0)
        self.assertEqual(receiving[-1]['downloadedBytes'],len(data))
    def test_taobao_zcool_behance_transfer_progress_is_wired_before_atomic_publication(self):
        for url,context in [(TAOBAO_URL,taobao_context()),(ZCOOL_URL,zcool_context()),(BEHANCE_URL,{'projectId':'123','project':project()})]:
            with self.subTest(url=url),tempfile.TemporaryDirectory() as folder:
                content=image_bytes();events=[];before=[]
                worker=Collector({'url':url,'context':context,'settings':{'saveDir':folder},'kind':'images','indices':[1]},lambda k,v:events.append((k,v)))
                item=worker.inspect()['items'][0];worker.request['mediaId']=item['mediaId'] if 'mediaId' in item else item['id']
                def emit(kind,event):
                    events.append((kind,event))
                    if kind=='progress' and event['status']=='image-receiving' and event['downloadedBytes']:
                        before.append((event['speed'],len(list(Path(folder).rglob('01.*')))))
                worker.emit=emit
                with mock.patch('requests.Session.send',side_effect=lambda *_a,**_kw:response(content)), \
                     mock.patch('yt_dlp.YoutubeDL') as ydl:
                    ydl.return_value.__enter__.return_value.urlopen.side_effect=lambda _:_Response(content,len(content),'image/png')
                    result=worker.download()
                self.assertEqual(result['count'],1);self.assertTrue(before)
                self.assertTrue(all(rate is None or rate>0 for rate,_ in before));self.assertTrue(all(count==0 for _,count in before))
                progress=[v for k,v in events if k=='progress'];self.assertEqual(progress[-1]['percent'],100)
                self.assertTrue(any(v['status']=='image-connecting' for v in progress))
                self.assertTrue(any(v['speed']>0 for v in progress if v.get('speed') is not None))
                self.assertTrue(any('[连接]' in v for k,v in events if k=='log'))
    def test_all_video_site_options_use_common_byte_and_speed_hooks(self):
        urls=['https://www.youtube.com/watch?v=fixture','https://www.bilibili.com/video/BV1fixture',
            'https://www.xiaohongshu.com/explore/fixture','https://vimeo.com/12345','https://www.douyin.com/video/12345',
            'https://www.behance.net/gallery/123/Fixture','https://www.zcool.com.cn/work/ZNzQyNjY2ODg=.html']
        for url in urls:
            with self.subTest(url=url):
                events=[];worker=Collector({'url':url,'settings':{},'providerUrl':''},lambda k,v:events.append((k,v)))
                options=worker.options(url)
                self.assertEqual(options['progress_hooks'],[worker.progress]);self.assertEqual(options['postprocessor_hooks'],[worker.postprocess])
                options['progress_hooks'][0]({'status':'downloading','downloaded_bytes':2048,'speed':1024,'eta':15})
                event=events[-1][1];self.assertIsNone(event['percent']);self.assertEqual(event['downloadedBytes'],2048)
                self.assertEqual(event['speed'],1024);self.assertEqual(event['eta'],15)
                worker.progress({'status':'downloading','downloaded_bytes':512,'total_bytes':1024,'speed':0})
                self.assertEqual(events[-1][1]['percent'],50);self.assertEqual(events[-1][1]['speed'],0)
                worker.progress({'status':'finished','downloaded_bytes':1024,'total_bytes':1024,'speed':1024})
                self.assertIsNone(events[-1][1]['speed'])
                worker.postprocess({});self.assertIsNone(events[-1][1]['speed']);self.assertEqual(events[-1][1]['status'],'processing')
                worker.close()
    def test_video_wait_publishes_stage_and_log_before_extraction(self):
        with tempfile.TemporaryDirectory() as folder:
            events=[];worker=Collector({'url':'https://www.xiaohongshu.com/explore/1234567890abcdef12345678','kind':'video','settings':{'saveDir':folder}},lambda k,v:events.append((k,v)))
            class Ydl:
                def __enter__(self):return self
                def __exit__(self,*_):pass
                def download(self,urls):
                    self_outer.assertTrue(any(k=='progress' and v['status']=='extracting' for k,v in events))
                    self_outer.assertTrue(any(k=='log' and '下载地址' in v for k,v in events));return 0
            self_outer=self
            with mock.patch('yt_dlp.YoutubeDL',return_value=Ydl()):worker.download()
    def test_common_speed_hook_remains_cancellable(self):
        event=threading.Event();worker=Collector({},lambda *_:None,event);event.set()
        with self.assertRaises(Cancelled):worker.progress({'status':'downloading','speed':1024})

if __name__=='__main__':unittest.main()
