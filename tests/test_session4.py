"""Processing failures must never look like successful zero coverage."""
import asyncio
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as backend
from PIL import Image
from fastapi import UploadFile
from test_session3 import request


class ProcessingTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / 'tmp'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(backend, 'TEMP_DIR', self.root).start()
        patch.object(backend, '_get_page_user_unit', return_value=1).start()
        patch.object(backend, '_get_page_dimensions', return_value=(144,144)).start()
        backend.limits.rates.clients.clear()

    def assert_error(self, code, callback):
        with self.assertRaises(backend.ProcessingError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(str(self.root), str(caught.exception))

    def test_nonzero_exit_even_with_output_is_rejected(self):
        for fn, args, code in [
            (backend._get_page_count, ('gs', 'file.pdf'), 'pdf_invalid'),
            (backend._render_preview, ('gs', 'file.pdf', 1, str(self.root/'preview.png')), 'preview_failed'),
            (backend._run_tiffsep, ('gs', 'file.pdf', 1, str(self.root)), 'analysis_failed')]:
            with patch.object(backend.limits, 'execute_processor', return_value=Mock(returncode=1, stdout='1', stderr='private/path/error')):
                self.assert_error(code, lambda: fn(*args))

    def test_timeout_and_unavailable_are_safe(self):
        for error, code in [(subprocess.TimeoutExpired('private/path', 30), 'pdf_timeout'),
                            (FileNotFoundError('private/path'), 'processor_unavailable')]:
            with patch.object(backend.limits, 'execute_processor', side_effect=error):
                self.assert_error(code, lambda: backend._get_page_count('gs', 'file.pdf'))

    def test_old_or_unrecognized_processor_version_is_unavailable(self):
        for version in ['10.05.1', 'unknown']:
            with patch.object(backend.limits, 'execute_processor', return_value=Mock(returncode=0,stdout=version)):
                self.assert_error('processor_unavailable', backend._find_gs)
        with patch.object(backend.limits, 'execute_processor', return_value=Mock(returncode=0,stdout='10.08.0')):
            self.assertEqual(backend._find_gs(), backend.GS_EXECUTABLE)

    def test_truncated_raster_payloads_are_rejected(self):
        for format,mode in [('PNG','RGB'),('TIFF','L')]:
            file = self.root/('truncated.'+format.lower())
            Image.new(mode,(100,100),0).save(file,format=format)
            data=file.read_bytes();file.write_bytes(data[:len(data)//2])
            self.assert_error('analysis_failed',lambda:backend._image_size(file,format,mode,'analysis_failed'))

    def test_password_is_distinguished(self):
        with patch.object(backend.limits, 'execute_processor', return_value=Mock(returncode=1, stdout='', stderr='This file requires a password')):
            self.assert_error('pdf_encrypted', lambda: backend._get_page_count('gs', 'file.pdf'))

    def test_metadata_uses_exact_read_permission_and_data_filename(self):
        path = self.root / 'parentheses (data).pdf'
        with patch.object(backend.limits, 'execute_processor', return_value=Mock(returncode=0, stdout='1')) as run:
            self.assertEqual(backend._get_page_count('gs', str(path)), 1)
        command = run.call_args.args[0]
        self.assertIn('-dSAFER', command)
        self.assertNotIn('-dNOSAFER', command)
        self.assertIn('--permit-file-read='+path.resolve().as_posix(), command)
        self.assertNotIn(str(path), command[-1])

    def test_missing_corrupt_and_stale_previews_are_rejected(self):
        output = self.root/'preview.png'
        for previous in [False, True]:
            if previous: Image.new('RGB', (2,2)).save(output)
            with patch.object(backend, '_run_gs'):
                self.assert_error('preview_failed', lambda: backend._render_preview('gs','file.pdf',1,str(output)))
        def corrupt(*args): output.write_bytes(b'PNG header only')
        with patch.object(backend, '_run_gs', side_effect=corrupt):
            self.assert_error('preview_failed', lambda: backend._render_preview('gs','file.pdf',1,str(output)))

    def write_separations(self, directory, missing=None, corrupt=None, mismatch=None):
        directory = Path(directory)
        Image.new('CMYK', (2,2)).save(directory/'sep.tif')
        for name in ['Cyan', 'Magenta', 'Yellow', 'Black', 'Spot (nested)']:
            if name == missing: continue
            file = directory/f'sep({name}).tif'
            if name == corrupt: file.write_bytes(b'broken TIFF')
            else: Image.new('L', (3,2) if name == mismatch else (2,2), 255).save(file)

    def analyze(self):
        return backend._analyze_ink_coverage('gs','file.pdf',1,0,0,144,144,144,144)

    def test_empty_partial_corrupt_and_mismatched_separations_fail_and_clean_up(self):
        for failure in ['empty','missing','corrupt','mismatch']:
            def render(gs,pdf,page,directory):
                if failure != 'empty': self.write_separations(directory, **{failure:'Cyan'})
            with patch.object(backend, '_run_tiffsep', side_effect=render):
                self.assert_error('analysis_failed', self.analyze)
            self.assertEqual(list(self.root.iterdir()), [])

    def test_legitimate_blank_page_is_zero_and_names_preserve_parentheses(self):
        with patch.object(backend, '_run_tiffsep', side_effect=lambda g,p,n,d:self.write_separations(d)):
            result = self.analyze()
        self.assertEqual(result['grandTotal'], 0)
        self.assertEqual(len(result['channels']), 5)
        self.assertIn('Spot (nested)', [c['name'] for c in result['channels']])

    def test_failed_uploads_remove_original(self):
        for contents in [b'not a PDF', b'%PDF-1.4 broken']:
            file = UploadFile(filename='broken.pdf', file=io.BytesIO(contents))
            with patch.object(backend, '_find_gs', return_value='gs'), patch.object(backend, '_get_page_count', side_effect=backend.ProcessingError('pdf_invalid')):
                self.assert_error('pdf_invalid', lambda: asyncio.run(backend._store_upload(file)))
            self.assertEqual(list(self.root.iterdir()), [])

    def test_api_error_codes_status_and_preview_cleanup(self):
        doc = self.root/('a'*32);backend.files.create(doc);backend.files.ready(doc);backend.files.release(doc);(doc/'upload.pdf').write_bytes(b'%PDF-1.4')
        payload=dict(id='a'*32,page=1,cropLeft=0,cropTop=0,cropWidth=144,cropHeight=144,pdfWidthPt=144,pdfHeightPt=144)
        for endpoint, function in [('/api/preview','_render_preview'),('/api/analyze','_analyze_ink_coverage')]:
            for code in backend.PROCESSING_ERRORS:
                backend.limits.rates.clients.clear()
                with patch.object(backend,'_find_gs',return_value='gs'), patch.object(backend,'_get_page_count',return_value=1), patch.object(backend,'_get_page_dimensions',return_value=(144,144)), patch.object(backend,function,side_effect=backend.ProcessingError(code)):
                    status, data = asyncio.run(request(endpoint,payload))
                self.assertEqual(status, 503 if code=='processor_unavailable' else 422)
                self.assertEqual(data['code'],code)
                self.assertNotIn(str(self.root),data['detail'])
                self.assertEqual(sorted(p.name for p in doc.iterdir()), ['lifetime.json', 'upload.pdf'])


if __name__ == '__main__': unittest.main()
