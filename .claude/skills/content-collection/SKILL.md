---
name: content-collection
description: Use when the user asks to build, generate, compile, refresh, or expand a CSV catalog/list/database of movies or TV shows from a specific country/region (especially Quebec/Canadian or Korean/South Korean) for a year range. Trigger on phrasing like "find me all Korean movies", "list Quebec films", "get me a CSV of movies from X", "build a film database", "expand the Quebec shows list", "regenerate the catalog", or any request that ends with feeding titles into a downstream playlist/automation tool. Also trigger when an existing CSV in content-collection/ is incomplete or stale and needs to be rebuilt.
---

# Content Collection Builder

Produces comprehensive CSV catalogs of movies and TV shows from a country/region over a year range. Output is consumed by `generate_playlist.py --from-file` to build M3U playlists matched against the user's IPTV provider catalog.

**Core principle:** Combine authoritative external sources (Wikipedia, filmsquebec.com, IMDB bulk data) **with** the user's IPTV provider catalog tags (`_qb`, `(CA)`, region markers). External sources tell us what *exists*; the provider catalog tells us what's *available*. A complete CSV merges both — neither alone is sufficient.

## Output Location & Schemas

Save CSVs to `content-collection/` at the repo root. UTF-8, properly quoted, one file per collection.

| File | Schema |
|---|---|
| Films | `title,title_en,year,genre,director,imdb_search_url` |
| TV Shows | `title,start_year,end_year,genre,imdb_search_url` |
| Korean films | `imdb_id,title_en,title_kr,year,genre,director,imdb_url` |
| Korean TV | `title_en,title_kr,start_year,end_year,genre,imdb_search_url` |

**Design principles for downstream agent consumption:**
- No formatting, merged cells, or styling — raw structured data only
- UTF-8 (preserves `œ`, Korean characters, accents — these matter for catalog matching)
- IMDB direct links (`/title/tt1234567/`) when known, otherwise search URLs (`/find/?q=...&s=tt&ttype=ft|tv`)
- Empty strings for missing fields (never "N/A" or "Unknown")
- Genres comma-separated within the field
- **Preserve diacritics and ligatures exactly** — substring matchers downstream depend on byte-exact title fragments

## Sources by Collection

| Collection | Primary External | Backup External | Provider Catalog Tag |
|---|---|---|---|
| Quebec Films | filmsquebec.com (year pages) | Wikipedia "List of Canadian films of YEAR" | none reliable in xtream/New |
| Quebec TV Shows | Wikipedia "Liste de téléromans québécois" + recent search | — | `_qb`, `(CA)` in series catalog |
| Korean Films | IMDB bulk datasets (director-validated) | Wikipedia per-year lists | none reliable |
| Korean TV Shows | Wikipedia + MyDramaList + HanCinema | search per year | none reliable |

## Provider Catalog Augmentation (REQUIRED for TV shows)

The user's IPTV providers tag region-specific content. For TV shows especially, the catalog is more authoritative than any external list because it reflects what's actually available to play.

**For Quebec TV shows — two-layer extraction:**

**Layer 1: Direct catalog tags.** Fetch the series catalog from the user's "New" provider (read creds from IPTVX SQLite as `generate_playlist.py` does):
```
{server}/player_api.php?username=...&password=...&action=get_series
```
Filter series whose `name` contains `_qb`, `(CA)`, or the keyword `québec` (case-insensitive). ~402 series in the New provider as of last check.

**Layer 2: Wikipedia cross-check finds untagged Quebec shows.** Some Quebec shows in the catalog use `-fr`, `FR`, `(2014) FR` or no suffix at all instead of `_qb`. Catch them:
1. Fetch `https://fr.wikipedia.org/wiki/Liste_de_téléromans_québécois`
2. Extract `<li>` entries matching `Title (YYYY...)` — keep titles ≥ 8 chars (shorter titles produce false positives like "Trauma" matching unrelated shows)
3. Normalize catalog names (lowercase, strip year-in-parens, strip suffixes `-fr`/`_msub`/`_vost`/` ca`/` nf`/` fr`/` mx`/` uk`/` es`/` de`/` us`)
4. For each untagged catalog show, if its normalized name **exactly equals** a Wikipedia title, treat it as Quebec
5. Layer 2 typically adds ~28-30 shows missed by Layer 1

