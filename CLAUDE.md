# IPTVX Playlist Generator

Generate M3U playlists from YouTube movie recommendation channels, matched against an IPTV provider's VOD catalog.

## Project Structure

```
iptvx-playlist/
├── generate_playlist.py   # Main CLI tool
├── tests/                 # pytest unit tests (86% coverage)
├── playlists/             # Generated M3U output (gitignored)
├── pyproject.toml         # Project config, pytest settings
└── .venv/                 # Python virtual environment
```

## CLI Usage

```bash
# Generate playlist for a YouTube channel
python generate_playlist.py @ChannelName

# Generate and upload to Google Drive + add to IPTVX
python generate_playlist.py @ChannelName --upload

# Sync existing playlists (validates URLs, finds new videos)
python generate_playlist.py --sync

# Sync and upload all changed playlists
python generate_playlist.py --sync --upload

# List available IPTVX playlists
python generate_playlist.py --playlists

# Use specific IPTVX playlist for credentials
python generate_playlist.py @Channel --playlist "playlist-name"

# Parallel workers for URL validation (default: 5)
python generate_playlist.py --sync --workers 10

# Set up Google Drive authentication (one-time)
python generate_playlist.py --setup-gdrive
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
| `upload_to_gdrive()` | Upload M3U file to Google Drive |
| `share_file_public()` | Share file with "anyone with link" |
| `add_playlist_to_iptvx()` | Add/update M3U playlist in IPTVX database |

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

### Google Drive Upload (Recommended)

Automatically upload playlists to Google Drive and register them in IPTVX:

**One-time setup:**
1. Install rclone: `brew install rclone`
2. Configure Google Drive remote:
   ```bash
   rclone config create gdrive drive scope drive root_folder_id 10i-MdLuzIZCWzRtW5AjBlWJSi7f8JXl3
   ```
3. Follow the OAuth flow in browser when prompted

**Usage:**
```bash
# Upload + share + add to IPTVX in one command
python3 generate_playlist.py @ChannelName --upload

# Sync all and upload
python3 generate_playlist.py --sync --upload
```

Files are uploaded to the configured Google Drive folder and shared with "anyone with link" permission. The URL is automatically added to IPTVX.

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

### Optional: Google Drive Upload

```bash
brew install rclone
```

Configure with: `rclone config create gdrive drive scope drive root_folder_id <FOLDER_ID>`
