"""Observed privacy: bounded anonymous outcomes, no tracker and private headers."""
import asyncio
import json
import re
import unittest
from pathlib import Path
from unittest.mock import patch
import app as backend
from outcomes import OutcomeMiddleware, ServiceOutcomes
from test_session5 import call, JSON

ROOT=Path(__file__).resolve().parents[1]


class OutcomeTests(unittest.TestCase):
    def test_counts_have_fixed_keys_and_ignore_arbitrary_inputs(self):
        counters=ServiceOutcomes()
        for n in range(1000):
            counters.record('private-document-'+str(n),'filename.pdf')
            counters.record('analysis','completed')
        self.assertEqual(len(counters.counts),12)
        self.assertEqual(counters.summary()['analysis']['completed'],1000)
        self.assertNotIn('private-document',json.dumps(counters.summary()))

    def test_logs_only_aggregate_counts(self):
        counters=ServiceOutcomes(); counters.record('upload','completed')
        with self.assertLogs('uvicorn.error.outcomes','INFO') as logs: counters.report()
        self.assertIn("'upload'",logs.output[0])
        self.assertIn("'completed': 1",logs.output[0])

    def test_statuses_are_counted_once_without_reading_request_details(self):
        async def scenario(status):
            counters=ServiceOutcomes()
            async def inner(scope,receive,send):
                await send({'type':'http.response.start','status':status,'headers':[]})
                await send({'type':'http.response.body','body':b'{"Cyan":74.3}','more_body':False})
            async def unread(): self.fail('Outcome collection read the PDF/body')
            async def send(message): pass
            scope={'type':'http','method':'POST','path':'/api/analyze','query_string':b'private.pdf',
                   'client':('198.51.100.77',1),'headers':[(b'user-agent',b'private-browser')]}
            await OutcomeMiddleware(inner,counters)(scope,unread,send)
            return counters.summary()
        for status,outcome in [(200,'completed'),(400,'rejected'),(410,'rejected'),(429,'rejected'),(500,'failed'),(503,'failed')]:
            summary=asyncio.run(scenario(status))
            self.assertEqual(summary['analysis'][outcome],1)
            self.assertEqual(sum(sum(values.values()) for values in summary.values()),1)
            serialized=json.dumps(summary)
            for private in ['74.3','Cyan','198.51.100.77','private','page','id']:
                self.assertNotIn(private,serialized)

    def test_cancelled_and_failed_requests_have_truthful_outcomes(self):
        async def scenario(error):
            counters=ServiceOutcomes()
            async def inner(*args): raise error
            with self.assertRaises(type(error)):
                await OutcomeMiddleware(inner,counters)({'type':'http','method':'POST','path':'/api/upload'},None,None)
            return counters.summary()['upload']
        self.assertEqual(asyncio.run(scenario(asyncio.CancelledError()))['interrupted'],1)
        self.assertEqual(asyncio.run(scenario(RuntimeError('private filename')))['failed'],1)

    def test_unknown_routes_and_gets_are_not_tracked(self):
        async def scenario():
            counters=ServiceOutcomes()
            async def inner(*args): pass
            middleware=OutcomeMiddleware(inner,counters)
            for scope in [{'type':'http','method':'GET','path':'/api/analyze'}, {'type':'http','method':'POST','path':'/api/unknown'}, {'type':'lifespan'}]:
                await middleware(scope,None,None)
            return counters.summary()
        self.assertEqual(sum(sum(values.values()) for values in asyncio.run(scenario()).values()),0)

    def test_real_rejected_boundary_is_counted_and_private(self):
        previous=backend.outcomes.summary()['analysis']['rejected']
        status,data,headers=asyncio.run(call('/api/analyze',b'{"id":"private.pdf","page":1}',JSON))
        self.assertEqual(status,422)
        self.assertNotIn('private.pdf',json.dumps(data))
        self.assertEqual(backend.outcomes.summary()['analysis']['rejected'],previous+1)
        self.assertEqual(headers[b'cache-control'],b'no-store')
        self.assertEqual(headers[b'referrer-policy'],b'no-referrer')
        self.assertNotIn(b'set-cookie',headers)

    def test_all_public_routes_have_local_fonts_and_no_external_trackers(self):
        for kind in ['index','about','faq','contact','privacy']:
            for suffix in ['','-es','-pt','-zh'] if kind!='index' else ['']:
                text=(ROOT/'static'/f'{kind}{suffix}.html').read_text(encoding='utf-8')
                for forbidden in ['gtag(', 'gtag?.(', 'googletagmanager', 'fonts.googleapis.com', 'fonts.gstatic.com']:
                    self.assertNotIn(forbidden,text)
                self.assertIn('/static/app-fonts.css',text)

    def test_translated_privacy_routes_have_matching_language_and_no_cookies(self):
        async def read(route):
            sent=[]
            async def receive(): return {'type':'http.request','body':b'','more_body':False}
            async def send(message): sent.append(message)
            scope=dict(type='http',asgi={'version':'3.0'},http_version='1.1',method='GET',scheme='http',path=route,
                       raw_path=route.encode(),query_string=b'',root_path='',headers=[],client=('127.0.0.1',1),server=('127.0.0.1',8001))
            await backend.app(scope,receive,send)
            start=next(message for message in sent if message['type']=='http.response.start')
            body=b''.join(message.get('body',b'') for message in sent if message['type']=='http.response.body').decode()
            return start,body
        for lang in ['en','es','pt','zh']:
            prefix='' if lang=='en' else '/'+lang
            start,body=asyncio.run(read(prefix+'/privacy'))
            self.assertEqual(start['status'],200)
            self.assertIn(f'<html lang="{"zh-Hans" if lang=="zh" else lang}">',body)
            self.assertIn('Web3Forms',body); self.assertIn('localStorage',body); self.assertIn('60',body)
            self.assertEqual(dict(start['headers'])[b'referrer-policy'],b'no-referrer')
            self.assertNotIn(b'set-cookie',dict(start['headers']))

    def test_contact_disclosure_and_submission_remain_deliberate(self):
        for suffix in ['','-es','-pt','-zh']:
            text=(ROOT/'static'/f'contact{suffix}.html').read_text(encoding='utf-8')
            self.assertIn('aria-describedby="contactPrivacy"',text)
            self.assertIn('Web3Forms',re.search(r'id="contactPrivacy">(.*?)</p>',text,re.S)[1])
            self.assertIn("credentials: 'omit'",text)
            self.assertIn("referrerPolicy: 'no-referrer'",text)
            self.assertNotIn('type="file"',text)


if __name__=='__main__':unittest.main()