**Combine layers:** dedupe on `series_id` (catalog primary key). Total ~430 series for Quebec.

**CSV title field:** Use the **full catalog name as the title** (e.g., `Des dames de cœur_qb`, `District 31_fr`, `450, chemin du Golf`). The downstream matcher uses byte-for-byte substring matching — keeping the suffix guarantees a match.

**Year extraction (priority order):**
1. Regex `\((\d{4})\)` from the catalog `name` field (rare — ~5% of series)
2. First 4 digits of `releaseDate` field (`YYYY-MM-DD` format) — ~99% coverage in the New provider
3. Empty otherwise

**CRITICAL: write year into `start_year` column, leave `year` column empty.** Otherwise `find_movie` filters strictly on year-in-name, and most catalog series don't have year-in-name → match rate drops from 100% to ~78%. The script's `parse_movie_file` only auto-copies `start_year → year` if the `year` column is missing. Including an empty `year` column blocks the copy and lets `--group-by start_year` handle grouping while matching stays year-agnostic.

**CSV columns for Quebec series:**
```
title,year,start_year,end_year,genre,imdb_search_url
"Des dames de cœur_qb",,1986,,,
```

**Why this matters:** Without catalog augmentation, the CSV reflects an external editorial list that may not match the user's subscription. The substring matcher in `generate_playlist.py` then fails on:
- Ligature mismatches (`coeur` vs `cœur`)
- Suffix differences (`-fr`, `_qb`, `(CA)`, `_msub`)
- Unlisted recent additions

For Quebec films and Korean content, no reliable provider tag exists — rely on external sources.

## Collection: Quebec Films

**Strategy:** Scrape filmsquebec.com year-by-year. Authoritative for Quebec fiction features (~1,300 since 1960s).

1. For each year in range, fetch:
   ```
   https://www.filmsquebec.com/annees/{year}/
   https://www.filmsquebec.com/annees/{year}/page/2/
   https://www.filmsquebec.com/annees/{year}/page/3/
   ```
   Stop paginating on 404 / "Page non trouvée".

2. Parse `<h4>` tags:
   - `<h4>TITLE – Film de DIRECTOR</h4>` or `<h4>TITLE – Film d'DIRECTOR</h4>`
   - Genre from `<a href="https://www.filmsquebec.com/genres/...">GENRE</a>`
   - Decode HTML entities (`&#8211;` → `–`, `&rsquo;` → `'`, etc.)

3. Dedupe on `(title, year)` — sidebar "recent films" bleeds onto older year pages.

4. IMDB search URL: `https://www.imdb.com/find/?q={title_urlencoded}+{year}&s=tt&ttype=ft`

5. Sort by year asc, title asc.

**Known issues:**
- Fiction features only, not documentaries
- If `<h4>` parsing fails, fall back to `web_search` + Wikipedia "List of Canadian films of {year}"
- ~3-5 sidebar duplicates per year

## Collection: Quebec TV Shows

**Strategy:** Wikipedia téléromans list + recent-shows web search + **provider catalog augmentation (see section above)**.

1. Fetch master list:
   ```
   https://fr.wikipedia.org/wiki/Liste_de_téléromans_québécois
   ```

2. Parse entries `Title (YYYY-YYYY)` or `Title (YYYY-en cours)`. Map "en cours" → empty `end_year`.

3. Supplement recent shows:
   ```
   nouvelles séries québécoises {current_year} Radio-Canada TVA Noovo
   séries québécoises {current_year-1} {current_year}
   ```

