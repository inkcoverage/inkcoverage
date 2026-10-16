"""Bounded streaming, admission, proxy identity and raster/job policy tests."""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app as backend
import workload as limits
from PIL import Image
from starlette.formparsers import MultiPartParser


async def call(path,body=b'',headers=(),method='POST',receive=None,client='127.0.0.1'):
    sent=[]
    if receive is None:
        async def receive():return {'type':'http.request','body':body,'more_body':False}
    async def send(message):sent.append(message)
    scope=dict(type='http',asgi={'version':'3.0'},http_version='1.1',method=method,scheme='http',path=path,
               raw_path=path.encode(),query_string=b'',root_path='',headers=list(headers),client=(client,12345),server=('127.0.0.1',8001))
    await backend.app(scope,receive,send)
    start=next(m for m in sent if m['type']=='http.response.start')
    return start['status'],json.loads(b''.join(m.get('body',b'') for m in sent if m['type']=='http.response.body')),dict(start['headers'])


def multipart(contents,extra=b''):
    return b'--Boundary\r\nContent-Disposition: form-data; name="file"; filename="test.pdf"\r\nContent-Type: application/pdf\r\n\r\n'+contents+b'\r\n'+extra+b'--Boundary--\r\n'

FORM=[(b'content-type',b'multipart/form-data; boundary=Boundary')]
JSON=[(b'content-type',b'application/json')]


class BoundaryTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]/'tmp';root.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=root);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.addCleanup(patch.stopall)
        patch.object(backend,'TEMP_DIR',self.root).start()
        patch.object(backend,'_inspect_upload',return_value=1).start()
        limits.rates.clients.clear()
        self.doc=self.root/('a'*32);backend.files.create(self.doc);backend.files.ready(self.doc);backend.files.release(self.doc);(self.doc/'upload.pdf').write_bytes(b'%PDF-test')
        self.payload=json.dumps(dict(id='a'*32,page=1,cropLeft=0,cropTop=0,cropWidth=144,cropHeight=144,pdfWidthPt=144,pdfHeightPt=144)).encode()

    def test_declared_oversize_rejected_without_reading(self):
        async def unread():self.fail('Oversize body was read')
        headers=FORM+[(b'content-length',str(limits.MAX_UPLOAD_BYTES+limits.UPLOAD_ENVELOPE_BYTES+1).encode())]
        status,data,_=asyncio.run(call('/api/upload',headers=headers,receive=unread))
        self.assertEqual((status,data['code']),(413,'upload_too_large'))

    def test_chunked_upload_stops_early_and_closes_spool(self):
        import starlette.formparsers as parsers
        original=parsers.SpooledTemporaryFile;opened=[]
        def spool(*args,**kwargs):
            file=original(*args,**kwargs);opened.append(file);return file
        body=multipart(b'%PDF-'+b'x'*3000);chunks=[body[i:i+64] for i in range(0,len(body),64)];reads=0
        async def receive():
            nonlocal reads
            chunk=chunks[reads];reads+=1
            return {'type':'http.request','body':chunk,'more_body':reads<len(chunks)}
        with patch.object(limits,'MAX_UPLOAD_BYTES',400),patch.object(limits,'UPLOAD_ENVELOPE_BYTES',64),patch.object(parsers,'SpooledTemporaryFile',spool),patch.object(MultiPartParser,'spool_max_size',64):
            status,data,_=asyncio.run(call('/api/upload',headers=FORM,receive=receive))
        self.assertEqual((status,data['code']),(413,'upload_too_large'))
        self.assertLess(reads,len(chunks));self.assertTrue(opened);self.assertTrue(all(f.closed for f in opened))
        self.assertEqual(len(list(self.root.iterdir())),1)

    def test_exact_file_limit_and_one_byte_over(self):
        for size,expected in [(128,200),(129,413)]:
            with patch.object(backend,'MAX_UPLOAD_BYTES',128):
                status,data,_=asyncio.run(call('/api/upload',multipart(b'%PDF-'+b' '*(size-5)),FORM))
            self.assertEqual(status,expected,data)
        self.assertEqual(len(list(self.root.iterdir())),2)

    def test_copy_reads_are_bounded_and_stop_at_limit(self):
        class Source:
            content_type='application/pdf';filename='test.pdf'
            def __init__(self):self.reads=[]
            async def read(self,size):self.reads.append(size);return b'%PDF-'+b'x'*1019
        source=Source()
        with patch.object(backend,'MAX_UPLOAD_BYTES',1500):
            with self.assertRaises(limits.WorkloadError):asyncio.run(backend._store_upload(source))
        self.assertEqual(source.reads,[1024*1024,1024*1024]);self.assertEqual(len(list(self.root.iterdir())),1)

    def test_json_stream_limit_preserves_413(self):
        reads=0
        async def receive():
            nonlocal reads
            reads+=1;return {'type':'http.request','body':b'x'*40,'more_body':True}
        with patch.object(limits,'MAX_JSON_BYTES',64):
            status,data,_=asyncio.run(call('/api/analyze',headers=JSON,receive=receive))
        self.assertEqual((status,data['code']),(413,'request_too_large'));self.assertEqual(reads,2)

    def test_extra_fields_and_files_are_rejected(self):
        extras=[b'--Boundary\r\nContent-Disposition: form-data; name="extra"\r\n\r\nvalue\r\n',
                b'--Boundary\r\nContent-Disposition: form-data; name="file"; filename="second.pdf"\r\n\r\n%PDF-test\r\n']
        for extra in extras:self.assertEqual(asyncio.run(call('/api/upload',multipart(b'%PDF-test',extra),FORM))[0],400)

    def test_stream_timeout_and_retry(self):
        async def receive():await asyncio.sleep(1);return {'type':'http.request','body':b'','more_body':True}
        with patch.object(limits,'MAX_UPLOAD_SECONDS',0.02):
            status,data,_=asyncio.run(call('/api/upload',headers=FORM,receive=receive))
        self.assertEqual((status,data['code']),(408,'upload_timeout'))
        self.assertEqual(asyncio.run(call('/api/upload',multipart(b'%PDF-test'),FORM))[0],200)

    def test_upload_admission_and_health_during_held_streams(self):
        async def scenario():
            release=asyncio.Event();entered=0
            async def receive():
                nonlocal entered
                entered+=1;await release.wait();return {'type':'http.request','body':multipart(b'%PDF-test'),'more_body':False}
            tasks=[asyncio.create_task(call('/api/upload',headers=FORM,receive=receive)) for _ in range(2)]
            while entered<2:await asyncio.sleep(0)
            async def unread():self.fail('Excess upload read')
            try:
                status,data,headers=await call('/api/upload',headers=FORM,receive=unread)
                self.assertEqual((status,data['code']),(503,'server_busy'));self.assertIn(b'retry-after',headers)
                self.assertEqual((await call('/api/ping',method='GET'))[0],200)
            finally:release.set();await asyncio.gather(*tasks)
            self.assertEqual((await call('/api/upload',multipart(b'%PDF-test'),FORM))[0],200)
        asyncio.run(scenario())

    def test_processing_admission_keeps_health_responsive(self):
        async def scenario():
            entered=threading.Event();release=threading.Event()
            def held(*args):entered.set();release.wait(3);return {'grandTotal':100}
            with patch.object(backend,'_analysis_job',side_effect=held):
                first=asyncio.create_task(call('/api/analyze',self.payload,JSON))
                await asyncio.to_thread(entered.wait,1)
                try:
                    self.assertEqual((await call('/api/ping',method='GET'))[0],200)
                    status,data,_=await call('/api/analyze',self.payload,JSON)
                    self.assertEqual((status,data['code']),(503,'server_busy'))
                finally:release.set();self.assertEqual((await first)[0],200)
        asyncio.run(scenario())

    def test_rate_identity_ignores_spoofing_and_separates_trusted_visitors(self):
        with patch.object(limits,'UPLOAD_RATE_MAX',1),patch.dict(os.environ,{'TRUSTED_PROXY_IPS':''}):
            self.assertEqual(asyncio.run(call('/api/upload',multipart(b'%PDF-test'),FORM+[(b'x-forwarded-for',b'198.51.100.1')]))[0],200)
            self.assertEqual(asyncio.run(call('/api/upload',multipart(b'%PDF-test'),FORM+[(b'x-forwarded-for',b'198.51.100.2')]))[0],429)
        limits.rates.clients.clear()
        with patch.object(limits,'UPLOAD_RATE_MAX',1),patch.dict(os.environ,{'TRUSTED_PROXY_IPS':'127.0.0.1'}):
            for visitor in [b'198.51.100.1',b'198.51.100.2']:
                self.assertEqual(asyncio.run(call('/api/upload',multipart(b'%PDF-test'),FORM+[(b'x-forwarded-for',visitor)]))[0],200)
            self.assertEqual(asyncio.run(call('/api/upload',multipart(b'%PDF-test'),FORM+[(b'x-forwarded-for',b'192.0.2.1, 198.51.100.1')]))[0],429)

    def test_upload_rate_before_body_and_window_recovery(self):
        with patch.object(limits,'UPLOAD_RATE_MAX',1):
            self.assertEqual(asyncio.run(call('/api/upload',multipart(b'%PDF-test'),FORM))[0],200)
            async def unread():self.fail('Rate limited upload read')
            status,data,headers=asyncio.run(call('/api/upload',headers=FORM,receive=unread))
            self.assertEqual((status,data['code']),(429,'rate_limited'));self.assertIn(b'retry-after',headers)
            now=time.monotonic()
            with patch.object(limits.time,'monotonic',return_value=now+3601):limits.rates.take('127.0.0.1','upload',1)

    def test_preview_rate_and_raster_rejection_before_render(self):
        with patch.object(limits,'PREVIEW_RATE_MAX',1),patch.object(backend,'_find_gs',side_effect=backend.ProcessingError('processor_unavailable')) as gs:
            self.assertEqual(asyncio.run(call('/api/preview',self.payload,JSON))[0],503)
            status,data,_=asyncio.run(call('/api/preview',self.payload,JSON))
            self.assertEqual((status,data['code']),(429,'rate_limited'));self.assertEqual(gs.call_count,1)
        limits.rates.clients.clear()
        for endpoint,render in [('/api/preview','_render_preview'),('/api/analyze','_analyze_ink_coverage')]:
            with patch.object(backend,'_find_gs',return_value='gs'),patch.object(backend,'_get_page_count',return_value=1),patch.object(backend,'_get_page_dimensions',return_value=(1e8,144)),patch.object(backend,render) as work:
                status,data,_=asyncio.run(call(endpoint,self.payload,JSON))
                self.assertEqual((status,data['code']),(413,'raster_limit'));work.assert_not_called()

    def test_page_count_limit_prevents_geometry_and_render(self):
        for endpoint in ['/api/preview','/api/analyze']:
            with patch.object(backend,'_find_gs',return_value='gs'),patch.object(backend,'_get_page_count',return_value=101),patch.object(backend,'_get_page_dimensions') as dims:
                status,data,_=asyncio.run(call(endpoint,self.payload,JSON))
                self.assertEqual((status,data['code']),(413,'page_limit'));dims.assert_not_called()

    def test_retained_document_and_byte_capacity(self):
        for name,value in [('MAX_STORED_DOCUMENTS',1),('MAX_STORED_UPLOAD_BYTES',limits.MAX_UPLOAD_BYTES)]:
            async def unread():self.fail('Upload admitted without storage capacity')
            with patch.object(limits,name,value):
                status,data,_=asyncio.run(call('/api/upload',headers=FORM,receive=unread))
                self.assertEqual((status,data['code']),(503,'server_busy'))


