#!/usr/bin/env python3
"""Daily podcast curator.

Gathers candidate episodes, scores them with Claude against config/profile.md,
keeps a small rotating set of picks, and publishes them as a podcast feed at
docs/feed.xml. Uses only the Python standard library.

Environment:
  ANTHROPIC_API_KEY   required for real runs (absent => dry run with keyword scoring)
  GITHUB_TOKEN        lets the weekly check-in open and read GitHub issues
  GITHUB_REPOSITORY   "owner/repo", set automatically by GitHub Actions
  FORCE_CHECKIN=1     run the weekly check-in today
  FORCE_BUZZ=1        run the buzz scan today
  OFFLINE=1           skip all network lookups except feeds listed in sources (for tests)
"""

import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CFG, STATE, DOCS = ROOT / "config", ROOT / "state", ROOT / "docs"
NS_IT = "http://www.itunes.com/dtds/podcast-1.0.dtd"
NS_ATOM = "http://www.w3.org/2005/Atom"
UA = "Mozilla/5.0 (podcast-curator; personal feed)"
NOW = (datetime.fromisoformat(os.environ["CURATOR_NOW"]) if os.environ.get("CURATOR_NOW")
       else datetime.now(timezone.utc))

CATEGORIES = [
    "comedy", "ai", "big_ideas", "systems", "geopolitics", "business", "healthcare",
    "news", "crime_econ", "parenting", "cooking", "history", "sports", "other", "excluded",
]
SOURCE_PRIORITY = ["seed", "core", "subscribed", "buzz", "discovery", "search"]

LOG = []
ERRORS = []


def log(msg):
    print(msg, flush=True)
    LOG.append(msg)


def warn(msg):
    print("WARN: " + msg, flush=True)
    ERRORS.append(msg)


# ---------------------------------------------------------------- files

def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception as e:  # corrupt state should never kill the run
        warn(f"could not read {path}: {e}")
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_text(path):
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


# ---------------------------------------------------------------- helpers

def norm_tokens(s):
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower().replace("'", "")).split()


def overlap(query, target):
    """Share of query words found in target."""
    q, t = norm_tokens(query), set(norm_tokens(target))
    if not q:
        return 0.0
    return sum(1 for w in q if w in t) / len(q)


def ep_id(show, title):
    key = " ".join(norm_tokens(show)) + "|" + " ".join(norm_tokens(title))
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def clean_text(s, limit=600):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_date(s):
    if not s:
        return None
    s = s.strip()
    try:
        d = parsedate_to_datetime(s)
    except Exception:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def parse_duration(s):
    """Return minutes from '3600', '62:03' or '1:02:03'."""
    if not s:
        return None
    s = s.strip()
    try:
        if ":" in s:
            parts = [float(p) for p in s.split(":")]
            secs = 0.0
            for p in parts:
                secs = secs * 60 + p
        else:
            secs = float(s)
        return round(secs / 60) if secs > 0 else None
    except ValueError:
        return None


def days_ago(iso):
    d = parse_date(iso) if isinstance(iso, str) else iso
    if not d:
        return 9999
    return (NOW - d).total_seconds() / 86400


def in_window(c, days):
    age = days_ago(c.get("published"))
    return -0.5 <= age <= days


def http_get(url, timeout=25, retries=2):
    last = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise last


# ---------------------------------------------------------------- feeds

def parse_rss(raw, label, source, max_items=None):
    """Parse a podcast RSS feed into candidate dicts."""
    text = raw.lstrip(b"\xef\xbb\xbf \t\r\n")
    root = ET.fromstring(text)
    channel = root.find("channel")
    if channel is None:
        return []
    show = (channel.findtext("title") or label).strip() or label
    show_img = None
    it_img = channel.find(f"{{{NS_IT}}}image")
    if it_img is not None:
        show_img = it_img.get("href")
    if not show_img:
        show_img = channel.findtext("image/url")
    out = []
    for i, item in enumerate(channel.findall("item")):
        if max_items and i >= max_items:
            break
        enc = item.find("enclosure")
        if enc is None or not enc.get("url"):
            continue
        title = (item.findtext("title") or "").strip()
        ep_type = (item.findtext(f"{{{NS_IT}}}episodeType") or "").strip().lower()
        desc = (item.findtext(f"{{{NS_IT}}}summary") or item.findtext("description") or "")
        img = item.find(f"{{{NS_IT}}}image")
        pub = parse_date(item.findtext("pubDate"))
        out.append({
            "id": ep_id(show, title),
            "show": show,
            "title": title,
            "audio_url": enc.get("url"),
            "audio_type": enc.get("type") or "audio/mpeg",
            "published": pub.isoformat() if pub else None,
            "minutes": parse_duration(item.findtext(f"{{{NS_IT}}}duration")),
            "description": clean_text(desc),
            "link": (item.findtext("link") or "").strip(),
            "guid": (item.findtext("guid") or enc.get("url")).strip(),
            "image": (img.get("href") if img is not None else None) or show_img,
            "episode_type": ep_type,
            "source": source,
        })
    return out