4. **Augment from provider catalog** — see "Provider Catalog Augmentation" above. Without this step the list will miss ~30% of available shows.

5. Filter to date range with overlap logic: `start_year <= range_end AND (end_year >= range_start OR end_year empty)`.

6. Dedupe on case-insensitive title-prefix. Prefer catalog rows over Wikipedia rows.

**Known issues:**
- Wikipedia rarely has genre — column will be mostly empty
- Comedies and variety shows underrepresented in téléromans list
- IMDB search URLs use `&s=tt&ttype=tv`

## Collection: Korean Films

**Strategy:** IMDB bulk datasets with **Korean director validation** pipeline. No API key needed.

**Why not just use IMDB's KR region?** `title.akas.tsv.gz` lists ~36,000 films with Korean distribution, but most are Hollywood/Japanese/Chinese films merely released in Korea. Naive extraction yields ~11,000 with massive contamination. Director-validation reduces to ~4,000 genuine Korean-produced films.

1. **Download (~1 GB total, parallel):**
   ```bash
   curl -s -o title.akas.tsv.gz   "https://datasets.imdbws.com/title.akas.tsv.gz" &
   curl -s -o title.basics.tsv.gz "https://datasets.imdbws.com/title.basics.tsv.gz" &
   curl -s -o title.crew.tsv.gz   "https://datasets.imdbws.com/title.crew.tsv.gz" &
   curl -s -o name.basics.tsv.gz  "https://datasets.imdbws.com/name.basics.tsv.gz" &
   wait
   ```

2. **Pass 1** — `title.akas.tsv.gz`: scan for `region == "KR"`. Build `tconst → Korean title` map (~36k films).

3. **Pass 2** — `title.crew.tsv.gz`: extract director `nconst` IDs for KR films (~28k).

4. **Pass 3** — `name.basics.tsv.gz`: load director names. Classify Korean using surname dictionary (below). Result: ~5,500-6,000 Korean directors.

5. **Pass 4** — `title.basics.tsv.gz`: filter in order:
   - `titleType == "movie"`
   - In KR akas set
   - `startYear` in range
   - Runtime ≥ 40 min when known (excludes shorts)
   - **Korean director → KEEP** (even if `primaryTitle == originalTitle`)
   - **Non-Korean director + `originalTitle == primaryTitle` → EXCLUDE** (English-language)
   - **Non-Korean director + `originalTitle != primaryTitle` → EXCLUDE** (foreign-language)
   - **No director data + Korean romanization markers in original → KEEP**
   - **No director data + no Korean markers → EXCLUDE**

6. Dedupe on `tconst`, sort by year then title.

7. **Cleanup:** delete the .tsv.gz files (1GB+).

### Korean Surname Dictionary

```python
KOREAN_SURNAMES = {
    'kim','lee','yi','rhee','ri','park','pak','bak',
    'choi','choe','jung','jeong','chung','cheong',
    'kang','gang','cho','jo','yoon','yun','jang','chang',
    'lim','im','rim','han','oh','seo','suh','shin','sin',
    'kwon','gwon','hwang','ahn','an','song','yoo','yu','ryu',
    'hong','jeon','jun','chun','ko','go','goh','koh',
    'moon','mun','son','yang','bae','pae','baek','paek','back',
    'heo','hur','huh','nam','noh','roh','no','ha',
    'woo','wu','gu','ku','koo','goo',
    'min','won','pi','bi','cheon','bang','pang',
    'sim','shim','byun','byeon','yim','do','ji',
    'bong','kwak','yeo','eom','um','sung','seong',
    'ryu','ryoo','joo','ju','cha','pyo','gil',
    'sa','mae','tak','ok','chin','gyeong','kyung',
    'yeom','yeon','hyun','hyeon','jeung',
    'na','ra','wee','wi','dam','geum','in','eo',
    'yeong','young','myeong','myung','tae','dae',
    'seol','sol','ye','gye','chu','noh',
    'rang','maeng','pil','su','hwan','suk',
}

NON_KOREAN_DIRECTORS = {'Ang Lee','Spike Lee','Bruce Lee','Jason Lee','Stan Lee'}

def is_korean_name(name):
    if not name or name in NON_KOREAN_DIRECTORS: return False
    parts = name.lower().strip().split()
    if len(parts) < 2: return False
    return parts[0] in KOREAN_SURNAMES or parts[-1] in KOREAN_SURNAMES
```

