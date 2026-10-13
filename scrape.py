"""
RotMG progression data scraper.

Scrapes the RealmEye wiki (https://www.realmeye.com/wiki/realm-of-the-mad-god)
and writes data.js, which index.html loads.

Usage:
    python scrape.py               # uses cached pages where available
    python scrape.py --refresh     # re-download everything
    python scrape.py --delay 1.0   # seconds between requests (be polite)

Pages are cached in ./cache so re-runs are fast.
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE = "https://www.realmeye.com"
HERE = Path(__file__).resolve().parent
CACHE = HERE / "cache"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Links on /wiki/equipment that are not equipment categories.
NOT_CATEGORIES = {
    "ability-items", "armor", "enchanting", "equipment-set-gear", "forge",
    "grand-bazaar", "items", "reskinned-equipment", "ring-of-minor-defense",
    "set-tier-items", "themed-sets", "untiered-drops", "weapon-attributes",
    "weapons",
}
ARMOR_CATEGORIES = {"leather-armors", "heavy-armors", "robes"}

STAT_KEYS = {
    "HP": "HP", "MP": "MP", "ATT": "ATT", "DEF": "DEF", "SPD": "SPD",
    "DEX": "DEX", "VIT": "VIT", "WIS": "WIS",
}

session = requests.Session()
session.headers.update(HEADERS)
ARGS = None
_last_request = 0.0


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def fetch(slug):
    """Return BeautifulSoup for /wiki/<slug>, using the on-disk cache."""
    global _last_request
    CACHE.mkdir(exist_ok=True)
    path = CACHE / (re.sub(r"[^a-z0-9_-]", "_", slug.lower()) + ".html")
    if path.exists() and not ARGS.refresh:
        return BeautifulSoup(path.read_text(encoding="utf-8"), "html.parser")
    for attempt in range(5):
        wait = ARGS.delay - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        try:
            r = session.get(f"{BASE}/wiki/{slug}", timeout=30)
        except requests.RequestException as e:
            log(f"  ! {slug}: {e}; retrying")
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code == 200 and r.text:
            path.write_text(r.text, encoding="utf-8")
            return BeautifulSoup(r.text, "html.parser")
        if r.status_code == 404:
            return None
        log(f"  ! {slug}: HTTP {r.status_code}; backing off")
        time.sleep(5 * (attempt + 1))
    log(f"  ! giving up on {slug}")
    return None


def wiki(soup):
    return soup.select_one(".wiki-page") or soup.body


def slug_of(href):
    if not href or not href.startswith("/wiki/"):
        return None
    return href[len("/wiki/"):].split("#")[0] or None


def text(el):
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)).replace("’", "'")


def num(s, default=None):
    m = re.search(r"-?\d+(?:\.\d+)?", s.replace(",", ""))
    return float(m.group()) if m else default


# ---------------------------------------------------------------- classes

def scrape_categories():
    soup = fetch("equipment")
    cats = []
    for a in wiki(soup).find_all("a", href=True):
        s = slug_of(a["href"])
        if s and "#" not in a["href"] and s not in NOT_CATEGORIES and s not in cats:
            cats.append(s)
    return set(cats)


def scrape_classes(category_set):
    soup = fetch("classes")
    classes = []
    for tr in wiki(soup).find_all("tr"):
        cells = [text(c) for c in tr.find_all(["td", "th"], recursive=False)]
        if len(cells) == 9 and cells[0] != "Class" and cells[1].isdigit():
            caps = dict(zip(["HP", "MP", "ATT", "DEF", "SPD", "DEX", "VIT", "WIS"],
                            map(int, cells[1:])))
            classes.append({"name": cells[0], "slug": cells[0].lower(), "caps": caps})

    for c in classes:
        cs = fetch(c["slug"])
        order = []
        for a in wiki(cs).find_all("a", href=True):
            s = slug_of(a["href"])
            if s in category_set and s not in order:
                order.append(s)
            if s == "rings":
                break
        armor = next((s for s in order if s in ARMOR_CATEGORIES), None)
        ai = order.index(armor)
        c["slots"] = {
            "weapon": order[:ai - 1],
            "ability": order[ai - 1],
            "armor": armor,
            "ring": "rings",
        }
        log(f"class {c['name']}: {c['slots']}")
    return classes


# ---------------------------------------------------------------- categories

SECTION_KINDS = [("limited", "limited"), ("set tier", "st"), ("untiered", "ut"),
                 ("tiered", "tiered")]


def scrape_category(slug):
    soup = fetch(slug)
    w = wiki(soup)
    name = text(soup.find("h1")) if soup.find("h1") else slug.replace("-", " ").title()
    items = []
    section = None
    for el in w.find_all(["h2", "h3", "h4", "table"]):
        if el.name != "table":
            h = text(el).lower()
            section = next((k for key, k in SECTION_KINDS if key in h), None)
            continue
        if section is None:
            continue
        for tr in el.find_all("tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 3:
                continue
            a = tds[2].find("a", href=True)
            item_slug = slug_of(a["href"]) if a else None
            if not item_slug:
                continue
            img = tds[0].find("img")
            tier = text(tds[1])
            items.append({
                "slug": item_slug,
                "name": text(tds[2]),
                "tier": tier,
                "img": (BASE + img["src"]) if img and img.get("src", "").startswith("/") else None,
                "section": section,
            })
    if not items:
        items = scrape_category_links(w)
    return name, items


def scrape_category_links(w):
    """Fallback for pages laid out as grids (e.g. rings): harvest item links
    per section, reading the tier from 'Tier N' sub-headings."""
    items, seen = [], set()
    section = tier = None
    for el in w.find_all(["h2", "h3", "h4", "table"]):
        if el.name != "table":
            h = text(el).lower()
            m = re.match(r"tier (\d+)", h)
            if m:
                tier = m.group(1)
                continue
            section = next((k for key, k in SECTION_KINDS if key in h), None)
            tier = {"ut": "UT", "st": "ST", "limited": "UT"}.get(section)
            continue
        if section is None or tier is None:
            continue
        for a in el.find_all("a", href=True):
            s = slug_of(a["href"])
            img = a.find("img")
            label = (img.get("title") or img.get("alt")) if img else text(a)
            if not s or s in seen or not label or "#" in a["href"]:
                continue
            seen.add(s)
            items.append({
                "slug": s, "name": label, "tier": tier, "section": section,
                "img": (BASE + img["src"]) if img and img.get("src", "").startswith("/") else None,
            })
    return items


# ---------------------------------------------------------------- items

AP_RE = re.compile(r"armou?r[- ]pierc|ignor\w* def", re.I)
MULTI_RE = re.compile(r"multiple targets", re.I)


def parse_stats(s):
    out = {}
    for m in re.finditer(r"([+-]\d+)\s*(HP|MP|ATT|DEF|SPD|DEX|VIT|WIS)\b", s):
        out[m.group(2)] = out.get(m.group(2), 0) + int(m.group(1))
    return out


def scrape_item(slug):
    soup = fetch(slug)
    if soup is None:
        return None
    w = wiki(soup)
    info = {"projectiles": [], "onEquip": {}, "effects": [], "sources": [],
            "forgeDungeons": [], "obtained": [], "powerLevel": None,
            "mpCost": None, "burst": None, "feedPower": None}
    proj = None
    in_blueprint = False
    proc_texts = []
    for tr in w.find_all("tr"):
        th = tr.find("th", recursive=False)
        td = tr.find("td", recursive=False)
        if not th or not td:
            continue
        key = text(th)
        val = text(td)
        links = [a["href"] for a in td.find_all("a", href=True)]
        if key == "Blueprint":
            in_blueprint = True
        if "Proc" in key:
            proc_texts.append(val.replace("–", "-"))
        if key == "Shots":
            proj = {"shots": num(val, 1), "avg": None, "rof": 1.0, "range": None,
                    "ap": False, "multi": False}
            info["projectiles"].append(proj)
        elif key == "Damage" and proj is None:
            # Some abilities list damage without a Shots row.
            proj = {"shots": 1, "avg": None, "rof": 1.0, "range": None, "ap": False, "multi": False}
            info["projectiles"].append(proj)
            m = re.search(r"average:\s*([\d.]+)", val)
            proj["avg"] = float(m.group(1)) if m else num(val)
        elif key == "Cooldown" and proj is not None:
            # A projectile with its own cooldown is a proc (e.g. "Reactive Proc"), not the main attack.
            proj["proc"] = True
        elif key == "Damage" and proj is not None and proj["avg"] is None:
            proj["dmgRange"] = re.sub(r"\s", "", val.split("(")[0]).replace("–", "-")
            m = re.search(r"average:\s*([\d.]+)", val)
            proj["avg"] = float(m.group(1)) if m else num(val)
        elif key in ("Projectile Speed", "Lifetime") and proj is not None:
            proj["detail"] = True
        elif key == "Range" and proj is not None and proj["range"] is None:
            proj["range"] = num(val)
        elif key == "Rate of Fire" and proj is not None:
            # Burst weapons list one rate per shot, e.g. "92, 96, 100%".
            rates = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", val)] or [100]
            proj["rof"] = sum(rates) / len(rates) / 100
        elif key == "Effect(s)":
            def grab(label):
                m = re.search(label + r"[^\d]{0,6}(\d+(?:\.\d+)?)", val)
                return float(m.group(1)) if m else None
            count = grab("Burst Count")
            if count:
                delay = grab("Burst Delay")
                mx = grab("Maximum Burst Delay") or delay or 1.0
                info["burst"] = {"count": int(count), "maxDelay": mx,
                                 "minDelay": grab("Minimum Burst Delay") or mx}
            if proj is not None:
                proj["ap"] = proj["ap"] or bool(AP_RE.search(val))
                proj["multi"] = proj["multi"] or bool(MULTI_RE.search(val))
            info["effects"].append(val)
        elif key == "On Equip":
            for k, v in parse_stats(val).items():
                info["onEquip"][k] = info["onEquip"].get(k, 0) + v
        elif key == "Power Level":
            info["powerLevel"] = num(val)
        elif key == "Feed Power":
            info["feedPower"] = num(val)
        elif key == "MP Cost":
            info["mpCost"] = num(val)
        elif key == "Forging Cost":
            for h in links:
                if h.endswith("#drops"):
                    info["forgeDungeons"].append(slug_of(h))
        elif key in ("Drops From", "Tier Grouped Drops") and not in_blueprint:
            for a in td.find_all("a", href=True):
                s = slug_of(a["href"])
                if s:
                    info["sources"].append({"slug": s, "name": text(a)})
            info["tierGrouped"] = key == "Tier Grouped Drops"
        elif key == "Obtained Through" and not in_blueprint:
            info["obtained"].append(re.sub(r"Current offers on RealmEye's trading pages", "",
                                           val).strip())
    # Armor-piercing is sometimes only mentioned in effects at item level.
    if any(AP_RE.search(e) for e in info["effects"]) and info["projectiles"]:
        if not any(p["ap"] for p in info["projectiles"]):
            info["projectiles"][0]["ap"] = True
    info["obtained"] = [o for o in info["obtained"] if o]
    # A projectile whose damage range is quoted in a proc row ("deals 200-300 damage") is that proc.
    procs = re.sub(r"\s", "", " ".join(proc_texts))
    for p in info["projectiles"]:
        if p.get("dmgRange") and "-" in p["dmgRange"] and p["dmgRange"] in procs:
            p["proc"] = True
        p.pop("dmgRange", None)
    if any(p.get("proc") for p in info["projectiles"]):
        info["effects"].append("Has a proc attack (not counted in DPS)")
    projs = [p for p in info["projectiles"] if p["avg"] and not p.get("proc")]
    # Multi-projectile pages open with a summary row (total shots/damage); drop it
    # when the detailed per-projectile rows are present.
    if any(p.get("detail") for p in projs):
        projs = [p for p in projs if p.get("detail")]
    for p in projs:
        p.pop("detail", None)
    info["projectiles"] = projs
    return info


# ---------------------------------------------------------------- dungeons

# Dungeon-page sections whose links are not the dungeon's own enemies.
NON_ENEMY_SECTIONS = re.compile(r"contents|screenshot|histor|trivia|daily|tips|strateg|guide|"
                                r"notes|gallery|music|achievement|related|see also|quest", re.I)
# Sections after which nothing is about the dungeon's own enemies.
END_SECTIONS = re.compile(r"histor|trivia|daily|tips|strateg|related|see also", re.I)


def scrape_dungeons():
    soup = fetch("dungeons")
    w = wiki(soup)
    dungeons = {}
    section = None
    for el in w.find_all(["h2", "h3", "table"]):
        if el.name != "table":
            section = text(el)
            continue
        for tr in el.find_all("tr"):
            tds = tr.find_all("td", recursive=False)
            if len(tds) < 3:
                continue
            a = tds[0].find("a", href=True)
            diff = num(text(tds[-1]))
            s = slug_of(a["href"]) if a else None
            if s and diff is not None and s not in dungeons:
                dungeons[s] = {"slug": s, "name": text(a), "difficulty": diff,
                               "section": section,
                               "event": "Event" in (section or "") and "Realm Event" not in section}
    log(f"{len(dungeons)} dungeons")
    for s, d in dungeons.items():
        ds = fetch(s)
        if ds is None:
            d["enemies"], d["loot"] = [], []
            continue
        enemies, loot = [], []
        mode = h2_mode = h2_h = None
        for el in wiki(ds).find_all(["h2", "h3", "a"]):
            if el.name in ("h2", "h3"):
                h = text(el).lower()
                if el.name == "h2":
                    mode = h2_mode = "loot" if "drops" in h or "loot" in h else \
                        None if NON_ENEMY_SECTIONS.search(h) else "enemies"
                elif h2_mode != "loot" and not END_SECTIONS.search(h2_h or ""):
                    # Boss/minion sub-sections can sit under e.g. "Screenshots".
                    mode = None if NON_ENEMY_SECTIONS.search(h) else "enemies"
                if el.name == "h2":
                    h2_h = h
                continue
            sl = slug_of(el.get("href"))
            if not sl or mode is None:
                continue
            (enemies if mode == "enemies" else loot).append(sl)
        d["enemies"] = sorted(set(enemies))
        d["loot"] = sorted(set(loot))
    return dungeons


# ---------------------------------------------------------------- attainability

def tier_difficulty(tier, max_tier):
    """Heuristic difficulty for tiered items, on RealmEye's 0-10 dungeon scale.

    Tiered gear drops from many sources, so it is rated by how far up the
    tier ladder it is: top tier ~ endgame, second-top ~ mid/high dungeons,
    third-top ~ godlands/mid dungeons, lower tiers scale linearly 0..3.
    """
    gap = max_tier - tier
    if gap == 0:
        return 8.5
    if gap == 1:
        return 5.5
    if gap == 2:
        return 3.5
    return round(3.0 * tier / max(1, max_tier - 3), 1)


REALM_EVENT_DIFFICULTY = 3.0


def scrape_biome_whites():
    """Biome whites drop from ordinary realm enemies but are many times rarer
    than a normal drop. Returns {item slug: biome name(s)}."""
    soup = fetch("biome-whites")
    out = {}
    if soup is None:
        return out
    for tr in wiki(soup).find_all("tr"):
        tds = tr.find_all("td", recursive=False)
        if len(tds) < 3:
            continue
        a = tds[1].find("a", href=True) or tds[0].find("a", href=True)
        s = slug_of(a["href"]) if a else None
        if s:
            out[s] = ", ".join(text(x) for x in tds[2].find_all("a")) or text(tds[2])
    log(f"{len(out)} biome whites")
    return out
SEASONAL_RE = re.compile(r"event|halloween|oryxmas|christmas|valentine|easter|candy|blooming|"
                         r"totem|pumpkin|egg hunt|beach|summer|winter|st\. patrick|mayhem|anniversary", re.I)


def resolve_attainability(item, dungeons, enemy_to_dungeons, loot_to_dungeons):
    cands = set(item.get("forgeDungeons", []))
    cands |= loot_to_dungeons.get(item["slug"], set())
    for src in item.get("sources", []):
        if src["slug"] in dungeons:
            cands.add(src["slug"])
        cands |= enemy_to_dungeons.get(src["slug"], set())
    cands = [dungeons[c] for c in cands if c in dungeons]
    item["dungeons"] = sorted({c["name"] for c in cands})
    # A 0 rating on RealmEye marks unrated sub-areas (e.g. The Inner Workings, Chess).
    cands = [c for c in cands if c["difficulty"] > 0]

    if item["tierNum"] is not None and item["section"] == "tiered":
        item["difficulty"] = tier_difficulty(item["tierNum"], item["_maxTier"])
        item["diffBasis"] = "tier"
        item["event"] = False
        return
    # Sources found in no dungeon are realm enemies/events; they count as a
    # realm-difficulty route (quest chests and seasonal sources excluded).
    realm_srcs = [s["name"] for s in item.get("sources", [])
                  if s["slug"] not in dungeons and not enemy_to_dungeons.get(s["slug"])
                  and not re.search(r"chest", s["name"], re.I) and not SEASONAL_RE.search(s["name"])]
    non_event = [c for c in cands if not c["event"]]
    # Only when most sources are realm-only, so one rare mimic-type enemy
    # doesn't make every dungeon item look easy.
    if realm_srcs and cands and len(realm_srcs) * 2 > len(item.get("sources", [])):
        non_event.append({"name": "realm: " + ", ".join(realm_srcs[:3]),
                          "difficulty": REALM_EVENT_DIFFICULTY, "event": False})
    pool = non_event or cands
    if pool:
        best = min(pool, key=lambda c: c["difficulty"])
        item["difficulty"] = best["difficulty"]
        item["diffBasis"] = best["name"] if best["name"].startswith("realm") else f"dungeon:{best['name']}"
        item["event"] = not non_event
    elif item.get("sources"):
        item["difficulty"] = REALM_EVENT_DIFFICULTY
        names = [s["name"] for s in item["sources"]]
        item["event"] = all(SEASONAL_RE.search(n) for n in names)
        item["diffBasis"] = ("seasonal event: " if item["event"] else "realm: ") + ", ".join(names[:3])
    else:
        item["difficulty"] = None
        item["diffBasis"] = "not dropped (" + ("; ".join(item.get("obtained", [])) or "unknown") + ")"
        item["event"] = item["section"] == "limited"


# ---------------------------------------------------------------- main

def main():
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="ignore cache")
    ap.add_argument("--delay", type=float, default=0.6, help="seconds between requests")
    ap.add_argument("--out", default=str(HERE / "data.js"))
    ARGS = ap.parse_args()

    category_set = scrape_categories()
    classes = scrape_classes(category_set)

    used = []
    for c in classes:
        for s in c["slots"]["weapon"] + [c["slots"]["ability"], c["slots"]["armor"], "rings"]:
            if s not in used:
                used.append(s)
    slot_of = {}
    for c in classes:
        for s in c["slots"]["weapon"]:
            slot_of[s] = "weapon"
        slot_of[c["slots"]["ability"]] = "ability"
        slot_of[c["slots"]["armor"]] = "armor"
    slot_of["rings"] = "ring"

    categories, items = {}, {}
    for cat in used:
        name, rows = scrape_category(cat)
        tiers = [int(r["tier"]) for r in rows if r["tier"].isdigit()]
        max_tier = max(tiers) if tiers else 0
        categories[cat] = {"slug": cat, "name": name.replace(" - the RotMG Wiki", ""),
                           "slot": slot_of[cat], "items": []}
        log(f"category {cat}: {len(rows)} items")
        for i, r in enumerate(rows, 1):
            if r["slug"] in items:
                continue
            log(f"  [{i}/{len(rows)}] {r['slug']}")
            info = scrape_item(r["slug"])
            if info is None:
                continue
            it = {**r, **info, "category": cat, "slot": slot_of[cat],
                  "tierNum": int(r["tier"]) if r["tier"].isdigit() else None,
                  "_maxTier": max_tier}
            items[r["slug"]] = it
            categories[cat]["items"].append(r["slug"])

    for slug, biome in scrape_biome_whites().items():
        if slug in items:
            items[slug]["rarity"] = "biome"
            items[slug]["biome"] = biome

    dungeons = scrape_dungeons()
    enemy_to, loot_to = {}, {}
    for d in dungeons.values():
        for e in d["enemies"]:
            enemy_to.setdefault(e, set()).add(d["slug"])
        for l in d["loot"]:
            loot_to.setdefault(l, set()).add(d["slug"])
    for it in items.values():
        resolve_attainability(it, dungeons, enemy_to, loot_to)
        it.pop("_maxTier", None)
        it.pop("forgeDungeons", None)

    data = {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": f"{BASE}/wiki/realm-of-the-mad-god",
        "classes": classes,
        "categories": categories,
        "items": items,
        "dungeons": {s: {k: d[k] for k in ("name", "difficulty", "section", "event")}
                     for s, d in dungeons.items()},
    }
    Path(ARGS.out).write_text("window.ROTMG_DATA = " + json.dumps(data, ensure_ascii=False) + ";\n",
                              encoding="utf-8")
    log(f"wrote {ARGS.out}: {len(classes)} classes, {len(categories)} categories, "
        f"{len(items)} items, {len(dungeons)} dungeons")


if __name__ == "__main__":
    main()