def fetch_feed(url, label, source, max_items=None):
    try:
        return parse_rss(http_get(url), label, source, max_items)
    except Exception as e:
        warn(f"feed failed for {label}: {e}")
        return []


# ---------------------------------------------------------------- Apple Podcasts directory

def itunes(params):
    params = dict(params, media="podcast", country="US")
    url = "https://itunes.apple.com/search?" + urllib.parse.urlencode(params)
    try:
        return json.loads(http_get(url)).get("results", [])
    except Exception as e:
        warn(f"directory search failed for {params.get('term')}: {e}")
        return []


def resolve_show_feed(name, cache):
    """Find a show's RSS URL by name, cached for 30 days."""
    hit = cache.get(name)
    if hit and days_ago(hit.get("checked")) < 30:
        return hit.get("url")
    best, best_score = None, 0.0
    for r in itunes({"term": name, "entity": "podcast", "limit": 8}):
        s = overlap(name, r.get("collectionName", ""))
        if s > best_score and r.get("feedUrl"):
            best, best_score = r, s
    url = best["feedUrl"] if best and best_score >= 0.6 else None
    cache[name] = {"url": url, "checked": NOW.isoformat(),
                   "matched": best.get("collectionName") if best else None}
    if not url:
        warn(f"could not find a feed for discovery show '{name}'")
    return url


def itunes_episode_to_candidate(r, source):
    show, title = r.get("collectionName", ""), r.get("trackName", "")
    if not r.get("episodeUrl") or not title:
        return None
    ms = r.get("trackTimeMillis")
    return {
        "id": ep_id(show, title),
        "show": show,
        "title": title,
        "audio_url": r["episodeUrl"],
        "audio_type": "audio/mpeg" if (r.get("episodeFileExtension") or "mp3") == "mp3" else "audio/x-m4a",
        "published": r.get("releaseDate"),
        "minutes": round(ms / 60000) if ms else None,
        "description": clean_text(r.get("description") or r.get("shortDescription")),
        "link": r.get("trackViewUrl", ""),
        "guid": r.get("episodeGuid") or r["episodeUrl"],
        "image": r.get("artworkUrl600") or r.get("artworkUrl160"),
        "episode_type": "",
        "source": source,
    }


def search_episodes(term, source, limit=15):
    out = []
    for r in itunes({"term": term, "entity": "podcastEpisode", "limit": limit}):
        c = itunes_episode_to_candidate(r, source)
        if c:
            out.append(c)
    return out


# ---------------------------------------------------------------- Claude