**Known false negatives (~2-3% miss rate):** films where `primaryTitle == originalTitle` AND director has no IMDB entry; unusual romanizations not in dict; Korean-American directors with anglicized names.

### Korean Romanization Markers (fallback for missing director data)

Korean Revised Romanization vowel clusters distinctive vs Japanese/Chinese:
```python
korean_markers = ['eo', 'eu', 'eun', 'eul', 'ae', 'ui ']
```

### Quality Verification

Print pass/fail against these benchmark titles before delivering:

**Must be present:** Parasite, Oldboy, Memories of Murder, Train to Busan, The Handmaiden, Shiri, My Sassy Girl, Decision to Leave, Burning, The Wailing, A Taxi Driver, I Saw the Devil, The Host, A Tale of Two Sisters, Joint Security Area, Extreme Job, Veteran, The Man from Nowhere, Minari, Spring Summer Fall Winter... and Spring

**Must be absent:** Spirited Away, Your Name., Shoplifters, Crouching Tiger Hidden Dragon, Avengers: Endgame, Ip Man, Kung Fu Hustle, In the Mood for Love, Departures, Battle Royale

If >2 expected films missing OR any banned films present, debug before delivering.

## Collection: Korean TV Shows

**Strategy:** Wikipedia + web search.

1. Fetch `https://en.wikipedia.org/wiki/List_of_South_Korean_television_series` plus per-year and network-specific lists.
2. Search supplements: `Korean drama series {year} complete list`, `K-drama {year} all shows Netflix tvN KBS SBS MBC`, `새 한국 드라마 {year}`.
3. Parse title, year(s), genre, network when available.
4. Filter to range with overlap logic.
5. IMDB search URL with `&s=tt&ttype=tv`.

## Performance Expectations

- Quebec Films: ~30s (web scrape, ~40 pages)
- Quebec TV: ~15s (Wikipedia + search) + ~5s (catalog augmentation)
- Korean Films: 3-5 min (download + 4-pass processing)
- Korean TV: ~30s

## Error Handling

- filmsquebec.com down → fall back to Wikipedia "List of Canadian films of YEAR"
- IMDB bulk download fails → retry up to 3× with 5s delay
- Wikipedia blocked from `curl` → use `web_fetch` tool
- IPTV provider unreachable → produce CSV from external sources only and warn the user that catalog augmentation was skipped
- Always delete IMDB .tsv.gz after processing

## Delivering Results

1. Save CSV to `content-collection/{collection}_{start}_{end}.csv`
2. Report: total count, per-year distribution, verification results (Korean only)
3. **Tell the user the next command to run.** Default to a single combined playlist (movies + shows in one M3U) so the user only manages one URL in IPTVX. IPTVX auto-routes `movie/` URLs to the Movies tab and `series/` URLs to the Shows tab, so a single combined M3U appears in both tabs naturally:
   ```bash
   # Step 1: movies (replaces the playlist file)
   python3 generate_playlist.py --from-file content-collection/{region}_films_{start}_{end}.csv \
       --name "{Region}-Movies" --group-by year genre

   # Step 2: append shows (with year grouping) and upload the combined file
   python3 generate_playlist.py --from-file content-collection/{region}_tv_shows_{start}_{end}.csv \
       --name "{Region}-Movies" --type series --group-by start_year --append --upload
   ```
   **Only suggest splitting into separate Movies/Shows playlists if the user explicitly asks.** Splitting requires a manual IPTVX UI step (CloudKit blocks DB inserts) and offers no upside for most workflows.
