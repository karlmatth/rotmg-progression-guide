# RotMG Progression Guide

A per-class gear progression guide for Realm of the Mad God, built from the
[RealmEye wiki](https://www.realmeye.com/wiki/realm-of-the-mad-god).

- `index.html`: the guide (open via a web server, see below)
- `data.js`: scraped item, class and dungeon data
- `scrape.py`: regenerates `data.js` (`pip install requests beautifulsoup4`)

## Run locally

```
python -m http.server 8765
```

Then open http://localhost:8765.

## Update the data

```
python scrape.py            # uses cache/ for pages already downloaded
python scrape.py --refresh  # re-download everything (~30 min)
```

## Host it

Only `index.html` and `data.js` are needed; it's a static site. `cache/` is
just the scraper's download cache and is git-ignored.

## Analytics and ads

Both are configured in `site-config.js` and stay off until their IDs are filled in.

- **Google Analytics 4:** set `gaMeasurementId` (`G-…`).
- **Google AdSense:** set `adsenseClient` (`ca-pub-…`) and the two display ad unit
  IDs in `adSlots` (`top`, `bottom`). Only these fixed banner units are used; keep
  AdSense **Auto ads** off (or at least its vignette, anchor and side-rail formats)
  so no pop-ups or overlays appear.
- AdSense also needs an `ads.txt` file at the root of the domain
  (`https://karlmatth.github.io/ads.txt`), which lives in a separate
  `karlmatth.github.io` repository.