def claude(system, user, max_tokens=4000, tools=None):
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("no ANTHROPIC_API_KEY")
    messages = [{"role": "user", "content": user}]
    texts = []
    for _turn in range(4):  # server tools may pause and need a continue
        body = {"model": SETTINGS["model"], "max_tokens": max_tokens,
                "system": system, "messages": messages}
        if tools:
            body["tools"] = tools
        data = None
        for attempt in range(4):
            req = urllib.request.Request(
                "https://api.anthropic.com/v1/messages",
                data=json.dumps(body).encode(),
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    data = json.loads(r.read())
                break
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:400]
                if e.code in (429, 500, 502, 503, 529) and attempt < 3:
                    time.sleep(10 * (attempt + 1))
                    continue
                raise RuntimeError(f"Claude API error {e.code}: {detail}")
        texts += [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        if data.get("stop_reason") == "pause_turn":
            messages = messages + [{"role": "assistant", "content": data["content"]}]
            continue
        break
    return "\n".join(texts)


def extract_json(text, opener="["):
    closer = "]" if opener == "[" else "}"
    start, end = text.find(opener), text.rfind(closer)
    if start == -1 or end == -1:
        raise ValueError("no JSON found in model reply")
    return json.loads(text[start:end + 1])


def taste_context(feedback):
    parts = ["# Listener profile\n" + read_text(CFG / "profile.md"),
             "# Listener's latest notes\n" + read_text(CFG / "notes.md")]
    if feedback:
        recent = feedback[-10:]
        lines = []
        for f in recent:
            picks = "; ".join(f.get("picks", []))
            lines.append(f"- {f['date'][:10]} (picks that week: {picks}) Listener said: {f['comment']}")
        parts.append("# Weekly check-in replies (newest last). Learn from these.\n" + "\n".join(lines))
    return "\n\n".join(parts)


SCORING_INSTRUCTIONS = """You score podcast episodes for one listener, described below.
For each candidate return an object with:
- "id": copy it exactly
- "score": 0 to 100. 90+ means the listener would likely star it. 70+ means worth a slot this week.
  Weight guest and host quality first, then topic fit. Use what you know about the guest and the show.
  A "buzz" flag means the episode was found because people are talking about it. Treat that as a plus if it fits.
- "category": one of %s. Use "excluded" for murder or mystery true crime, celebrity gossip, and anything the profile says never to pick.
- "filler": true for trailers, reruns, ad-only drops, housekeeping, or a weak bonus episode. Patreon previews are not filler by themselves.
- "reason": one short sentence, at most 18 words, written to the listener, saying why it is worth it. Name the guest when there is one.
Reply with only a JSON array, no other text.""" % ", ".join(CATEGORIES)


def score_with_claude(cands, feedback):
    system = SCORING_INSTRUCTIONS + "\n\n" + taste_context(feedback)
    results = {}
    for i in range(0, len(cands), 30):
        batch = cands[i:i + 30]
        payload = [{
            "id": c["id"], "show": c["show"], "title": c["title"],
            "minutes": c.get("minutes"), "published": (c.get("published") or "")[:10],
            "source": c["source"], "buzz": bool(c.get("buzz")),
            "description": c.get("description", "")[:450],
        } for c in batch]
        try:
            reply = claude(system, "Candidates:\n" + json.dumps(payload, ensure_ascii=False), 6000)
            for row in extract_json(reply):
                if isinstance(row, dict) and row.get("id"):
                    results[row["id"]] = row
        except Exception as e:
            warn(f"scoring batch {i // 30 + 1} failed: {e}")
    return results


KEYWORDS = {
    "comedy": ["comedian", "stand-up", "comedy", "normand", "morril", "santino", "bobby lee", "stavros", "che "],
    "ai": [" ai ", "artificial intelligence", "openai", "anthropic", "llm", "chatgpt"],
    "big_ideas": ["consciousness", "philosophy", "meaning", "stoic", "meditation"],
    "systems": ["systems", "first principles", "mental model", "constraint", "decision"],
    "geopolitics": ["china", "geopolit", "russia", "iran", "saudi"],
    "healthcare": ["health", "hospital", "medicare", "patient"],
    "crime_econ": ["cartel", "cocaine", "smuggl", "fraud", "launder"],
    "excluded": ["murder", "serial killer", "missing girl", "celebrity gossip"],
}


def score_with_keywords(cands):
    """Offline stand-in for Claude, used only for dry runs and tests."""
    out = {}
    for c in cands:
        text = f" {c['title']} {c['description']} ".lower()
        cat, best = "other", 0
        for k, words in KEYWORDS.items():
            hits = sum(1 for w in words if w in text)
            if hits > best:
                cat, best = k, hits
        score = 0 if cat == "excluded" else min(95, 55 + 12 * best)
        if c["source"] == "seed":
            score = max(score, 80)
        out[c["id"]] = {"id": c["id"], "score": score, "category": cat,
                        "filler": c.get("episode_type") == "trailer",
                        "reason": f"Dry-run keyword match: {cat}."}
    return out


# ---------------------------------------------------------------- gathering

def gather(sources, state, dry):
    cands = []
    core = sources.get("core_shows", [])

    def is_core(name):
        return any(overlap(c, name) >= 0.8 for c in core)

    for s in sources.get("subscribed", []):
        items = fetch_feed(s["url"], s["name"], "subscribed", max_items=40)
        for c in items:
            if in_window(c, SETTINGS["subscribed_window_days"]):
                c["source"] = "core" if is_core(s["name"]) or is_core(c["show"]) else "subscribed"
                cands.append(c)
    log(f"subscribed feeds: {len(cands)} recent episodes")

    if OFFLINE:
        return cands + resolve_seeds(sources, state)

    feed_cache = state.setdefault("feed_cache", {})
    n0 = len(cands)
    for name in sources.get("discovery_shows", []):
        url = resolve_show_feed(name, feed_cache)
        if not url:
            continue
        for c in fetch_feed(url, name, "discovery", max_items=25):
            if in_window(c, SETTINGS["discovery_window_days"]):
                cands.append(c)
    log(f"discovery shows: {len(cands) - n0} episodes")

    n0 = len(cands)
    terms = sources.get("people", []) + sources.get("topics", [])
    rot = max(1, SETTINGS["search_rotation_days"])
    today = NOW.timetuple().tm_yday % rot
    for i, term in enumerate(terms):
        if i % rot != today:
            continue
        for c in search_episodes(term, "search"):
            if in_window(c, SETTINGS["search_window_days"]):
                cands.append(c)
    log(f"people and topic searches: {len(cands) - n0} episodes")

    if not dry and (NOW.weekday() in SETTINGS["buzz_scan_weekdays"] or os.environ.get("FORCE_BUZZ")):
        cands += buzz_scan(state)

    cands += resolve_seeds(sources, state)
    return cands


def buzz_scan(state):
    """Ask Claude with web search for episodes people are talking about."""
    system = ("You find podcast episodes for one listener. Use web search to look at Reddit, "
              "newsletters, best-of lists, and press from the last eight weeks.\n\n"
              + taste_context(state.get("feedback", [])))
    user = ("Find up to 12 specific podcast episodes released in the last eight weeks that are getting "
            "unusual attention and fit this listener. Favor single standout episodes over popular shows. "
            "Skip shows the listener already gets: Stavvy's World, Pardon My Take, Making Sense. "
            'End with only a JSON array of {"show": ..., "title": ..., "why": ...}.')
    try:
        reply = claude(system, user, 4000,
                       tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 8}])
        found = extract_json(reply)
    except Exception as e:
        warn(f"buzz scan skipped: {e}")
        return []
    out = []
    for f in found:
        if not isinstance(f, dict) or not f.get("title"):
            continue
        best, best_s = None, 0.0
        for c in search_episodes(f"{f.get('show', '')} {f['title']}", "buzz", limit=10):
            s = overlap(f["title"], c["title"]) * 0.7 + overlap(f.get("show", ""), c["show"]) * 0.3
            if s > best_s:
                best, best_s = c, s
        if best and best_s >= 0.6:
            best["buzz"] = True
            best["buzz_note"] = f.get("why", "")
            out.append(best)
    log(f"buzz scan: {len(found)} found, {len(out)} matched to audio")
    return out


