# tromi-data

Data collectors for [Tromi](https://play.google.com/store/apps/details?id=ca.romi.tromi).
The plan lives in the app repo, in `docs/OVERPASS_EXTRACT_PLAN.md`.

**This repo stays private until the Régie confirms the feed may be republished.**
Recording prices isn't publishing them. Nothing here is served to the app yet.

## Price history

The Régie de l'énergie du Québec republishes its feed every 5 minutes and keeps only
the latest files, so a price that isn't recorded on the day can't be recovered.
`.github/workflows/collect.yml` runs `collect.py` on a schedule:

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

What two test phones had recorded before collection began, in the same line format:

| File | Span (EDT) | Rows |
|---|---|---|
| `pixel8a-2026-10-30.ndjson.gz` | Sept 30 22:15 to Oct 30 21:54 | 178,175 |
| `pixel4a-2026-10-30.ndjson.gz` | Oct 10 16:50 to Oct 28 08:41 | 101,781 |

Use them together to bootstrap, but keep them apart from the daily files. Their limits:

- **Timing is coarse.** A row is stamped when the phone next refreshed, not when the price
  changed. Together the phones have a recorded refresh in 87% of 3-hour slots over the 4a's span.
- **A first row isn't a change.** It means "this price, at or before this time". Phones delete
  rows older than 30 days, including the one that set a long-standing price, so a price is only
  known from its next change. The 4a's first refresh is a real baseline of 6,745 prices.
- **Coverage:** together they know a price for 86.0% of the 30 days (the 8a alone, 81.7%). From
  Oct 11 on, 99.3% of current station and grade combinations are known.
- **Closed stations are mostly missing.** A phone deletes a station's history along with it.

When both phones refreshed within 2 minutes of each other, they agreed on 99.96% of 18,803
prices.

## Attribution

Prices come from the Régie de l'énergie du Québec. The licence for republishing them is
**unconfirmed**; see the note at the top.
