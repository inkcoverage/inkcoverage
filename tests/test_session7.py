"""Read-only per-client allowance snapshots and rolling-window integration."""
import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workload as limits
from test_session5 import call


class AllowanceTests(unittest.TestCase):
    def setUp(self):
        limits.rates.clients.clear()
        self.addCleanup(limits.rates.clients.clear)

    def test_unknown_client_reads_do_not_allocate(self):
        for number in range(100):
            data = limits.rates.snapshot(str(number))
            self.assertEqual(data['analysis']['remaining'], limits.RATE_LIMIT_MAX)
            self.assertEqual(data['analysis']['retryAfter'], 0)
        self.assertEqual(len(limits.rates.clients), 0)

    def test_separate_allowances_and_reads_do_not_extend_lifetime(self):
        with patch.object(limits.time, 'monotonic', return_value=100):
            limits.rates.take('client', 'analysis', limits.RATE_LIMIT_MAX)
            limits.rates.take('client', 'upload', limits.UPLOAD_RATE_MAX)
        with patch.object(limits.time, 'monotonic', return_value=200):
            data = limits.rates.snapshot('client')
        self.assertEqual(data['analysis']['remaining'], limits.RATE_LIMIT_MAX - 1)
        self.assertEqual(data['upload']['remaining'], limits.UPLOAD_RATE_MAX - 1)
        self.assertEqual(data['preview']['remaining'], limits.PREVIEW_RATE_MAX)
        self.assertEqual(limits.rates.clients['client']['last'], 100)

    def test_exhaustion_retry_after_matches_enforcement(self):
        with patch.object(limits.time, 'monotonic', return_value=100):
            for _ in range(limits.RATE_LIMIT_MAX): limits.rates.take('client', 'analysis', limits.RATE_LIMIT_MAX)
        with patch.object(limits.time, 'monotonic', return_value=200):
            data = limits.rates.snapshot('client')['analysis']
            with self.assertRaises(limits.WorkloadError) as caught: limits.rates.take('client', 'analysis', limits.RATE_LIMIT_MAX)
        self.assertEqual(data['remaining'], 0)
        self.assertEqual(data['retryAfter'], caught.exception.retry_after)
        self.assertEqual(data['retryAfter'], limits.RATE_WINDOW - 100)

    def test_rolling_expiry_at_exact_boundary(self):
        with patch.object(limits.time, 'monotonic', return_value=100): limits.rates.take('client', 'analysis', limits.RATE_LIMIT_MAX)
        with patch.object(limits.time, 'monotonic', return_value=200): limits.rates.take('client', 'analysis', limits.RATE_LIMIT_MAX)
        with patch.object(limits.time, 'monotonic', return_value=100 + limits.RATE_WINDOW):
            self.assertEqual(limits.rates.snapshot('client')['analysis']['remaining'], limits.RATE_LIMIT_MAX - 1)
        with patch.object(limits.time, 'monotonic', return_value=200 + limits.RATE_WINDOW):
            self.assertEqual(limits.rates.snapshot('client')['analysis']['remaining'], limits.RATE_LIMIT_MAX)

    def test_endpoint_is_uncached_and_does_not_spend_allowance(self):
        for _ in range(3):
            status, data, headers = asyncio.run(call('/api/allowance', method='GET'))
            self.assertEqual(status, 200)
            self.assertEqual(data['windowSeconds'], limits.RATE_WINDOW)
            self.assertEqual(headers[b'cache-control'], b'no-store')
            self.assertNotIn('client', data)
        self.assertEqual(len(limits.rates.clients), 0)

    def test_endpoint_uses_same_trusted_identity_as_enforcement(self):
        limits.rates.take('198.51.100.7', 'analysis', limits.RATE_LIMIT_MAX)
        with patch.dict('os.environ', {'TRUSTED_PROXY_IPS': '127.0.0.1'}):
            _, first, _ = asyncio.run(call('/api/allowance', method='GET', headers=[(b'x-forwarded-for', b'198.51.100.7')]))
            _, second, _ = asyncio.run(call('/api/allowance', method='GET', headers=[(b'x-forwarded-for', b'198.51.100.8')]))
        self.assertEqual(first['allowances']['analysis']['remaining'], limits.RATE_LIMIT_MAX - 1)
        self.assertEqual(second['allowances']['analysis']['remaining'], limits.RATE_LIMIT_MAX)
        with patch.dict('os.environ', {'TRUSTED_PROXY_IPS': ''}):
            _, untrusted, _ = asyncio.run(call('/api/allowance', method='GET', headers=[(b'x-forwarded-for', b'198.51.100.7')]))
        self.assertEqual(untrusted['allowances']['analysis']['remaining'], limits.RATE_LIMIT_MAX)

    def test_full_identity_store_still_allows_read_only_snapshots(self):
        with patch.object(limits, 'MAX_RATE_CLIENTS', 1):
            limits.rates.take('client', 'upload', limits.UPLOAD_RATE_MAX)
            self.assertEqual(limits.rates.snapshot('another')['upload']['remaining'], limits.UPLOAD_RATE_MAX)
            with self.assertRaises(limits.WorkloadError): limits.rates.take('another', 'upload', limits.UPLOAD_RATE_MAX)
        self.assertEqual(len(limits.rates.clients), 1)


if __name__ == '__main__': unittest.main()