def resolve_seeds(sources, state):
    seeds = load_json(CFG / "seeds.json", {}).get("seeds", [])
    cache = state.setdefault("seed_cache", {})
    feed_cache = state.setdefault("feed_cache", {})
    out = []
    for s in seeds:
        key = f"{s['show']}|{s['title']}"
        hit = cache.get(key)
        if hit and (hit.get("candidate") or days_ago(hit.get("checked")) < 7):
            if hit.get("candidate"):
                out.append(dict(hit["candidate"], source="seed"))
            continue
        found = None
        url = next((x["url"] for x in sources.get("subscribed", [])
                    if overlap(s["show"], x["name"]) >= 0.8), None)
        if not url and not OFFLINE:
            url = resolve_show_feed(s["show"], feed_cache)
        if url:
            for c in fetch_feed(url, s["show"], "seed"):
                if overlap(s["title"], c["title"]) >= 0.8:
                    found = c
                    break
        if not found and not OFFLINE:
            for c in search_episodes(f"{s['show']} {s['title']}", "seed", limit=10):
                if overlap(s["title"], c["title"]) >= 0.8:
                    found = c
                    break
        cache[key] = {"candidate": found, "checked": NOW.isoformat()}
        if found:
            out.append(found)
        else:
            warn(f"starred episode not found yet: {s['show']} / {s['title']}")
    log(f"starred backlog: {len(out)} of {len(seeds)} found")
    return out


def dedupe(cands):
    by_id = {}
    for c in cands:
        prev = by_id.get(c["id"])
        if prev is None:
            by_id[c["id"]] = c
            continue
        buzz = prev.get("buzz") or c.get("buzz")
        keep = c if SOURCE_PRIORITY.index(c["source"]) < SOURCE_PRIORITY.index(prev["source"]) else prev
        if buzz:
            keep["buzz"] = True
            keep["buzz_note"] = prev.get("buzz_note") or c.get("buzz_note")
        by_id[c["id"]] = keep
    return list(by_id.values())


