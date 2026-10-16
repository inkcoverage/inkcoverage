"""Request-boundary regression tests; run with unittest, no extra packages."""
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as backend


async def request(endpoint, payload):
    body = json.dumps(payload).encode()
    messages = []
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}
    async def send(message):
        messages.append(message)
    scope = dict(type="http", asgi={"version": "3.0"}, http_version="1.1",
                 method="POST", scheme="http", path=endpoint, raw_path=endpoint.encode(),
                 query_string=b"", root_path="", headers=[(b"content-type", b"application/json")],
                 client=("127.0.0.1", 12345), server=("127.0.0.1", 8001))
    await backend.app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    content = json.loads(b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))
    return status, content


class RequestTests(unittest.TestCase):
    def setUp(self):
        workspace_tmp = Path(__file__).resolve().parents[1] / "tmp"
        workspace_tmp.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=workspace_tmp)
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(patch.stopall)
        patch.object(backend, "TEMP_DIR", self.root).start()
        self.document_id = "a" * 32
        session = self.root / self.document_id
        backend.files.create(session)
        backend.files.ready(session)
        backend.files.release(session)
        (session / "upload.pdf").write_bytes(b"%PDF-test-boundary")
        self.gs = patch.object(backend, "_find_gs", return_value="gs").start()
        self.count = patch.object(backend, "_get_page_count", return_value=3).start()
        self.dimensions = patch.object(backend, "_get_page_dimensions", return_value=(144, 144)).start()
        self.analysis = patch.object(backend, "_analyze_ink_coverage", return_value={"channels": []}).start()
        self.render = patch.object(backend, "_render_preview").start()
        backend.limits.rates.clients.clear()
        self.valid = dict(id=self.document_id, page=1, cropLeft=0, cropTop=0,
                          cropWidth=144, cropHeight=144, pdfWidthPt=144, pdfHeightPt=144)

    def call(self, endpoint, payload):
        return asyncio.run(request(endpoint, payload))

    def assert_rejected(self, endpoint, payload, expected=422):
        status, response = self.call(endpoint, payload)
        self.assertEqual(status, expected, response)
        self.assertIsInstance(response['detail'], str)
        self.assertNotIn(str(self.root), response['detail'])
        self.analysis.assert_not_called()
        self.render.assert_not_called()

    def test_bad_ids_never_access_ghostscript(self):
        for document_id in ['../outside', '..\\outside', '/etc', 'C:\\outside', '', 'A'*32, 'g'*32, 'a'*31, 'a'*33]:
            for endpoint in ['/api/preview', '/api/analyze']:
                with self.subTest(id=document_id, endpoint=endpoint):
                    self.assert_rejected(endpoint, dict(self.valid, id=document_id))
        self.gs.assert_not_called()

    def test_invalid_page_types_and_ranges(self):
        for page in [0, -1, 1.5, True, '1', None]:
            for endpoint in ['/api/preview', '/api/analyze']:
                with self.subTest(page=page, endpoint=endpoint):
                    self.assert_rejected(endpoint, dict(self.valid, page=page))
        for endpoint in ['/api/preview', '/api/analyze']:
            self.assert_rejected(endpoint, dict(self.valid, page=4), 400)

    def test_non_finite_negative_and_empty_geometry(self):
        for field in ['cropLeft', 'cropTop', 'cropWidth', 'cropHeight', 'pdfWidthPt', 'pdfHeightPt']:
            for value in [float('nan'), float('inf'), -float('inf'), -1, True, False, '1', None]:
                with self.subTest(field=field, value=value):
                    self.assert_rejected('/api/analyze', dict(self.valid, **{field: value}))
        for field in ['cropWidth', 'cropHeight', 'pdfWidthPt', 'pdfHeightPt']:
            self.assert_rejected('/api/analyze', dict(self.valid, **{field: 0}))

    def test_out_of_bounds_and_forged_dimensions(self):
        for change in [dict(cropWidth=145), dict(cropHeight=145), dict(cropLeft=144),
                       dict(cropTop=144), dict(cropLeft=100, cropWidth=50),
                       dict(cropTop=100, cropHeight=50), dict(pdfWidthPt=288, cropWidth=288),
                       dict(pdfHeightPt=100), dict(cropWidth=1e308)]:
            with self.subTest(change=change):
                self.assert_rejected('/api/analyze', dict(self.valid, **change), 400)

    def test_server_geometry_and_roundoff_are_used(self):
        payload = dict(self.valid, cropWidth=144 + 5e-7)
        self.assertEqual(self.call('/api/analyze', payload)[0], 200)
        self.assertEqual(self.analysis.call_args.args[-4:], (144, 144, 144, 144))

    def test_missing_document(self):
        for endpoint in ['/api/preview', '/api/analyze']:
            self.assert_rejected(endpoint, dict(self.valid, id='b'*32), 404)

    def test_resolved_session_and_file_cannot_escape(self):
        original_resolve = Path.resolve
        for escape in ['session', 'file']:
            def resolve(path, *args, **kwargs):
                if (escape == 'session' and path.name == self.document_id
                        or escape == 'file' and path.name == 'upload.pdf'):
                    return self.root.parent / 'outside'
                return original_resolve(path, *args, **kwargs)
            with patch.object(Path, 'resolve', resolve):
                for endpoint in ['/api/preview', '/api/analyze']:
                    self.assert_rejected(endpoint, self.valid, 400)

    def test_unreadable_metadata_is_not_guessed(self):
        self.count.side_effect = RuntimeError('private/path/error')
        for endpoint in ['/api/preview', '/api/analyze']:
            self.assert_rejected(endpoint, self.valid)

    def test_raster_empty_selection_is_an_error(self):
        self.analysis.side_effect = backend.InvalidSelection('Selection is too small at the analysis resolution; select a larger area')
        status, response = self.call('/api/analyze', dict(self.valid, cropWidth=0.0001))
        self.assertEqual(status, 400)
        self.assertIn('too small', response['detail'])

    def test_preview_output_cannot_escape(self):
        original_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            if path.name == 'preview_1.png': return self.root.parent / 'outside.png'
            return original_resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve):
            self.assert_rejected('/api/preview', self.valid, 400)

    def test_rate_limit_precedes_metadata_and_rendering(self):
        for _ in range(backend.RATE_LIMIT_MAX): backend.limits.rates.take('127.0.0.1', 'analysis', backend.RATE_LIMIT_MAX)
        self.assert_rejected('/api/analyze', self.valid, 429)
        self.gs.assert_not_called()
        self.count.assert_not_called()

    def test_actual_tiff_rejects_empty_raster_crop(self):
        from PIL import Image
        tiff = self.root / 'gray.tif'
        Image.new('L', (144, 144), 0).save(tiff)
        with self.assertRaises(backend.InvalidSelection):
            backend._analyze_tiff_coverage(str(tiff), 0, 0, 0.001, 144, 144, 144)
        self.assertEqual(backend._analyze_tiff_coverage(str(tiff), 0, 0, 1, 144, 144, 144), 100)


