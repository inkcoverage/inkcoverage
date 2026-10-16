"""Controlled clock, persistent lifetime, cleanup retry and executor lease tests."""
import asyncio
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app as backend
from lifetime import FileLifetime, Unavailable
from test_session5 import call, multipart, FORM, JSON


class LifetimeTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        self.root = Path(self.temp.name)
        self.now = 1000
        patch('lifetime.time.time', side_effect=lambda: self.now).start()
        patch.object(backend, 'TEMP_DIR', self.root).start()
        self.files = FileLifetime(lambda: self.root, lambda: 600)
        patch.object(backend, 'files', self.files).start()
        backend.limits.rates.clients.clear()
        self.doc = self.root / ('a'*32)
        self.files.create(self.doc)
        (self.doc / 'upload.pdf').write_bytes(b'%PDF-test')
        self.files.ready(self.doc)
        self.files.release(self.doc)
        self.payload = json.dumps(dict(id='a'*32, page=1, cropLeft=0, cropTop=0,
                                      cropWidth=144, cropHeight=144, pdfWidthPt=144, pdfHeightPt=144)).encode()

    def test_exact_expiry_denies_both_apis_before_quota_or_processing(self):
        self.now = 1600
        with patch.object(backend, '_preview_job') as preview, patch.object(backend, '_analysis_job') as analyze:
            for endpoint in ['/api/preview', '/api/analyze']:
                status, data, headers = asyncio.run(call(endpoint, self.payload, JSON))
                self.assertEqual((status, data['code']), (410, 'document_unavailable'))
                self.assertEqual(headers[b'cache-control'], b'no-store')
            preview.assert_not_called(); analyze.assert_not_called()
        self.assertEqual(backend.limits.rates.clients, {})
        self.assertTrue(self.doc.exists())  # Access expiry precedes physical removal.
        self.files.sweep()
        self.assertFalse(self.doc.exists())
        self.assertEqual(asyncio.run(call('/api/preview', self.payload, JSON))[0], 404)

    def test_repeated_previews_and_mtime_do_not_extend_expiry(self):
        with patch.object(backend, '_preview_job', return_value={'image':'test'}):
            for self.now in [1000, 1200, 1599]:
                self.assertEqual(asyncio.run(call('/api/preview', self.payload, JSON))[0], 200)
                os.utime(self.doc, (999999, 999999))
                self.files.sweep()
                self.assertTrue(self.doc.exists())
        self.assertEqual(self.files._read(self.doc)['expiresAt'], 1600)
        self.now = 1600
        self.files.sweep()
        self.assertFalse(self.doc.exists())

    def test_restart_keeps_fixed_expiry_and_cleans_orphans_without_mtime(self):
        orphan = self.root / ('b'*32); orphan.mkdir(); (orphan/'upload.pdf').write_bytes(b'test')
        scratch = self.root / ('analysis_'+'c'*32); scratch.mkdir()
        foreign = self.root/'unowned'; foreign.mkdir()
        restarted = FileLifetime(lambda: self.root, lambda: 99999)
        self.now = 1599
        restarted.sweep()
        self.assertTrue(self.doc.exists()); self.assertTrue(foreign.exists())
        self.assertFalse(orphan.exists()); self.assertFalse(scratch.exists())
        self.now = 1600
        restarted.sweep()
        self.assertFalse(self.doc.exists())

    def test_failed_removal_is_observable_denied_and_retried(self):
        self.now = 1600
        with patch('lifetime.shutil.rmtree', side_effect=PermissionError('private filename')), self.assertLogs('uvicorn.error.cleanup', 'WARNING') as captured:
            status = self.files.sweep()
        self.assertEqual(status['lastFailures'], 1); self.assertEqual(status['removed'], 0)
        self.assertNotIn('private filename', ' '.join(captured.output))
        self.assertTrue(self.doc.exists())
        with self.assertRaises(Unavailable): self.files.acquire(self.doc)
        status = self.files.sweep()
        self.assertEqual(status['lastFailures'], 0); self.assertEqual(status['failures'], 1)
        self.assertFalse(self.doc.exists())

    def test_preview_derivative_retry_leaves_live_original(self):
        derivative = self.doc/'preview_test'; derivative.mkdir(); (derivative/'output.png').write_bytes(b'test')
        with patch('lifetime.shutil.rmtree', side_effect=PermissionError()), self.assertLogs('uvicorn.error.cleanup', 'WARNING'):
            self.files.remove_derivative(derivative, self.doc)
        self.files.sweep()
        self.assertFalse(derivative.exists()); self.assertTrue((self.doc/'upload.pdf').exists())

    def test_scratch_lease_protects_separations_until_worker_finishes(self):
        scratch = self.root / ('analysis_'+'b'*32)
        self.files.create(scratch, scratch=True)
        self.now = 9999
        self.files.sweep(); self.assertTrue(scratch.exists())
        self.files.release(scratch); self.assertFalse(scratch.exists())

    def test_corrupt_manifest_never_reopens_access(self):
        for value in ['{}', '{"uploadedAt":1000,"expiresAt":NaN,"state":"ready"}', 'x'*1025]:
            (self.doc/'lifetime.json').write_text(value)
            with self.assertRaises(Unavailable): self.files.acquire(self.doc)
        self.files.sweep(); self.assertFalse(self.doc.exists())

    def test_cleanup_never_follows_an_escaping_target(self):
        # Deterministically simulate a junction resolution on Windows, without
        # requiring permission to create symlinks or touching an outside tree.
        original = Path.resolve
        outside = self.root.parent / 'outside-protected'
        def resolve(path, *args, **kwargs):
            return outside if path == self.doc else original(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve), patch('lifetime.shutil.rmtree') as remove, self.assertLogs('uvicorn.error.cleanup','WARNING'):
            self.assertEqual(self.files.sweep()['lastFailures'],1)
            remove.assert_not_called()
        self.assertTrue((self.doc/'upload.pdf').exists())

    def test_busy_and_rate_rejection_release_leases(self):
        with patch.object(backend.limits.jobs, 'active', backend.limits.MAX_CONCURRENT_PROCESSING):
            self.assertEqual(asyncio.run(call('/api/analyze', self.payload, JSON))[0], 503)
        self.assertEqual(self.files.active, {})
        with patch.object(backend.limits, 'RATE_LIMIT_MAX', 1), patch.object(backend, 'RATE_LIMIT_MAX', 1):
            self.assertEqual(asyncio.run(call('/api/analyze', self.payload, JSON))[0], 429)
        self.assertEqual(self.files.active, {})

    def test_expiry_during_processing_finishes_started_job_but_denies_new_work(self):
        async def scenario(cancel=False):
            entered = threading.Event(); release = threading.Event()
            def held(*args):
                entered.set(); release.wait(3)
                self.assertTrue((self.doc/'upload.pdf').exists())
                return {'grandTotal':50}
            with patch.object(backend, '_analysis_job', side_effect=held):
                task = asyncio.create_task(call('/api/analyze', self.payload, JSON))
                await asyncio.to_thread(entered.wait, 1)
                try:
                    self.now = 1600
                    self.files.sweep(); self.assertTrue(self.doc.exists())
                    self.assertEqual((await call('/api/preview', self.payload, JSON))[0], 410)
                    if cancel:
                        task.cancel()
                        with self.assertRaises(asyncio.CancelledError): await task
                        self.files.sweep(); self.assertTrue(self.doc.exists())
                        self.assertTrue(self.files.active)
                finally: release.set()
                if not cancel: self.assertEqual((await task)[0], 200)
                while backend.limits.jobs.active: await asyncio.sleep(0.001)
                self.assertEqual(self.files.active, {})
                self.files.sweep(); self.assertFalse(self.doc.exists())
        asyncio.run(scenario())

    def test_http_cancellation_cannot_release_running_job(self):
        # Reuse the held-job scenario, explicitly cancelling only the HTTP task.
        async def scenario():
            entered = threading.Event(); release = threading.Event()
            def held(*args): entered.set(); release.wait(3); return {}
            with patch.object(backend, '_analysis_job', side_effect=held):
                task = asyncio.create_task(call('/api/analyze', self.payload, JSON))
                await asyncio.to_thread(entered.wait, 1)
                try:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
                    self.now = 1600
                    self.files.sweep(); self.assertTrue(self.doc.exists()); self.assertTrue(self.files.active)
                finally: release.set()
                while backend.limits.jobs.active: await asyncio.sleep(0.001)
                self.files.sweep(); self.assertFalse(self.doc.exists())
        asyncio.run(scenario())

    def test_upload_timestamps_survive_preview(self):
        with patch.object(backend, '_inspect_upload', return_value=1):
            status, data, _ = asyncio.run(call('/api/upload', multipart(b'%PDF-test'), FORM))
        self.assertEqual(status, 200)
        self.assertEqual((data['uploadedAt'], data['expiresAt'], data['remainingSeconds']), (1000,1600,600))
        self.assertEqual(self.files._read(self.root/data['id'])['state'], 'ready')

    def test_cancelled_upload_inspection_is_not_deleted_under_worker(self):
        async def scenario():
            entered = threading.Event(); release = threading.Event(); path = []
            def held(pdf):
                path.append(Path(pdf)); entered.set(); release.wait(3)
                self.assertTrue(path[0].is_file()); return 1
            with patch.object(backend, '_inspect_upload', side_effect=held):
                task = asyncio.create_task(call('/api/upload', multipart(b'%PDF-test'), FORM))
                await asyncio.to_thread(entered.wait, 1)
                try:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task
                    self.files.sweep(); self.assertTrue(path[0].is_file())
                finally: release.set()
                while backend.limits.jobs.active: await asyncio.sleep(0.001)
                self.assertFalse(path[0].parent.exists()); self.assertEqual(self.files.active,{})
        asyncio.run(scenario())

    def test_cancelled_copy_waits_for_its_disk_write_before_closing(self):
        async def scenario():
            entered=threading.Event(); release=threading.Event()
            class Writer:
                closed=False
                def write(self, chunk):
                    entered.set(); release.wait(3)
                    self_test.assertFalse(self.closed)
                def close(self): self.closed=True
            self_test=self
            output=Writer()
            async def copy():
                try: await backend._write_upload_chunk(output,b'test')
                finally: output.close()
            task=asyncio.create_task(copy())
            while not entered.is_set(): await asyncio.sleep(.001)
            try:
                task.cancel(); await asyncio.sleep(.01)
                task.cancel(); await asyncio.sleep(.01)
                self.assertFalse(task.done()); self.assertFalse(output.closed)
            finally: release.set()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertTrue(output.closed)
        asyncio.run(scenario())


if __name__ == '__main__': unittest.main()
