"""Build fake podcast feeds for an offline test run. Usage: python make_fixtures.py OUTDIR"""
import json
import sys
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
base = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)

pmt = []
for ago in range(9, -13, -1):  # Nov 9 to Nov 30
    day = base - timedelta(days=ago)
    if day.weekday() in (0, 2, 4):
        pmt.append((f"PMT {day:%a %b %d} episode", 150, ago, "Big Cat and PFT on the NFL weekend."))
        if day.weekday() == 0:
            pmt.append((f"PMT {day:%b %d} bonus clip", 20, ago, "Short bonus clip."))
stavvy = []
for ago in range(9, -13, -1):
    day = base - timedelta(days=ago)
    if day.weekday() == 0:
        stavvy.append((f"#{200 + (20 - ago) // 7} - Guest {day:%b %d}", 90, ago, "Stavros with a comedian guest."))
    if day.weekday() == 3:
        stavvy.append((f"Bonus #{day:%d} - Live Call Show [PATREON PREVIEW]", 12, ago, "Preview of the call-in show."))
shows = {
    "Pardon My Take": pmt,
    "Stavvy's World": stavvy,
    "Decoder with Nilay Patel": [
        ("Anthropic's CEO on AI and work", 70, 2, "Dario Amodei on artificial intelligence, Anthropic, and jobs."),
        ("Trailer: new season", 2, 1, "Trailer for the new season."),
        ("The longest AI roundtable ever", 190, 4, "A four hour AI marathon."),
    ],
    "Big Ideas Weekly": [
        ("Consciousness and the self", 60, 5, "A philosophy conversation about consciousness, stoic meditation and meaning."),
        ("Old starred classic: The Lessons of Death", 27, 400, "A meditation on death."),
    ],
    "Mental Models Hour": [("Systems thinking for leaders", 25, 6, "First principles and mental models for decision making.")],
    "Crime Pod": [("The murder at Lake Pine", 45, 2, "A true crime murder mystery.")],
    "World Affairs": [("China's long game", 80, 8, "Geopolitics of China, Russia and Iran.")],
    "Odd Money": [("How cartels launder money", 50, 9, "Cartel economics, cocaine routes, and money laundering.")],
    "Health Talk": [("Hospital finances explained", 40, 10, "Health systems and hospital patient economics.")],
    "Late Week Show": [("Fresh AI story", 30, -8, "OpenAI and Anthropic artificial intelligence news.")],
    "Stoic Hour": [("Stoic meaning in hard times", 40, -3, "Stoic philosophy and meaning.")],
    "Parenting Now": [("Raising boys", 45, 1, "Parenting and health of boys.")],
    "Second AI Show": [("AI agents and work", 55, -5, "Artificial intelligence agents, OpenAI, Anthropic.")],
}

sources = {"core_shows": ["Stavvy's World", "Pardon My Take", "Making Sense with Sam Harris"],
           "subscribed": [], "discovery_shows": [], "people": [], "topics": []}
for show, eps in shows.items():
    items = []
    for title, mins, ago, desc in eps:
        slug = abs(hash(title)) % 10**8
        etype = "<itunes:episodeType>trailer</itunes:episodeType>" if title.startswith("Trailer") else ""
        items.append(f"""<item><title>{escape(title)}</title>
<description>{escape(desc)}</description>
<pubDate>{format_datetime(base - timedelta(days=ago))}</pubDate>
<guid>{slug}</guid>{etype}
<enclosure url="https://example.com/{slug}.mp3" type="audio/mpeg" length="1"/>
<itunes:duration>{mins * 60}</itunes:duration></item>""")
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"><channel>
<title>{escape(show)}</title><itunes:image href="https://example.com/{abs(hash(show))}.jpg"/>
{''.join(items)}</channel></rss>"""
    path = out / (show.replace("'", "").replace(" ", "_") + ".xml")
    path.write_text(xml)
    sources["subscribed"].append({"name": show, "url": path.resolve().as_uri()})

(out / "sources.json").write_text(json.dumps(sources, indent=2))
(out / "seeds.json").write_text(json.dumps({"seeds": [
    {"show": "Big Ideas Weekly", "title": "The Lessons of Death"},
    {"show": "Nowhere Show", "title": "Missing episode"}]}, indent=2))
print("fixtures written to", out)
