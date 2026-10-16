"""Bound admission, request bodies and PDF jobs for a single worker process."""
import asyncio
import contextvars
import ipaddress
import math
import os
import subprocess
import shutil
import threading
from pathlib import Path
import time
from collections import OrderedDict
from starlette.responses import JSONResponse


def positive_setting(name, default):
    value = int(os.environ.get(name, str(default)))
    if value < 1:
        raise ValueError(f'{name} must be positive')
    return value


MAX_UPLOAD_MB = positive_setting('MAX_UPLOAD_MB', 50)
MAX_UPLOAD_BYTES = MAX_UPLOAD_MB * 1024 * 1024
UPLOAD_ENVELOPE_BYTES = 64 * 1024
MAX_DOCUMENT_PAGES = positive_setting('MAX_DOCUMENT_PAGES', 100)
MAX_PREVIEW_PIXELS = positive_setting('MAX_PREVIEW_PIXELS', 20_000_000)
MAX_ANALYSIS_PIXELS = positive_setting('MAX_ANALYSIS_PIXELS', 5_000_000)
MAX_RASTER_SIDE = positive_setting('MAX_RASTER_SIDE', 10_000)
MAX_OUTPUT_BYTES = positive_setting('MAX_OUTPUT_BYTES', 64 * 1024 * 1024)
MAX_JOB_SECONDS = positive_setting('MAX_JOB_SECONDS', 150)
MAX_UPLOAD_SECONDS = positive_setting('MAX_UPLOAD_SECONDS', 120)
MAX_CONCURRENT_UPLOADS = positive_setting('MAX_CONCURRENT_UPLOADS', 2)
MAX_CONCURRENT_PROCESSING = positive_setting('MAX_CONCURRENT_PROCESSING', 1)
UPLOAD_RATE_MAX = positive_setting('UPLOAD_RATE_MAX', 10)
PREVIEW_RATE_MAX = positive_setting('PREVIEW_RATE_MAX', 120)
RATE_LIMIT_MAX = positive_setting('RATE_LIMIT_MAX', 30)
RATE_WINDOW = 3600
BUSY_RETRY_SECONDS = 5
MAX_RATE_CLIENTS = positive_setting('MAX_RATE_CLIENTS', 10_000)
MAX_STORED_DOCUMENTS = positive_setting('MAX_STORED_DOCUMENTS', 100)
MAX_STORED_UPLOAD_BYTES = positive_setting('MAX_STORED_UPLOAD_BYTES', 512 * 1024 * 1024)
MAX_JSON_BYTES = 16 * 1024
MAX_DIAGNOSTIC_BYTES = 256 * 1024
MAX_JOB_DISK_BYTES = positive_setting('MAX_JOB_DISK_BYTES', 512 * 1024 * 1024)
DEADLINE = contextvars.ContextVar('pdf_job_deadline', default=None)
WORK_DIR = contextvars.ContextVar('pdf_work_directory', default=None)


class WorkloadError(Exception):
    def __init__(self, code, status=413, *, retry_after=None, **parameters):
        self.code, self.status = code, status
        self.retry_after, self.parameters = retry_after, parameters
        super().__init__(code)

    def response(self):
        messages = {
            'upload_too_large': f'PDF exceeds the {MAX_UPLOAD_MB} MB upload limit.',
            'request_too_large': 'Request body is too large.',
            'upload_timeout': 'Upload took too long. Please try again.',
            'server_busy': 'The server is busy. Please try again shortly.',
            'rate_limited': 'Request allowance reached. Please wait before trying again.',
            'page_limit': f'PDF exceeds the {MAX_DOCUMENT_PAGES}-page limit.',
            'raster_limit': 'This page is too large to process at the current resolution. Use a smaller page.',
            'job_timeout': 'Processing took too long. Try a simpler PDF.',
            'job_limit': 'This PDF requires too much processing. Try a simpler PDF.',
        }
        headers = {'Retry-After': str(self.retry_after)} if self.retry_after else {}
        return JSONResponse({'code': self.code, 'detail': messages[self.code], **self.parameters},
                            status_code=self.status, headers=headers)