4. Keep post-delivery commentary brief.

## Critical Pitfalls

### Re-uploading breaks IPTVX Favorites

IPTVX channel-level data (Favorites, watch history) references internal channel IDs derived from a hash that includes `tvg-name`, `group-title`, and stream URL. Any change to those — even reordering, regrouping, or adding/removing entries — invalidates the hash on next M3U refresh. Favorites become orphaned and effectively lost (no automatic recovery; backups are not kept).

**Mitigations:**
- **Settle on grouping/structure before users start favoriting.** Once a playlist is in active use, treat its EXTINF format as a stable contract.
- **Warn the user before any re-upload.** Before running the same `generate_playlist.py` command twice, confirm they're aware that any favorites tied to the changing playlist will be lost.
- **No automatic recovery exists.** The Realm DB at `~/Library/Containers/27DB3F00-3088-4A00-BCCF-C8F6CB49A29F/Data/Documents/realm-db.realm` keeps no `.bak`. Time Machine is the only fallback.
- Splitting movies and shows into separate playlists *can* isolate updates, but it forces a manual UI add step in IPTVX and is rarely worth the friction. Default to a single combined playlist.

### Don't insert directly into the IPTVX SQLite

The catalog database (`~/Library/Containers/.../IPTVX/CloudKit.sqlite`) uses Core Data with CloudKit sync. New `ZPLAYLISTCONFIGURATION` rows require encoded `CKRecord` blobs in `ANSCKRECORDMETADATA` that only Core Data can produce. Direct SQL inserts crash IPTVX on next launch. Always use the IPTVX UI to add new playlists; the script only **updates** existing playlist URLs.

## Multi-Collection Order

When the user requests several, run fastest → slowest:
1. Quebec TV Shows (~20s)
2. Quebec Films (~30s)
3. Korean TV Shows (~30s)
4. Korean Films (3-5 min)

## Extending to Other Countries

The Korean Films pipeline generalizes:
1. Change region filter (`JP`, `FR`, etc.) in Pass 1
2. Build a country-specific surname dictionary
3. Update false-positive exclusion list
4. Update verification benchmark films

The IMDB bulk approach works for any country. The bottleneck is always the surname dictionary.

For TV shows in regions where the IPTV catalog uses tags (look for `_xx` suffixes or `(YY)` markers in series names), apply the **Provider Catalog Augmentation** approach.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Stripping suffixes like `_qb` from catalog titles before writing CSV | Keep them — substring matcher needs them |
| Normalizing ligatures (`œ` → `oe`) | Don't — provider catalog uses `œ` |
| Using `/mnt/user-data/outputs/` | Save to `content-collection/` instead |
| Skipping catalog augmentation for Quebec TV | Mandatory — external lists miss ~30% |
| Skipping Wikipedia cross-check (Layer 2) | Catches `-fr` / `FR` shows that lack `_qb` tag (~28 extra) |
| Putting year into the `year` column for TV shows | Strict year-in-name matching kills 22% of matches; use `start_year` column |
| Splitting movies + shows into separate playlists by default | Single combined playlist is preferred. IPTVX auto-routes `movie/` and `series/` URLs to the right tabs. Only split when explicitly requested. |
| Using `--group-by year` for series | Year column is empty in our schema. Use `--group-by start_year`. |
| Re-uploading without warning the user | Breaks IPTVX favorites silently. Always confirm first. |
| Filtering Wikipedia titles < 8 chars in cross-check | Short titles ("Trauma", "Le Jeu") match unrelated shows |
| Trusting filmsquebec.com alone for older films | Source is incomplete pre-1990. Augment with Wikipedia "List of Canadian films of YEAR" |