# ---------------------------------------------------------------- selection

def rank(c):
    bonus = 5 if c.get("buzz") else 0
    return c["score"] + bonus


def eastern_weekday(iso):
    d = parse_date(iso)
    if not d:
        return None
    try:
        from zoneinfo import ZoneInfo
        d = d.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        d = d - timedelta(hours=5)
    return d.weekday()


def rule_for(c, rules):
    return next((r for r in rules if overlap(r["show"], c["show"]) >= 0.8), None)


def is_regular(c, r):
    """True when a core episode is one that always gets sent."""
    if not r or c.get("episode_type") == "trailer":
        return False
    title = c["title"].lower()
    if any(w in title for w in r.get("skip_title_words", [])):
        return False
    if r.get("weekdays") is not None and eastern_weekday(c.get("published")) not in r["weekdays"]:
        return False
    return True


def select(pool, cands, feed_state):
    """Decide what gets sent to the queue today. Anything sent is never sent again."""
    S = SETTINGS
    items = feed_state.setdefault("items", [])
    history = feed_state.setdefault("history", [])
    seen = {h["id"] for h in history}
    changes = []
    rules = S.get("always_include", [])

    # 1. the feed only needs recent picks, since the queue already has them
    keep = []
    for it in items:
        if days_ago(it["added_at"]) >= S["feed_window_days"]:
            mark_removed(history, it["id"])
        else:
            keep.append(it)
    items[:] = keep

    week = [h for h in history if days_ago(h["added_at"]) < 7]

    def add(c, why, kind):
        entry = {k: c.get(k) for k in ("id", "show", "title", "audio_url", "audio_type", "published",
                                       "minutes", "description", "link", "guid", "image", "source",
                                       "score", "category", "reason", "buzz", "buzz_note")}
        entry["added_at"] = NOW.isoformat()
        entry["kind"] = kind
        items.append(entry)
        h = {"id": c["id"], "show": c["show"], "title": c["title"], "source": c["source"],
             "category": c.get("category"), "score": c.get("score"), "minutes": c.get("minutes"),
             "kind": kind, "added_at": entry["added_at"], "removed_at": None}
        history.append(h)
        week.append(h)
        seen.add(c["id"])
        changes.append(f"sent ({why}, score {c.get('score')}): {c['show']} / {c['title']}")

    # 2. regulars: Pardon My Take on Monday and Friday, Stavvy's main episode
    by_rule_day = {}
    for c in cands:
        if c["id"] in seen or days_ago(c.get("published")) > S["always_include_max_age_days"]:
            continue
        r = rule_for(c, rules)
        if not is_regular(c, r):
            continue
        key = (r["show"], (c.get("published") or "")[:10])
        if any(h.get("kind") == "regular" and h.get("rule_key") == "|".join(key) for h in history):
            continue  # that day's regular was already sent
        prev = by_rule_day.get(key)
        if prev is None or (c.get("minutes") or 0) > (prev.get("minutes") or 0):
            by_rule_day[key] = c  # one per show per day, the main (longest) episode
    for c in sorted(by_rule_day.values(), key=lambda c: c.get("published") or ""):
        scored = next((p for p in pool if p["id"] == c["id"]), None)
        c = dict(c, **{k: scored[k] for k in ("score", "category", "reason")}) if scored else dict(c)
        c.setdefault("category", "comedy" if "stavvy" in c["show"].lower() else "sports")
        if not c.get("reason"):
            c["reason"] = f"Your regular {c['show']} episode."
        add(c, "regular", "regular")
        history[-1]["rule_key"] = f"{rule_for(c, rules)['show']}|{(c.get('published') or '')[:10]}"

    # 3. discovery picks, paced to the weekly budget
    disc_week = [h for h in week if h.get("kind") != "regular"]
    budget = min(S["max_discovery_per_day"], S["max_discovery_per_week"] - len(disc_week))
    seeds_week = sum(1 for h in disc_week if h["source"] == "seed")

    def last_added(pred):
        return min((days_ago(h["added_at"]) for h in history if pred(h)), default=9999)

    def eligible(c):
        if c["id"] in seen or c.get("filler") or c["category"] == "excluded":
            return False
        r = rule_for(c, rules)
        if is_regular(c, r):
            return False  # regulars are handled above, never twice
        if r:
            floor = r.get("others_min_score", S["core_min_score"])
        elif c["source"] == "seed":
            floor = S["seed_min_score"]
        elif c["source"] == "core":
            floor = S["core_min_score"]
        else:
            floor = S["min_score"]
        if c["score"] < floor:
            return False
        if c["source"] == "seed" and seeds_week >= S["max_seeds_per_week"]:
            return False
        if c["source"] != "seed" and c.get("minutes") and c["minutes"] > S["max_minutes"]:
            return False
        cool = S["occasional_categories"].get(c["category"])
        if cool and last_added(lambda h: h.get("category") == c["category"]) < cool:
            return False
        if any(overlap(c["show"], h["show"]) >= 0.9 for h in disc_week):
            return False
        if sum(1 for h in disc_week if h.get("category") == c["category"]) >= S["max_per_category_per_week"]:
            return False
        return True

    pool = sorted(pool, key=rank, reverse=True)

    def best(pred=lambda c: True):
        return next((c for c in pool if pred(c) and eligible(c)), None)

    while budget > 0:
        c, why = None, "best available"
        if not any(h.get("category") in S["thoughtful_categories"] for h in disc_week):
            c, why = best(lambda c: c["category"] in S["thoughtful_categories"]), "thoughtful pick"
        if not c and not any((h.get("minutes") or 999) <= S["short_minutes"] for h in disc_week):
            c, why = best(lambda c: (c.get("minutes") or 999) <= S["short_minutes"]), "short pick"
        if not c:
            c, why = best(), "best available"
        if not c:
            break
        add(c, why, "discovery")
        disc_week.append(week[-1])
        if c["source"] == "seed":
            seeds_week += 1
        budget -= 1
    if budget > 0:
        changes.append(f"held {budget} slot(s) open: nothing cleared the bar today")
    return changes


