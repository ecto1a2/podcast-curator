"""Offline check of the exploration step. Fakes Claude, Apple search and feeds.
Run from a scratch copy of the repo: CURATOR_NOW=... python tests/sim_explore.py"""
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["ANTHROPIC_API_KEY"] = "fake"
os.environ.pop("GITHUB_TOKEN", None)
os.environ.pop("GITHUB_ACTIONS", None)
import curator as c  # noqa: E402

day = c.NOW.strftime("%m%d")


def fake_claude(system, user, max_tokens=4000, tools=None):
    if "widen the search net" in system:
        return json.dumps({"add": [
            {"term": "Joe List", "kind": "person", "tier": 2, "reason": "Normand's co-host"},
            {"term": "shadow fleet sanctions", "kind": "topic", "tier": 3, "reason": "Next to drug trade"},
            {"term": "Ballet Today", "kind": "show", "tier": 3, "reason": "bad idea test"}],
            "keep": [], "drop": []})
    items = json.loads(user.split("\n", 1)[1])
    out = []
    for i in items:
        t = i["title"].lower()
        score = 30 if "ballet" in t else 85 if i["source"] == "explore" else 80
        out.append({"id": i["id"], "score": score, "category": "ai" if "ai" in t else "comedy" if "list" in t
                    else "geopolitics" if "fleet" in t else "business", "filler": False, "reason": "test"})
    return json.dumps(out)


def fake_search(term, source, limit=15):
    eps = []
    for k in range(2):
        title = f"{term} episode {day}-{k}"
        eps.append({"id": c.ep_id(f"{term} Show", title), "show": f"{term} Show {day}{k}", "title": title,
                    "audio_url": f"https://example.com/{abs(hash(title))}.mp3", "audio_type": "audio/mpeg",
                    "published": (c.NOW - timedelta(days=1)).isoformat(), "minutes": 50 + k * 40,
                    "description": term, "link": "", "guid": title, "image": None, "episode_type": "",
                    "source": source})
    return eps


c.claude = fake_claude
c.search_episodes = fake_search
c.resolve_show_feed = lambda name, cache: None
c.fetch_feed = lambda url, label, source, max_items=None: []
c.OFFLINE = False
c.SETTINGS["search_rotation_days"] = 1
c.SETTINGS["buzz_scan_weekdays"] = []
c.main()
