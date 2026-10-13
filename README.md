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

- **Google Analytics 4:** set `gaMeasurementId` (`G-…`) in `site-config.js`.
- **Google AdSense:** the AdSense tag in `index.html` runs Auto ads. Ad formats
  and placement are managed in the AdSense dashboard. `ads.txt` lives in the
  `karlmatth.github.io` repository, at the domain root.

## Copyright

Copyright (c) 2026 karlmatth. All rights reserved. No license is granted to copy,
modify, redistribute or host this work; see [LICENSE](LICENSE). Item data and
images are from the RealmEye wiki and belong to their respective owners.
