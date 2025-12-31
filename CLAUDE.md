# IPTVX Playlist Generator

Generate M3U playlists from YouTube movie recommendation channels, matched against an IPTV provider's VOD catalog.

## Project Structure

```
iptvx-playlist/
├── generate_playlist.py   # Main CLI tool
├── hide_broken.js         # IPTVX Realm helper (Node.js)
├── tests/                 # pytest unit tests (86% coverage)
├── playlists/             # Generated M3U output (gitignored)
├── pyproject.toml         # Project config, pytest settings
└── .venv/                 # Python virtual environment
```

## CLI Usage

```bash
# Generate playlist for a YouTube channel
python generate_playlist.py @ChannelName

# Sync existing playlists (validates URLs, finds new videos)
python generate_playlist.py --sync

# List available IPTVX playlists
python generate_playlist.py --playlists

# Use specific IPTVX playlist for credentials
python generate_playlist.py @Channel --playlist "playlist-name"

# Parallel workers for URL validation (default: 5)
python generate_playlist.py --sync --workers 10
```

## Credential Sources (priority order)

1. **IPTVX App** - Auto-reads from `~/Library/Containers/.../IPTVX/CloudKit.sqlite`
2. **Environment variables** - `IPTV_SERVER`, `IPTV_USERNAME`, `IPTV_PASSWORD`

No config file needed. Credentials are never stored in project files.

## Key Functions

| Function | Purpose |
|----------|---------|
| `get_iptvx_playlists()` | Query IPTVX SQLite for Xtream credentials |
| `fetch_channel_videos()` | Get video list from YouTube via yt-dlp |
| `parse_movies_from_description()` | Extract movie titles from timestamp lines |
| `fetch_vod_catalog()` | Get VOD catalog from Xtream API |
| `find_movie()` | Match movie title against VOD index |
| `generate_m3u_content()` | Build M3U with metadata tracking |
| `sync_playlists()` | Incremental update with URL validation |

## M3U Metadata Format

Generated playlists include metadata for state tracking:

```
# @playlist_meta
# @channel: @ChannelName
# @synced: 2024-01-01 12:00:00
# @videos_processed: vid1,vid2,vid3
# @entry_start
# @video: vid1 | Video Title
# @movie: Movie Name | year: 2020 | matched
# stream_id: 12345
#EXTINF:-1 tvg-name="Movie Name",Movie Name
http://server/movie/user/pass/12345.mp4
# @entry_end
```

States: `matched`, `unmatched`, `unavailable`

## IPTVX Integration

### Data Locations

```
~/Library/Containers/27DB3F00-3088-4A00-BCCF-C8F6CB49A29F/Data/
├── Documents/realm-db.realm           # Content database
└── Library/Application Support/IPTVX/
    └── CloudKit.sqlite                # Playlist configs (credentials here)
```

### Serving Playlists

```bash
# Start local server
python3 -m http.server 8080

# Add to IPTVX as M3U playlist:
# http://localhost:8080/playlists/channelname.m3u
```

## hide_broken.js

Node.js tool to hide broken streams in IPTVX's Realm database:

```bash
npm install                          # Install realm dependency
node hide_broken.js --list           # Show Realm schema
node hide_broken.js --hide 123,456   # Hide specific stream IDs
node hide_broken.js --from-m3u       # Hide unavailable from playlist
node hide_broken.js --dry-run        # Preview without changes
```

## Xtream Codes API

Stream URL pattern:
```
{server}/movie/{username}/{password}/{stream_id}.{ext}
```

API endpoints:
- `GET /player_api.php?username=X&password=Y&action=get_vod_streams`
- `GET /player_api.php?username=X&password=Y&action=get_vod_categories`

## Development

```bash
# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install pytest pytest-cov

# Run tests (requires 80% coverage)
pytest

# Run single test
pytest tests/test_generate_playlist.py::TestFindMovie -v
```

## Dependencies

- **Python 3.9+** (stdlib only, no pip packages for main tool)
- **yt-dlp** - YouTube scraping (`brew install yt-dlp`)
- **curl** - API requests (pre-installed on macOS)
- **Node.js + realm** - Only for hide_broken.js