class RateLimiter:
    def __init__(self):
        self.clients = OrderedDict()

    def snapshot(self, client):
        """Read live allowances without allocating or extending client state."""
        now = time.monotonic()
        record = self.clients.get(client)
        result = {}
        for kind, limit in (('upload', UPLOAD_RATE_MAX), ('preview', PREVIEW_RATE_MAX), ('analysis', RATE_LIMIT_MAX)):
            bucket = record['buckets'].get(kind, []) if record else []
            live = [stamp for stamp in bucket if stamp > now - RATE_WINDOW]
            remaining = max(0, limit - len(live))
            result[kind] = {'limit': limit, 'remaining': remaining,
                            'retryAfter': max(1, math.ceil(live[0] + RATE_WINDOW - now)) if live and not remaining else 0}
        return result

    def take(self, client, kind, limit):
        now = time.monotonic()
        # Expired oldest records are reclaimed. If all records are live, reject
        # new identities rather than evicting live quotas and enabling bypass.
        while self.clients and next(iter(self.clients.values()))['last'] <= now - RATE_WINDOW:
            self.clients.popitem(last=False)
        record = self.clients.get(client)
        if record is None:
            if len(self.clients) >= MAX_RATE_CLIENTS:
                raise WorkloadError('server_busy', 503, retry_after=BUSY_RETRY_SECONDS)
            record = self.clients[client] = {'last': now, 'buckets': {}}
        record['last'] = now
        self.clients.move_to_end(client)
        bucket = [stamp for stamp in record['buckets'].get(kind, []) if stamp > now - RATE_WINDOW]
        record['buckets'][kind] = bucket
        if len(bucket) >= limit:
            seconds = max(1, math.ceil(bucket[0] + RATE_WINDOW - now))
            raise WorkloadError('rate_limited', 429, retry_after=seconds,
                                operation=kind, limit=limit, retryAfter=seconds)
        bucket.append(now)


rates = RateLimiter()


def client_identity(scope):
    # The app ignores forwarded headers. Uvicorn proxy processing is disabled
    # by our launchers; only explicitly configured immediate peers are trusted.
    peer = (scope.get('client') or ('unknown', 0))[0]
    trusted = [ipaddress.ip_network(item.strip(), strict=False)
               for item in os.environ.get('TRUSTED_PROXY_IPS', '').split(',') if item.strip()]
    if any(network.prefixlen == 0 for network in trusted):
        return str(peer)  # Never accept a trust-all network.
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return 'unknown'
    if not any(peer_ip in network for network in trusted):
        return str(peer_ip)
    values = [value for key, value in scope.get('headers', []) if key.lower() == b'x-forwarded-for']
    if len(values) != 1 or len(values[0]) > 512:
        return str(peer_ip)
    try:
        chain = [ipaddress.ip_address(item.strip()) for item in values[0].decode('ascii').split(',')]
    except (ValueError, UnicodeError):
        return str(peer_ip)
    for address in reversed(chain):
        if not any(address in network for network in trusted):
            return str(address)
    return str(peer_ip)


def check_deadline():
    deadline = DEADLINE.get()
    if deadline is not None and time.monotonic() >= deadline:
        raise WorkloadError('job_timeout', 422)


def remaining_timeout(default):
    check_deadline()
    deadline = DEADLINE.get()
    return min(default, max(0.001, deadline - time.monotonic())) if deadline else default


def execute_processor(command, timeout):
    """Bound diagnostic capture as well as execution time; never spool logs."""
    directory = WORK_DIR.get()
    environment = None
    if directory is not None:
        environment = os.environ.copy()
        environment.update({name: str(directory) for name in ('TMPDIR', 'TEMP', 'TMP')})
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,
                               env=environment,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    def collect(stream, buffer):
        while chunk := stream.read(8192):
            remaining = MAX_DIAGNOSTIC_BYTES - len(buffer)
            buffer.extend(chunk[:remaining])
            if len(chunk) > remaining:
                overflow.set()
                try: process.kill()
                except ProcessLookupError: pass
    threads = [threading.Thread(target=collect, args=(stream, buffer), daemon=True)
               for stream, buffer in zip((process.stdout, process.stderr), buffers)]
    for thread in threads: thread.start()
    try:
        try:
            stop = time.monotonic() + timeout
            while process.poll() is None:
                if directory is not None:
                    # Include Ghostscript banding scratch files, not just final
                    # PNG/TIFF output. Polling can briefly overshoot this budget.
                    total = working_bytes(directory)
                    if total > MAX_JOB_DISK_BYTES:
                        raise WorkloadError('job_limit', 422)
                remaining = stop - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try: process.wait(timeout=min(0.05, remaining))
                except subprocess.TimeoutExpired: pass
            if directory is not None and working_bytes(directory) > MAX_JOB_DISK_BYTES:
                raise WorkloadError('job_limit', 422)
        except BaseException:
            process.kill()
            process.wait()
            raise
    finally:
        for thread in threads: thread.join()
        process.stdout.close()
        process.stderr.close()
    if overflow.is_set():
        raise WorkloadError('job_limit', 422)
    return subprocess.CompletedProcess(command, process.returncode,
            buffers[0].decode('utf-8', errors='replace'), buffers[1].decode('utf-8', errors='replace'))


def working_bytes(directory):
    total = 0
    with os.scandir(directory) as entries:
        for entry in entries:
            try:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except FileNotFoundError:
                pass  # Ghostscript can remove its banding files between polls.
    return total


