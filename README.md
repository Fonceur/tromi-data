# tromi-data

Data collectors for [Tromi](https://play.google.com/store/apps/details?id=ca.romi.tromi), a free,
ad-free app for Québec drivers. Three things are published here: the 30-day price history, which
Tromi for Android reads from version 1.0.9; daily regional averages, Tromi's own calculation,
which no app version reads yet; and the whole archive, one file per month, for Tromi for iOS's
price chart.

**Gas prices: Régie essence Québec, Régie de l'énergie.** Tromi is independent: it isn't mandated
by, affiliated with or endorsed by the Régie de l'énergie. Reusing these prices comes with the
Régie's conditions; see [Source and terms](#source-and-terms).

## Price history

Régie essence Québec republishes its feed every 5 minutes and keeps only the latest files, and
the Régie doesn't plan to publish a per-station history, so a price that isn't recorded on the day
can't be recovered. `.github/workflows/collect.yml` runs `collect.py` on an hourly schedule.
GitHub drops some scheduled runs: on an every-3-hours schedule it ran 3 a day, not 8, so a change
can be stamped several hours late.

| Path | What |
|---|---|
| `history/YYYY/MM/DD.ndjson` | Every price change seen that UTC day |
| `history/seed/` | One-off phone-sampled history, not collected here (see below) |
| `state/latest.json` | The last price per station and grade |
| `state/stations.json` | Each station's name, brand, region and position |
| `state/feed.json` | The last feed's ETag and `generated_at` |

Each history line is one change:

```json
{"t":"2026-10-31T02:00:00Z","id":"9531_boul._gouin_ouest,_montréal_h8y_1r2","f":"REGULIER","c":1994}
```

- `t` is the feed's `generated_at`, so a change happened at or before that time.
- `id` is built exactly as the app builds it, so it joins onto `price_snapshots`. A
  corrected address re-keys a station and splits its history, as it does in the app.
- `c` is tenths of a cent: `1994` is 199.4¢. `null` means the grade stopped being sold or
  the station left the feed.

The first line of a station and grade in the daily files is a baseline, not a change.

**A failed run means the feed looks wrong:** under 2,000 stations, or most of them unpriced.
It writes nothing and fails on purpose. Fix `collect.py` quickly, because every day it
stays down is history lost for good.

`collect.py` mirrors the app's `RegieEssenceRemoteDataSource`. If those rules change there,
change them here too.

```bash
python3 -m unittest discover -s tests
```

### `history/seed/`

What two test phones, a tablet, the iOS simulators and two Android emulators had recorded before
collection began, in the same line format:

| File | Span (EDT) | Rows |
|---|---|---|
| `pixel8a-2026-10-30.ndjson.gz` | Sept 30 22:15 to Oct 30 21:54 | 178,175 |
| `pixel4a-2026-10-30.ndjson.gz` | Oct 10 16:50 to Oct 28 08:41 | 101,781 |
| `pixel4a-1.0.3-2026-10-31.ndjson.gz` | Aug 22 21:52 to Sept 11 02:49 | 103,949 |
| `ios-simulators-2026-10-31.ndjson.gz` | June 7 15:14 to Oct 20 20:05, 19 scattered days | 251,479 |
| `avds-2026-10-31.ndjson.gz` | July 5 08:18 to Oct 17 17:53, 5 scattered days | 44,430 |
| `tabs7-1.0.3-2026-10-31.ndjson.gz` | July 18 02:08 and Aug 2 09:10 | 12,986 |

Use them together to bootstrap, but keep them apart from the daily files. Their limits, with the
figures covering the two October phone files:

- **Timing is coarse.** A row is stamped when the phone next refreshed, not when the price
  changed. Together the phones have a recorded refresh in 87% of 3-hour slots over the 4a's span.
- **A first row isn't a change.** It means "this price, at or before this time". Phones delete
  rows older than 30 days, including the one that set a long-standing price, so a price is only
  known from its next change. The 4a's first refresh is a real baseline of 6,745 prices.
- **Coverage:** together they know a price for 86.0% of the 30 days (the 8a alone, 81.7%). From
  Oct 11 on, 99.3% of current station and grade combinations are known.
- **Closed stations are mostly missing.** A phone deletes a station's history along with it.
- **Some prices flip.** A station can alternate between two prices on consecutive refreshes: by
  the same test (3+ straight flips, each under 6 h apart), 154 station and grade pairs in the 8a's
  file, 31 in the 4a's and 24 in the 1.0.3 file. `150 rte 116, Richmond` did it on Sept 1–2, and
  has two SUPER prices stamped the same second, the only such key in the seed. The cause isn't
  known, so these rows stay as recorded.

When both phones refreshed within 2 minutes of each other, they agreed on 99.96% of 18,803
prices.

**The 1.0.3 file stands apart.** It comes from a separate install on the 4a, and ends nearly 20
days before the 8a's file starts, so its last prices aren't a starting point for October. It has
155 refreshes, 3 h apart on average and up to 12.2 h. The phone has nothing older, and its first
refresh holds 5,326 prices, so that isn't a full baseline. Its last row for each station and grade
equals the price on the phone's station row, for all 6,918 that have both.

**The simulators' file differs again.** `tools/seed_from_simulators.py` merged 25 iOS simulators'
databases and 6 Xcode test clones, and dropped 117,863 exact duplicates. A simulator refreshed only
when someone ran the app, so this is 176 refreshes on 19 days, not a steady record. 24 of them are
full snapshots of the feed, on 13 of those days. One, on Sept 12, is the only data between the 1.0.3
file and Sept 30. Nothing covers Sept 13 to 29.

- **A row is a sighting, not a change.** The file merges separate databases, so a line can repeat
  the price before it. Each row means "this price at this time".
- **Snapshots fill in what a phone's first rows can't.** At noon on Oct 5 the October phone files
  know 51.5% of their 6,923 station and grade pairs. Sim rows from the 3 days before raise that to
  99.0%.
- **It has stations the phones don't:** 16 that have since been re-keyed or left the feed.
- **It agrees with the phones.** Within 2 minutes of a phone's refresh, 11,245 of 11,263 prices
  are equal. The other 18 are one refresh, 82 s after the phone's, during a 3¢ move.

**The emulators' file reads the same way.** The same tool merged both Tromi packages from two
Android emulators, so a row is a sighting here too: 8 refreshes on 5 days, 6 of them full snapshots.
Jul 5, Jul 15 and Aug 1 are the only data between June 22 and Aug 2. No row repeats one in another
file. Each database's last row for a station and grade equals the price on its station row, for
all 27,595, and the 193 prices within 2 minutes of a phone's refresh are all equal.

**The Tab S7+ file is two refreshes** from an old 1.0.3 debug install, nearly full snapshots because
they're 15 days apart. July 18 is the only data between July 15 and Aug 1. No row repeats one in
another file, and its last rows equal the prices on its station rows, for all 6,909.

## Published files

`publish.py` builds every file under `data/qc/`, and the workflow's `publish` job deploys only
them to GitHub Pages, under `https://fonceur.github.io/tromi-data/data/qc/`, after each run that
recorded a change. If any fails its check, the build writes none and Pages keeps the last good
copy of each, since a deploy replaces the whole site.

```bash
python3 publish.py   # writes _site/data/qc/prices-30d.json, regional-daily.json and history/
```

### `prices-30d.json`

The last 30 days of changes, which Tromi downloads once to fill in a new install's price history.
Live since 2026-11-07.

```json
{"schema":1,"source":"Régie essence Québec, Régie de l'énergie","source_url":"https://regieessencequebec.ca",
 "terms":"…","from":1791438900,"to":1794030900,"unit":"tenths of a cent: 1994 is 199.4¢",
 "rows":"…","stations":{"<id>":{"REGULIER":[[1794000000,1994]]}}}
```

- Each `[epoch seconds, price]` is a published price, stamped with the feed time it was first
  seen at. A row is kept only where the price differs from the one before it, which is how the
  app stores history, so nothing is averaged, rounded or filled in.
- A station and grade whose price didn't change in the window has no rows.
- `null` rows are left out: a price that left the feed simply stops.
- Before collection began (2026-10-31 02:30 UTC), rows come from `history/seed/`.
- Under 2,000 stations, the build fails and Pages keeps the last good file.
- About 2,450 stations and 186,000 rows on 2026-11-07: 3.4 MB, 620 KB gzipped.

Every file carries `source` and `terms`, so a downloaded copy keeps its attribution. `schema` changes
only for a format an older app can't read: the app rejects any other value and keeps the history
it has.

### `regional-daily.json`

**Tromi's calculation, not a Régie figure.** The Régie publishes regional averages of its own, and
these can differ from them. Whoever shows them must label them as Tromi's and credit the source,
as the file's `terms` say. For each Régie region and grade, one point per day for the 30 complete
days, Québec time, before the last feed:

```json
{"schema":1,"source":"Régie essence Québec, Régie de l'énergie","source_url":"https://regieessencequebec.ca",
 "terms":"…","method":"…","from":"2026-10-09","to":"2026-11-07","feed":1794153600,
 "time_zone":"America/Toronto","unit":"tenths of a cent: 1994 is 199.4¢","points":"…",
 "regions":{"Montréal":{"REGULIER":[["2026-10-09",1939,223]]}}}
```

- A point is `[day, average, stations averaged]`. The average is of each station's last published
  price at the end of that day, rounded half up to a tenth of a cent. `feed` is the last feed's
  epoch seconds, as `to` is in the backfill file.
- A station counts in the region the current feed gives it (`state/stations.json`), so a station
  that has left the feed counts on no day.
- A point is left out with fewer than 5 stations, or under 80% of the series' last-day count, so
  the line moves with prices, not with how many stations are known. That drops the one station in
  "Municipalités hors MRC \\ CMM", so 17 regions have lines.
- Under 15 regions with a regular-grade line, the build fails: the join onto `state/stations.json`
  broke.
- Unlike the backfill file, it needs every current price, so it reads the daily files from
  collection start, whose first run is a full snapshot. A price unchanged since then is known only
  from it. That read grows with the archive, by about 6,000 rows a day.
- **Before collection began, the points come from `history/seed/`**, with its limits. At the seam
  (Oct 30, the first day the collector closed), 10 station and grade pairs the seed knew stop
  counting, because the collector's first snapshot didn't have them. No line moved by more than
  about 1¢ there. The seed days leave the window around Nov 30.
- 17 regions, 3 grades, 37 KB, 8 KB gzipped, on 2026-11-08. Its Nov 7 points equal averages of the
  collector's own state at its last run that day, for all 51 lines.

### `history/index.json` and `history/prices-YYYY-MM.json`

The archive since its continuous start, one file per Québec month, which Tromi for iOS reads to
draw its full-screen price chart over any days since then. Live from the first deploy after
2026-11-13.

```json
{"schema":1,"source":"Régie essence Québec, Régie de l'énergie","source_url":"https://regieessencequebec.ca",
 "terms":"…","from":1790820958,"to":1794612900,"time_zone":"America/Toronto","continuity":"…",
 "months":["2026-09","2026-10","2026-11"],"path":"prices-<month>.json, beside this file"}
```

- **`from` is the continuous start:** the first recorded refresh after the last gap of more than
  24 hours between two of them. On 2026-11-13 that was 02:15:58 UTC on Oct 1 (Sept 30, 22:15 in
  Québec), in the seed: nothing covers Sept 13 to 29. A gap of more than a day, now or later, moves
  `from` past it, since prices change several times a day.
- Each month file has the 30-day file's rows, for its month, Québec time, held to `from` and `to`,
  plus `starts`: the published price in force at its `from` for each station and grade that has
  one. A month can be drawn on its own, and a station's line starts at the month's start. The first
  month's `starts` is empty: nothing before `from` is used, since the archive has gaps there.
- A `null` takes a pair out of the next months' `starts`, until it's priced again. At the
  collector's first run every price is forgotten, as for the regional file, so a price only the
  seed knew doesn't carry into November. The collector's baseline appears as a row then.
- A month that began after collection started must know at least 2,000 stations, or the build
  fails. A seed month knows what the phones knew: October starts with 233 stations priced, and
  most stations' October lines start at their first recorded change.
- A past month's file is rebuilt identically on every run, so it caches for good.
- On 2026-11-13: September 24 KB (Sept 30 from 22:15), October 3.8 MB, November so far 1.6 MB.
- Every file carries `source` and `terms`, as the others. `schema` is their own number.

## Source and terms

The prices come from **Régie essence Québec, Régie de l'énergie**: the
[Régie essence Québec](https://regieessencequebec.ca) platform of the Régie de l'énergie du
Québec. On 2026-11-05 the Régie confirmed by email that the platform's data is open to everyone
in Québec, with no limit on access, frequency or redistribution, on three conditions. They apply
to anyone who reuses the prices in this repo:

1. The data must not be modified.
2. The source, "Régie essence Québec, Régie de l'énergie", must be credited and visible at all
   times.
3. Nobody may claim to be mandated by the Régie de l'énergie, or let anyone believe they are.

There is no formal licence, Creative Commons or otherwise: these conditions are the terms. The
Régie takes no position on how a third party collects the data.

What this repo does with the feed, so you can tell recorded data from Tromi's own work:

- **Prices are as published.** `c` re-encodes `199.4¢` as `1994`, with no rounding: every price
  had one decimal on 2026-11-07, and `collect.py` warns if one ever has more.
- **Left out, not altered:** stations whose `Status` isn't "En opération", stations placed outside
  Québec, and grades marked unavailable or with no readable price, unless more than half the
  feed would go (then everything is kept). These rules mirror what the app shows.
- **Tromi's, not the Régie's:** the station `id`, derived from the address and postal code because
  the feed has none, and the `null` rows, which mark a price that left the feed.
- **When:** `t` is the time the feed was generated, so a change happened at or before it.
- **`history/seed/`** was recorded from the same feed by test devices running Tromi, with the
  limits described above.

Any figure computed from these prices, such as the averages in `regional-daily.json`, is Tromi's
calculation, not a Régie figure. The Régie publishes its own regional averages.