def mark_removed(history, eid):
    for h in history:
        if h["id"] == eid and not h.get("removed_at"):
            h["removed_at"] = NOW.isoformat()


# ---------------------------------------------------------------- publishing

def base_url():
    if SETTINGS.get("feed_base_url"):
        return SETTINGS["feed_base_url"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "owner/podcast-curator")
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"


def write_feed(items):
    ET.register_namespace("itunes", NS_IT)
    ET.register_namespace("atom", NS_ATOM)
    rss = ET.Element("rss", {"version": "2.0"})
    ch = ET.SubElement(rss, "channel")
    url = base_url()
    ET.SubElement(ch, "title").text = SETTINGS["feed_title"]
    ET.SubElement(ch, "link").text = url
    ET.SubElement(ch, "language").text = "en-us"
    ET.SubElement(ch, "description").text = (
        "Podcast episodes picked daily by Claude for one listener. Each episode is sent once.")
    ET.SubElement(ch, f"{{{NS_ATOM}}}link", {"href": url + "feed.xml", "rel": "self",
                                             "type": "application/rss+xml"})
    ET.SubElement(ch, f"{{{NS_IT}}}author").text = "Podcast Curator"
    ET.SubElement(ch, f"{{{NS_IT}}}explicit").text = "true"
    ET.SubElement(ch, f"{{{NS_IT}}}image", {"href": url + "cover.png"})
    img = ET.SubElement(ch, "image")
    ET.SubElement(img, "url").text = url + "cover.png"
    ET.SubElement(img, "title").text = SETTINGS["feed_title"]
    ET.SubElement(img, "link").text = url
    ET.SubElement(ch, "lastBuildDate").text = format_datetime(NOW)

    for it in sorted(items, key=lambda i: i["added_at"], reverse=True):
        e = ET.SubElement(ch, "item")
        ET.SubElement(e, "title").text = f"{it['show']}: {it['title']}"
        why = it.get("reason") or ""
        if it.get("buzz_note"):
            why += f" Buzz: {it['buzz_note']}"
        orig = (it.get("published") or "")[:10]
        ET.SubElement(e, "description").text = (
            f"Why: {why}\n\nOriginally released {orig}. Score {it.get('score')}.\n\n{it.get('description', '')}")
        ET.SubElement(e, "enclosure", {"url": it["audio_url"], "type": it.get("audio_type") or "audio/mpeg",
                                       "length": "0"})
        ET.SubElement(e, "guid", {"isPermaLink": "false"}).text = "curated-" + it["id"]
        ET.SubElement(e, "pubDate").text = format_datetime(parse_date(it["added_at"]))
        if it.get("link"):
            ET.SubElement(e, "link").text = it["link"]
        if it.get("minutes"):
            ET.SubElement(e, f"{{{NS_IT}}}duration").text = str(int(it["minutes"]) * 60)
        if it.get("image"):
            ET.SubElement(e, f"{{{NS_IT}}}image", {"href": it["image"]})
    DOCS.mkdir(exist_ok=True)
    ET.ElementTree(rss).write(DOCS / "feed.xml", encoding="utf-8", xml_declaration=True)


