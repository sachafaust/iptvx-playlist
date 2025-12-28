# IPTVX Playlist Editor Project

## Overview
Building an application to edit IPTVX playlist data and inject custom movie/show playlists.

## IPTVX Application Details

### Installation
- **App Location:** `/Applications/IPTVX.app`
- **Type:** iOS app running on macOS (Catalyst/iOS compatibility)
- **Bundle ID:** `com.mgapps.iptvx`

### Data Storage Locations

```
~/Library/Containers/27DB3F00-3088-4A00-BCCF-C8F6CB49A29F/Data/
├── Documents/
│   └── realm-db.realm              # Main content database (110MB)
├── Library/
│   ├── Application Support/IPTVX/
│   │   ├── CloudKit.sqlite         # Playlist configurations (iCloud synced)
│   │   ├── Local.sqlite            # Local-only configs
│   │   └── Cache.db                # HTTP cache
│   └── Preferences/
│       └── com.mgapps.iptvx.plist  # App preferences
```

## Database Architecture

### SQLite: Playlist Configuration
**Table:** `ZPLAYLISTCONFIGURATION`

| Column | Purpose |
|--------|---------|
| ZPLAYLISTHASHID | Unique ID linking to Realm content |
| ZNAME | Display name |
| ZURLSTRING | Provider base URL |
| ZUSERNAME | Xtream username |
| ZPASSWORD | Xtream password |
| ZEPGURL | EPG guide URL |
| ZSOURCETYPE | Source type enum (8 = Xtream) |
| ZDAYSRELOADFREQUENCY | Sync interval in days |

### Realm: Content Data
**File:** `realm-db.realm`

**Classes:**
- `class_Channel` - Live TV channels
- `class_VODMetadata` - Movies/VOD content
- `class_Playlist` - Playlist definitions
- `class_PlaylistCategory` - Categories
- `class_Actor`, `class_Genre` - Metadata

**Key Fields:**
- `primaryKey` - Realm primary key
- `playlistHashId` - Links to SQLite playlist config
- `categoryHashId` - Links to category
- `streamId` - Xtream stream ID

### Data Flow
```
SQLite (ZPLAYLISTCONFIGURATION)
    │
    │ playlistHashId
    ▼
Realm (class_VODMetadata, class_Channel)
    │
    │ streamId
    ▼
Xtream API Stream URL
```

## IPTV Provider (Xtream Codes API)

### Credentials
- **Server:** `http://REDACTED_SERVER/`
- **Username:** `REDACTED_USERNAME`
- **Password:** `REDACTED_PASSWORD`

### API Endpoints
```bash
# Account info
GET /player_api.php?username=USER&password=PASS

# VOD categories
GET /player_api.php?username=USER&password=PASS&action=get_vod_categories

# VOD streams by category
GET /player_api.php?username=USER&password=PASS&action=get_vod_streams&category_id=ID

# Series categories
GET /player_api.php?username=USER&password=PASS&action=get_series_categories

# Series by category
GET /player_api.php?username=USER&password=PASS&action=get_series&category_id=ID
```

### Stream URL Pattern
```
http://REDACTED_SERVER/movie/{username}/{password}/{stream_id}.{extension}

# Example:
http://REDACTED_SERVER/movie/REDACTED_USERNAME/REDACTED_PASSWORD/3098183.mp4
```

## Integration Options

### Option A: Shadow Playlist (Realm + SQLite)
Create a fake playlist source that IPTVX won't sync:
- Insert into SQLite with unknown source type and reload freq = 0
- Inject content into Realm with matching playlistHashId
- **Risk:** IPTVX might ignore or crash on unknown types

### Option B: Piggyback + Re-inject
Use existing playlistHashId but re-inject after syncs:
- Watch Realm file for changes (FSEvents/launchd)
- Re-inject custom content after each provider sync
- **Risk:** Timing issues, complexity

### Option C: Local M3U Server (Recommended)
Serve custom playlists via local HTTP server:
- Python/FastAPI backend with SQLite for custom library
- Web UI for searching/curating content
- M3U endpoint that IPTVX subscribes to
- Stream URLs point directly to provider
- **Advantage:** Non-invasive, uses standard protocols

## Tools

### Realm Studio
```bash
# Installed via Homebrew
brew install --cask mongodb-realm-studio

# Open Realm database for inspection
open -a "Realm Studio" ~/Library/Containers/27DB3F00-3088-4A00-BCCF-C8F6CB49A29F/Data/Documents/realm-db.realm
```

### SQLite Inspection
```bash
sqlite3 "~/Library/Containers/27DB3F00-3088-4A00-BCCF-C8F6CB49A29F/Data/Library/Application Support/IPTVX/CloudKit.sqlite"
.tables
SELECT * FROM ZPLAYLISTCONFIGURATION;
```

## Current Implementation: Local M3U Server

### Mooncut01 Playlist
YouTube channel [@Mooncut01](https://www.youtube.com/@Mooncut01) curates classic/cult movies by theme.

**Generated Playlists:**
- `mooncut01-all.m3u` - Complete collection (421 movies, 59 video categories)
- `mooncut01-YYYYMMDD-HHMMSS.m3u` - Timestamped backups

**Playlist URL (for IPTVX):**
```
http://localhost:8080/mooncut01-all.m3u
```

**Start the server:**
```bash
cd /Users/sachafaust/code/iptvx-playlist
python3 -m http.server 8080
```

### Refresh Process
To sync new videos from Mooncut01:

```bash
# 1. Fetch all video IDs and titles
yt-dlp --flat-playlist --print "%(id)s|%(title)s" "https://www.youtube.com/@Mooncut01/videos" > /tmp/mooncut_videos.txt

# 2. Fetch descriptions (takes ~3 min for 80+ videos)
while IFS='|' read -r vid_id title; do
  echo "=== $title ==="
  yt-dlp --skip-download --print description "https://www.youtube.com/watch?v=$vid_id"
  echo ""
done < /tmp/mooncut_videos.txt > /tmp/mooncut_descriptions.txt

# 3. Re-run the playlist generator (see scripts/)
```

### VOD Cache
Provider VOD catalog is cached at `/tmp/vod_cache.json` (~49MB).
Refresh with:
```bash
curl -s "http://REDACTED_SERVER/player_api.php?username=REDACTED_USERNAME&password=REDACTED_PASSWORD&action=get_vod_streams" > /tmp/vod_cache.json
```

## Tools

### yt-dlp
```bash
# Installed via Homebrew
brew install yt-dlp

# Get video description
yt-dlp --skip-download --print description "https://www.youtube.com/watch?v=VIDEO_ID"

# List channel videos
yt-dlp --flat-playlist --print "%(id)s|%(title)s" "https://www.youtube.com/@CHANNEL/videos"
```