def check_disk_space(directory, extra=0):
    path = Path(directory)
    while not path.exists(): path = path.parent
    if shutil.disk_usage(path).free < MAX_JOB_DISK_BYTES + extra:
        raise WorkloadError('server_busy', 503, retry_after=60)


class JobRunner:
    def __init__(self):
        self.active = 0

    async def run(self, function, *args, on_finished=None):
        if self.active >= MAX_CONCURRENT_PROCESSING:
            if on_finished is not None: on_finished()
            raise WorkloadError('server_busy', 503, retry_after=BUSY_RETRY_SECONDS)
        self.active += 1
        context = contextvars.copy_context()
        context.run(DEADLINE.set, time.monotonic() + MAX_JOB_SECONDS)
        try:
            future = asyncio.get_running_loop().run_in_executor(None, context.run, function, *args)
        except BaseException:
            self.active -= 1
            if on_finished is not None: on_finished()
            raise
        def finished(done):
            self.active -= 1
            if on_finished is not None: on_finished()
            # Retrieve errors even if the HTTP task was cancelled/disconnected.
            if not done.cancelled(): done.exception()
        future.add_done_callback(finished)
        # A cancelled HTTP request must not release a slot while its thread and
        # Ghostscript process still run. No pending-job queue is maintained.
        return await asyncio.shield(future)


jobs = JobRunner()


def check_page_count(count):
    if count > MAX_DOCUMENT_PAGES:
        raise WorkloadError('page_limit', pageLimit=MAX_DOCUMENT_PAGES)


def check_raster(width, height, dpi, operation, scale=1):
    limit = MAX_PREVIEW_PIXELS if operation == 'preview' else MAX_ANALYSIS_PIXELS
    x, y = width * scale * dpi / 72, height * scale * dpi / 72
    if (not math.isfinite(x) or not math.isfinite(y) or x <= 0 or y <= 0
            or max(x, y) > MAX_RASTER_SIDE or math.ceil(x) * math.ceil(y) > limit):
        raise WorkloadError('raster_limit', operation=operation, maxPixels=limit)


class RequestBounds:
    """Guard the incoming ASGI stream before form/JSON parsing or spooling."""
    def __init__(self, app, upload_capacity=None):
        self.app = app
        self.uploads = 0
        self.upload_capacity = upload_capacity

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope.get('method') != 'POST' or scope['path'] not in (
                '/api/upload', '/api/preview', '/api/analyze', '/api/ink-limit/channels', '/api/ink-limit', '/api/channel-preview'):
            return await self.app(scope, receive, send)
        upload = scope['path'] == '/api/upload'
        admitted = False
        started = False
        failure = None
        async def tracked_send(message):
            nonlocal started
            # Framework body parsing can translate stream exceptions to 400.
            # Preserve our precise size/timeout response at the outer boundary.
            if failure is not None: return
            if message['type'] == 'http.response.start': started = True
            await send(message)
        try:
            cap = MAX_UPLOAD_BYTES + UPLOAD_ENVELOPE_BYTES if upload else MAX_JSON_BYTES
            lengths = [value for key, value in scope['headers'] if key.lower() == b'content-length']
            if lengths:
                if len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isdigit():
                    await JSONResponse({'detail':'Invalid Content-Length'}, status_code=400)(scope, receive, send)
                    return
                if int(lengths[0]) > cap:
                    raise WorkloadError('upload_too_large' if upload else 'request_too_large', maxMb=MAX_UPLOAD_MB)
            if upload:
                rates.take(client_identity(scope), 'upload', UPLOAD_RATE_MAX)
                if self.uploads >= MAX_CONCURRENT_UPLOADS:
                    raise WorkloadError('server_busy', 503, retry_after=BUSY_RETRY_SECONDS)
                if self.upload_capacity is not None:
                    self.upload_capacity(self.uploads + 1)
                self.uploads += 1
                admitted = True
            read_bytes = 0
            deadline = time.monotonic() + (MAX_UPLOAD_SECONDS if upload else 15)
            async def bounded_receive():
                nonlocal read_bytes, failure
                if time.monotonic() >= deadline:
                    failure = WorkloadError('upload_timeout', 408)
                    raise failure
                try:
                    message = await asyncio.wait_for(receive(), max(0.001, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    failure = WorkloadError('upload_timeout', 408)
                    raise failure from None
                if message['type'] == 'http.request':
                    read_bytes += len(message.get('body', b''))
                    if read_bytes > cap:
                        failure = WorkloadError('upload_too_large' if upload else 'request_too_large', maxMb=MAX_UPLOAD_MB)
                        raise failure
                return message
            await self.app(scope, bounded_receive, tracked_send)
            if failure is not None: raise failure
        except WorkloadError as exc:
            if started: raise
            await exc.response()(scope, receive, send)
        finally:
            if admitted: self.uploads -= 1
