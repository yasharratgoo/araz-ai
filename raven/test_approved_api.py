"""Offline guardrail tests for the official OAuth read adapter."""
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from approved_api import (APIConfig, AccessNotApproved, APIBlocked, APIRateLimited,
                          approval_status, from_environment, approved_url, get_comments)

class AuthorizedAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=Path(self.tmp.name)/"approval.json"
        self.env={
            "RAVEN_REDDIT_APP_ID":"test_registered_app",
            "RAVEN_REDDIT_OAUTH_TOKEN":"TEST_TOKEN_DO_NOT_USE",
            "RAVEN_REDDIT_USER_AGENT":"windows:registered.raven.reader:v0.4 (by /u/Historical-Wheel-749)"
        }
        self.config=APIConfig("test_registered_app","TEST_TOKEN_DO_NOT_USE",
                              self.env["RAVEN_REDDIT_USER_AGENT"],True)
    def tearDown(self):
        self.tmp.cleanup()
    def approval(self):
        self.path.write_text(json.dumps({
            "reddit_explicit_approval":True,
            "owner_approved_read_only":True,
            "use_case":"nexa-volunteer-beta-research",
            "reddit_approval_reference":"test-reference-only"
        }),encoding="utf-8")
    def test_default_is_fail_closed_20(self):
        self.assertFalse(approval_status(self.path))
        for i in range(20):
            with self.subTest(i=i):
                env=dict(self.env)
                env["RAVEN_REDDIT_APP_ID"]="test_"+str(i)
                with self.assertRaises(AccessNotApproved):from_environment(env,self.path)
    def test_refuse_approvals_without_required_evidence_40(self):
        reference={
            "reddit_explicit_approval":True,"owner_approved_read_only":True,
            "use_case":"nexa-volunteer-beta-research",
            "reddit_approval_reference":"registered"
        }
        for i in range(40):
            bad=dict(reference)
            key=list(reference)[i%4]
            bad[key]=None if i%2 else False
            self.path.write_text(json.dumps(bad),encoding="utf-8")
            with self.subTest(i=i):
                self.assertFalse(approval_status(self.path))
    def test_missing_credentials_after_approval(self):
        self.approval()
        self.assertTrue(approval_status(self.path))
        self.assertEqual(from_environment(self.env,self.path).app_id,"test_registered_app")
        for key in self.env:
            env=dict(self.env);env.pop(key)
            with self.subTest(key=key):
                with self.assertRaises(AccessNotApproved):from_environment(env,self.path)
    def test_invalid_subs_60(self):
        bads=["betaReaders","popular","all","betareadersforai/../../api/submit",
              "personalfinance","redditdev","", "u/historical-wheel-749"]
        for i in range(60):
            sub=bads[i%len(bads)]
            with self.subTest(i=i):
                with self.assertRaises(ValueError):approved_url(sub,"abc123")
    def test_endpoint_request_is_get_only(self):
        calls=[]
        def fake(req):
            calls.append(req)
            return 200,b'[{"kind":"Listing","data":{"children":[]}}]'
        result=get_comments(self.config,subreddit="BetaReadersForAI",post_id="1w5qyzk",transport=fake)
        self.assertIsInstance(result,list)
        self.assertEqual(len(calls),1)
        req=calls[0]
        self.assertEqual(req.get_method(),"GET")
        self.assertIn("oauth.reddit.com/r/betareadersforai/comments/1w5qyzk",req.full_url)
        self.assertNotIn("/api/submit",req.full_url)
        self.assertEqual(req.get_header("Authorization"),"bearer TEST_TOKEN_DO_NOT_USE")
        self.assertNotIn("TEST_TOKEN_DO_NOT_USE",repr(self.config))
    def test_denied_and_rate_limited_do_not_retry_40(self):
        codes=[401,403,429,500]
        for i in range(40):
            status=codes[i%len(codes)];calls=[]
            def fake(req):
                calls.append(1)
                return status,b'{"message":"you are blocked"}'
            with self.subTest(i=i):
                with self.assertRaises((APIBlocked,APIRateLimited,RuntimeError)):
                    get_comments(self.config,subreddit="betareadersforai",post_id="abc123",transport=fake)
                self.assertEqual(len(calls),1)
    def test_oversized_nonjson_20(self):
        vals=[b"x"*700001,b"not-json",b"[] trailing"]
        for i in range(20):
            raw=vals[i%len(vals)]
            with self.subTest(i=i):
                with self.assertRaises(ValueError):
                    get_comments(self.config,subreddit="betareadersforai",
                                 post_id="abc123",transport=lambda req:(200,raw))
    def test_bad_url_ids_50(self):
        vals=["","../../api/submit","abc?limit=1000","a"*20," abc123","ab","abc/def"]
        for i in range(50):
            with self.subTest(i=i):
                with self.assertRaises(ValueError):approved_url("betareadersforai",vals[i%len(vals)])
    def test_direct_network_call_unapproved_is_blocked(self):
        with self.assertRaises(AccessNotApproved):
            get_comments(self.config,subreddit="betareadersforai",post_id="abc123")
    def test_not_a_real_token_or_approval(self):
        self.assertFalse(approval_status(self.path))
        self.assertTrue(self.config.access_token.startswith("TEST_"))
        self.assertRaises(AccessNotApproved,from_environment,{},self.path)

if __name__=="__main__":
    unittest.main()