def write_index(items):
    rows = []
    for it in sorted(items, key=lambda i: i["added_at"], reverse=True):
        mins = f"{it['minutes']} min" if it.get("minutes") else ""
        rows.append(
            f"<li><div class='show'>{html.escape(it['show'])}</div>"
            f"<a href='{html.escape(it['audio_url'])}'>{html.escape(it['title'])}</a>"
            f"<div class='why'>{html.escape(it.get('reason') or '')}</div>"
            f"<div class='meta'>{html.escape(it.get('category') or '')} · {mins} · score {it.get('score')} · "
            f"added {it['added_at'][:10]}</div></li>")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Curated Picks</title>
<style>
:root{{--bg:#fbfaf7;--fg:#1d1d1b;--muted:#6b6a65;--accent:#b4441c;--line:#e6e3dc}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161614;--fg:#eeece6;--muted:#9a978f;--accent:#f08a5d;--line:#2c2b28}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:16px/1.5 Georgia,serif}}
main{{max-width:680px;margin:0 auto;padding:32px 16px}}
h1{{font-size:28px;margin:0 0 4px}} p.sub{{color:var(--muted);margin:0 0 24px}}
ul{{list-style:none;padding:0;margin:0}} li{{padding:16px 0;border-top:1px solid var(--line)}}
.show{{font:600 12px/1.4 system-ui,sans-serif;letter-spacing:.06em;text-transform:uppercase;color:var(--accent)}}
a{{color:var(--fg);font-size:19px;text-decoration:none}} a:hover{{text-decoration:underline}}
.why{{margin-top:4px}} .meta{{color:var(--muted);font:13px system-ui,sans-serif;margin-top:4px}}
code{{font-size:13px;word-break:break-all}}
</style></head><body><main>
<h1>Curated Picks</h1>
<p class="sub">Updated {NOW.strftime('%B %d, %Y')}. Subscribe in your podcast app with <code>{html.escape(base_url())}feed.xml</code></p>
<ul>{''.join(rows) or '<li>No picks yet.</li>'}</ul>
</main></body></html>"""
    (DOCS / "index.html").write_text(page, encoding="utf-8")


# ---------------------------------------------------------------- weekly check-in on GitHub

def gh(method, path, body=None):
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not token or not repo:
        raise RuntimeError("GitHub token or repository not set")
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
                 "User-Agent": UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def read_checkin_replies(state):
    """Pull new replies from open check-in issues into state['feedback']."""
    feedback = state.setdefault("feedback", [])
    known = {f["comment_id"] for f in feedback}
    open_issues = [i for i in gh("GET", "/issues?state=open&per_page=50")
                   if i["title"].startswith("Podcast check-in") and "pull_request" not in i]
    picks_by_issue = state.get("checkin_picks", {})
    for issue in open_issues:
        for cm in gh("GET", f"/issues/{issue['number']}/comments?per_page=100"):
            if cm["id"] in known or cm["user"]["type"] == "Bot":
                continue
            feedback.append({"comment_id": cm["id"], "date": cm["created_at"],
                             "picks": picks_by_issue.get(str(issue["number"]), []),
                             "comment": cm["body"].strip()[:1500]})
            log(f"read check-in reply from {cm['created_at'][:10]}")
    return open_issues


def topic_poll(state):
    system = taste_context(state.get("feedback", []))
    user = ("Suggest 3 new podcast topics or shows this listener has not mentioned but would likely enjoy, "
            "based on the profile and starred examples. One line each, under 15 words, labeled A, B, C. "
            "Reply with only the three lines.")
    try:
        return claude(system, user, 400).strip()
    except Exception as e:
        warn(f"topic poll skipped: {e}")
        return ""


def weekly_checkin(feed_state, state, dry):
    if not os.environ.get("GITHUB_TOKEN"):
        log("no GITHUB_TOKEN, skipping check-in")
        return
    try:
        open_issues = read_checkin_replies(state)
    except Exception as e:
        warn(f"could not read check-in replies: {e}")
        return
    if not (NOW.weekday() == SETTINGS["checkin_weekday"] or os.environ.get("FORCE_CHECKIN")):
        return
    week = [h for h in feed_state.get("history", []) if days_ago(h["added_at"]) < 7]
    picks = [f"{n}. {h['show']}: {h['title']}" for n, h in enumerate(week, 1)]
    poll = "" if dry else topic_poll(state)
    user = SETTINGS.get("github_username")
    body = "\n".join([
        f"@{user} Two-minute check-in. Reply below in any format. Short is fine." if user else
        "Two-minute check-in. Reply below in any format.",
        "",
        "**This week's picks**",
        *(picks or ["No new picks this week."]),
        "",
        "**Questions**",
        "1. Which would you have starred?",
        "2. Which did you skip, and why? One word is fine.",
        "3. Want any of these new topics? Reply with the letters.",
        poll or "(no suggestions this week)",
        "4. Anything to change? More, less, never.",
        "",
        "Example reply: `starred 2, 5. skipped 3 boring guest. add B. fewer markets episodes.`",
    ])
    try:
        issue = gh("POST", "/issues", {"title": f"Podcast check-in: week of {NOW.strftime('%b %d')}",
                                       "body": body})
        state.setdefault("checkin_picks", {})[str(issue["number"])] = [p.split(". ", 1)[1] for p in picks]
        for old in open_issues:
            gh("PATCH", f"/issues/{old['number']}", {"state": "closed"})
        log(f"opened check-in issue #{issue['number']}")
    except Exception as e:
        warn(f"could not open check-in issue: {e}")


# ---------------------------------------------------------------- main

def load_settings():
    s = load_json(CFG / "settings.json", {})
    return {k: v for k, v in s.items() if not k.startswith("_")}


SETTINGS = load_settings()
OFFLINE = bool(os.environ.get("OFFLINE"))


def main():
    dry = not os.environ.get("ANTHROPIC_API_KEY")
    if dry and os.environ.get("GITHUB_ACTIONS"):
        print("ANTHROPIC_API_KEY secret is missing. Add it under Settings > Secrets and variables > "
              "Actions. Stopping so no picks are sent without Claude.")
        return 1
    if dry:
        log("No ANTHROPIC_API_KEY found, so this is a dry run with simple keyword scoring.")
    sources = load_json(CFG / "sources.json", {})
    state = load_json(STATE / "state.json", {})
    feed_state = load_json(STATE / "feed.json", {"items": [], "history": []})
    scores = load_json(STATE / "scores.json", {})

    cands = dedupe(gather(sources, state, dry))
    log(f"{len(cands)} unique candidates")

    seen = {h["id"] for h in feed_state.get("history", [])}
    cands = [c for c in cands if c["id"] not in seen]
    need = [c for c in cands
            if c["id"] not in scores or days_ago(scores[c["id"]].get("scored_at")) >= SETTINGS["rescore_after_days"]]
    need.sort(key=lambda c: (SOURCE_PRIORITY.index(c["source"]), days_ago(c.get("published"))))
    need = need[:SETTINGS["max_scored_per_run"]]
    log(f"scoring {len(need)} new or stale candidates")
    fresh = score_with_keywords(need) if dry else score_with_claude(need, state.get("feedback", []))
    for eid, row in fresh.items():
        scores[eid] = {"score": int(row.get("score", 0)), "category": row.get("category", "other"),
                       "filler": bool(row.get("filler")), "reason": row.get("reason", ""),
                       "scored_at": NOW.isoformat()}

    pool = []
    for c in cands:
        s = scores.get(c["id"])
        if s:
            pool.append(dict(c, **{k: s[k] for k in ("score", "category", "filler", "reason")}))
    changes = select(pool, cands, feed_state)
    for ch in changes:
        log(ch)

    write_feed(feed_state["items"])
    write_index(feed_state["items"])
    weekly_checkin(feed_state, state, dry)

    # keep state files from growing forever
    cutoff = 120
    for k in [k for k, v in scores.items() if days_ago(v.get("scored_at")) > cutoff]:
        del scores[k]
    state["last_run"] = {"at": NOW.isoformat(), "dry_run": dry, "candidates": len(cands),
                         "scored": len(fresh), "changes": changes, "errors": ERRORS[-50:]}
    save_json(STATE / "state.json", state)
    save_json(STATE / "feed.json", feed_state)
    save_json(STATE / "scores.json", scores)
    log(f"done. {len(feed_state['items'])} picks in the feed, {len(ERRORS)} warnings.")


if __name__ == "__main__":
    sys.exit(main())
