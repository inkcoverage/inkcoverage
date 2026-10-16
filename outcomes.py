"""Aggregate service outcomes only; never inspect bodies, clients or queries."""
import logging
from collections import Counter
from starlette.datastructures import MutableHeaders

log = logging.getLogger('uvicorn.error.outcomes')
OPERATIONS = {'/api/upload': 'upload', '/api/preview': 'preview', '/api/analyze': 'analysis',
              '/api/ink-limit/channels': 'preview', '/api/ink-limit': 'analysis', '/api/channel-preview': 'preview'}


class ServiceOutcomes:
    def __init__(self):
        # Fixed keys: no document identifiers, timestamps or visitor records.
        self.counts = Counter({(operation, outcome): 0 for operation in OPERATIONS.values()
                               for outcome in ('completed', 'rejected', 'failed', 'interrupted')})

    def record(self, operation, outcome):
        if (operation, outcome) in self.counts:
            self.counts[operation, outcome] += 1

    def summary(self):
        return {operation: {outcome: self.counts[operation, outcome]
                            for outcome in ('completed', 'rejected', 'failed', 'interrupted')}
                for operation in OPERATIONS.values()}

    def report(self):
        log.info('Aggregate service outcomes: %s', self.summary())


class OutcomeMiddleware:
    def __init__(self, app, counters):
        self.app, self.counters = app, counters

    async def __call__(self, scope, receive, send):
        operation = OPERATIONS.get(scope.get('path'))
        if scope['type'] != 'http' or scope.get('method') != 'POST' or not operation:
            return await self.app(scope, receive, send)
        outcome = 'interrupted'
        completed = False
        failed = False
        async def tracked_send(message):
            nonlocal outcome, completed
            if message['type'] == 'http.response.start':
                status = message['status']
                outcome = 'completed' if 200 <= status < 300 else 'rejected' if 400 <= status < 500 else 'failed'
            await send(message)
            if message['type'] == 'http.response.body' and not message.get('more_body', False):
                completed = True
        try:
            await self.app(scope, receive, tracked_send)
        except Exception:
            failed = True
            raise
        finally:
            self.counters.record(operation, 'failed' if failed else outcome if completed else 'interrupted')


class PrivacyHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http': return await self.app(scope, receive, send)
        async def private_send(message):
            if message['type'] == 'http.response.start':
                headers = MutableHeaders(scope=message)
                headers['Referrer-Policy'] = 'no-referrer'
                if scope.get('path', '').startswith('/api/'):
                    headers['Cache-Control'] = 'no-store'
            await send(message)
        await self.app(scope, receive, private_send)