class PolicyTests(unittest.TestCase):
    def test_processor_diagnostics_and_time_are_bounded(self):
        with patch.object(limits,'MAX_DIAGNOSTIC_BYTES',1024):
            with self.assertRaises(limits.WorkloadError) as exc:
                limits.execute_processor([sys.executable,'-c','print("x"*1000000)'],3)
            self.assertEqual(exc.exception.code,'job_limit')
        with self.assertRaises(subprocess.TimeoutExpired):
            limits.execute_processor([sys.executable,'-c','import time;time.sleep(10)'],0.05)
        result=limits.execute_processor([sys.executable,'-c','print("recovered")'],3)
        self.assertEqual(result.stdout.strip(),'recovered')

    def test_processor_working_disk_budget_includes_scratch(self):
        root=Path(__file__).resolve().parents[1]/'tmp'
        with tempfile.TemporaryDirectory(dir=root) as directory:
            script='import pathlib,time;pathlib.Path("scratch").write_bytes(b"x"*20000);time.sleep(3)'
            # Use TMPDIR exactly as Ghostscript would; no huge disk fixture.
            script='import os;os.chdir(os.environ["TMPDIR"]);'+script
            token=limits.WORK_DIR.set(Path(directory))
            try:
                with patch.object(limits,'MAX_JOB_DISK_BYTES',1024):
                    with self.assertRaises(limits.WorkloadError) as exc:
                        limits.execute_processor([sys.executable,'-c',script],3)
                    self.assertEqual(exc.exception.code,'job_limit')
            finally:limits.WORK_DIR.reset(token)

    def test_cancelled_job_retains_slot_until_thread_finishes(self):
        async def scenario():
            runner=limits.JobRunner();entered=threading.Event();release=threading.Event()
            def blocked():entered.set();release.wait(3);return 'finished'
            task=asyncio.create_task(runner.run(blocked));await asyncio.to_thread(entered.wait,1);task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            try:
                self.assertEqual(runner.active,1)
                with self.assertRaises(limits.WorkloadError) as exc:await runner.run(lambda:None)
                self.assertEqual(exc.exception.code,'server_busy')
            finally:release.set()
            for _ in range(100):
                if not runner.active:break
                await asyncio.sleep(0.01)
            self.assertEqual(await runner.run(lambda:42),42);self.assertEqual(runner.active,0)
        asyncio.run(scenario())

    def test_failure_releases_slot_and_deadline_propagates(self):
        async def scenario():
            runner=limits.JobRunner()
            def fail():self.assertIsNotNone(limits.DEADLINE.get());raise ValueError('test')
            with self.assertRaises(ValueError):await runner.run(fail)
            self.assertEqual(runner.active,0);self.assertEqual(await runner.run(lambda:1),1)
        asyncio.run(scenario())
        token=limits.DEADLINE.set(time.monotonic()-1)
        try:
            with self.assertRaises(limits.WorkloadError):limits.remaining_timeout(30)
        finally:limits.DEADLINE.reset(token)

    def test_rate_store_does_not_evict_live_quotas(self):
        limiter=limits.RateLimiter()
        with patch.object(limits,'MAX_RATE_CLIENTS',2):
            limiter.take('one','analysis',1);limiter.take('two','analysis',1)
            with self.assertRaises(limits.WorkloadError) as exc:limiter.take('three','analysis',1)
            self.assertEqual(exc.exception.code,'server_busy')
            with self.assertRaises(limits.WorkloadError) as exc:limiter.take('one','analysis',1)
            self.assertEqual(exc.exception.code,'rate_limited');self.assertEqual(len(limiter.clients),2)

    def test_only_trusted_peers_can_supply_valid_forwarded_chain(self):
        def scope(peer,values):return {'client':(peer,1),'headers':[(b'x-forwarded-for',v) for v in values]}
        with patch.dict(os.environ,{'TRUSTED_PROXY_IPS':''}):
            self.assertEqual(limits.client_identity(scope('127.0.0.1',[b'198.51.100.1'])),'127.0.0.1')
        with patch.dict(os.environ,{'TRUSTED_PROXY_IPS':'127.0.0.1,10.0.0.1,::1'}):
            self.assertEqual(limits.client_identity(scope('203.0.113.1',[b'198.51.100.1'])),'203.0.113.1')
            self.assertEqual(limits.client_identity(scope('127.0.0.1',[b'192.0.2.99,198.51.100.1'])),'198.51.100.1')
            self.assertEqual(limits.client_identity(scope('127.0.0.1',[b'198.51.100.1,10.0.0.1'])),'198.51.100.1')
            self.assertEqual(limits.client_identity(scope('::1',[b'2001:db8::1'])),'2001:db8::1')
            for values in [[b'garbage'],[b'198.51.100.1',b'192.0.2.2'],[b'']]:
                self.assertEqual(limits.client_identity(scope('127.0.0.1',values)),'127.0.0.1')

    def test_raster_boundaries_scaling_and_actual_output(self):
        limits.check_raster(2000,2500,72,'analysis')
        for args in [(2000,2501,72,'analysis'),(1e308,1,150,'preview'),(10001,1,72,'analysis'),(144,144,150,'preview',1000)]:
            with self.assertRaises(limits.WorkloadError):limits.check_raster(*args)
        root=Path(__file__).resolve().parents[1]/'tmp'
        with tempfile.TemporaryDirectory(dir=root) as directory:
            file=Path(directory)/'test.png';Image.new('RGB',(10,10)).save(file)
            with patch.object(limits,'MAX_PREVIEW_PIXELS',99):
                with self.assertRaises(limits.WorkloadError):backend._image_size(file,'PNG','RGB','preview_failed')


if __name__ == '__main__':unittest.main()
