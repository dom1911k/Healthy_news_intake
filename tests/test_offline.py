"""Offline end-to-end test: fake sources + fake Claude. Run: python -m unittest"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from newsdigest import pipeline
from newsdigest.config import load_config, load_interests
from newsdigest.db import DB
from newsdigest.deliver import render_html, render_text
from newsdigest.llm import LLM
from newsdigest.models import Comment, Item
from newsdigest.schedule import is_due
from newsdigest.sources.base import Source

NOW = datetime(2026, 10, 1, 5, 30, tzinfo=timezone.utc)


def item(title, replies, kind="gaming", source_id="resetera"):
    return Item(source="ResetEra · Gaming Forum" if kind == "gaming" else source_id, title=title,
                url=f"https://example.com/{title.replace(' ', '-')}/", author="a", created_at=NOW - timedelta(hours=3),
                score=None, reply_count=replies, body_excerpt=f"Body of {title}", kind=kind, source_id=source_id)


class FakeSource(Source):
    def __init__(self, sid, items, min_replies=50):
        super().__init__({}, None)
        self.id, self._items, self.min_replies = sid, items, min_replies

    def fetch(self, since):
        return list(self._items)

    def passes_engagement(self, i):
        return i.kind == "good_news" or (i.reply_count or 0) >= self.min_replies

    def enrich(self, i):
        i.top_comments = [Comment("Looks good", 40), Comment("Not convinced", 12)]


class FakeLLM(LLM):
    def __init__(self):
        self.batch_size = 25
        self.scores = {"Persona 6 announced": 9, "Console war again": 1, "Persona 6 dated for 2027": 8,
                       "Studio X lays off 200": 7, "Small thread": 9,
                       "Malaria vaccine cuts deaths 30%": 9, "Dog rescued from well": 2, "GNN fluffy story": 7}

    def _haiku(self, system, user, schema, max_tokens=4000):
        import json
        rows = [json.loads(l) for l in user.splitlines() if l.startswith("{")]
        if "groups" in schema["properties"]:
            ids = [r["id"] for r in rows if "Persona 6" in r["title"]]
            return {"groups": [ids] if len(ids) > 1 else []}
        return {"scores": [{"id": r["id"], "score": self.scores.get(r["title"], 0), "reason": "test"} for r in rows]}

    def _sonnet(self, system, user, schema):
        if "evidence_strength" in schema["properties"]:
            return {"headline": "GN headline <b>", "what_happened": "x.", "why_it_matters": "y.",
                    "evidence_strength": "solid data"}
        return {"headline": "H: " + user.split("<title>")[1].split("</title>")[0], "what_happened": "w.",
                "community_consensus": "c.", "dissent": ""}


class OfflineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DB(Path(self.tmp.name) / "t.sqlite3")
        self.cfg = load_config()
        self.interests = load_interests()
        gaming = [item("Persona 6 announced", 900), item("Console war again", 2000),
                  item("Persona 6 dated for 2027", 120), item("Studio X lays off 200", 300), item("Small thread", 10)]
        good = [item("Malaria vaccine cuts deaths 30%", None, "good_news", "positive_news"),
                item("Dog rescued from well", None, "good_news", "positive_news"),
                item("GNN fluffy story", None, "good_news", "good_news_network")]
        self.patches = [
            mock.patch.object(pipeline, "build_gaming_sources", lambda c, f: [FakeSource("resetera", gaming)]),
            mock.patch.object(pipeline, "build_good_news_sources",
                              lambda c, f: [FakeSource("positive_news", good[:2]), FakeSource("good_news_network", good[2:])]),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_end_to_end(self):
        d, to_mark = pipeline.build_digest(self.cfg, self.interests, self.db, None, FakeLLM(), NOW)
        titles = [s.primary.title for s in d.stories]
        self.assertEqual(titles, ["Persona 6 announced", "Studio X lays off 200"])  # clustered, filtered, ranked
        self.assertEqual(len(d.stories[0].items), 2)
        self.assertNotIn("Small thread", [i.title for i in to_mark])  # below engagement: not marked seen
        self.assertEqual([s.primary.title for s in d.good_news], ["Malaria vaccine cuts deaths 30%"])  # GNN needs 8
        html = render_html(d, self.cfg)
        self.assertIn("Good news", html)
        self.assertIn("&lt;b&gt;", html)  # escaped
        self.assertIn("That's everything", render_text(d, self.cfg))

        # second run: everything seen -> empty digest, good news not due
        self.db.mark_seen(to_mark)
        self.db.record_digest(len(d.stories), len(d.good_news), "file")
        d2, _ = pipeline.build_digest(self.cfg, self.interests, self.db, None, FakeLLM(), NOW + timedelta(days=1))
        self.assertEqual(d2.stories, [])
        self.assertIsNone(d2.good_news)
        self.assertIn("Nothing notable today", render_html(d2, self.cfg))
        self.assertNotIn("Good news", render_html(d2, self.cfg))

    def test_schedule(self):
        cfg = {"schedule": {"timezone": "Europe/Berlin", "time": "07:00", "days": ["mon", "wed", "fri"]}}
        wed_0500_utc = datetime(2026, 10, 7, 5, 7, tzinfo=timezone.utc)  # 07:07 CEST
        self.assertTrue(is_due(cfg, wed_0500_utc, None)[0])
        self.assertFalse(is_due(cfg, wed_0500_utc, wed_0500_utc - timedelta(minutes=5))[0])
        self.assertFalse(is_due(cfg, datetime(2026, 10, 8, 6, 7, tzinfo=timezone.utc), None)[0])  # thursday
        winter = datetime(2026, 12, 2, 5, 7, tzinfo=timezone.utc)  # wed 06:07 CET -> too early
        self.assertFalse(is_due(cfg, winter, None)[0])
        self.assertTrue(is_due(cfg, winter + timedelta(hours=1), None)[0])


if __name__ == "__main__":
    unittest.main()