class MetadataTests(unittest.TestCase):
    def test_page_count_rejects_errors_and_guesses(self):
        for code, stdout in [(1, 'Error 12'), (0, ''), (0, '0'), (0, '3\nError 12')]:
            with patch.object(backend.limits, 'execute_processor', return_value=Mock(returncode=code, stdout=stdout)):
                with self.assertRaises(RuntimeError): backend._get_page_count('gs', 'file.pdf')

    def test_page_dimensions_require_finite_positive_geometry(self):
        for stdout in ['', '0\n0\nNaN\n144', '0\n0\n0\n144', '0\n0\n144\nInfinity', '0\n0\n144\n144\nextra', '-1e308\n0\n1e308\n144']:
            with patch.object(backend.limits, 'execute_processor', return_value=Mock(returncode=0, stdout=stdout)):
                with self.assertRaises(RuntimeError): backend._get_page_dimensions('gs', 'file.pdf', 1)

    def test_valid_page_count_and_nonzero_origin(self):
        with patch.object(backend.limits, 'execute_processor', return_value=Mock(returncode=0, stdout='3\n')):
            self.assertEqual(backend._get_page_count('gs', 'file.pdf'), 3)
        with patch.object(backend.limits, 'execute_processor', return_value=Mock(returncode=0, stdout='10\n20\n154\n164')):
            self.assertEqual(backend._get_page_dimensions('gs', 'file.pdf', 1), (144, 144))


if __name__ == '__main__': unittest.main()
