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

`pixel-2026-10-30.ndjson.gz` is the 30 days a test Pixel 8a had recorded before collection
began. It's the same line format, but captured whenever the app happened to refresh, so its
timing is coarser than the daily files. Use it to bootstrap, and don't mix it with them.

## Attribution

Prices come from the Régie de l'énergie du Québec. The licence for republishing them is
**unconfirmed**; see the note at the top.
