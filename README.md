# IPTVX Playlist Generator

Generate M3U playlists from YouTube movie recommendation channels for use with IPTVX or any M3U-compatible player.

## Overview

This tool:
1. Scrapes movie lists from YouTube channels (via video descriptions with timestamps)
2. Matches movies against your IPTV provider's VOD catalog
3. Generates M3U playlists with working stream URLs
4. Tracks state to enable incremental syncing

## Requirements

- Python 3.9+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) - for YouTube scraping
- [curl](https://curl.se/) - for API requests
- IPTV provider with Xtream Codes API (or IPTVX app with configured playlist)

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd iptvx-playlist

# Install yt-dlp (macOS)
brew install yt-dlp

# Or via pip
pip install yt-dlp
```

No Python dependencies required beyond the standard library.

## Usage

### Basic Commands

```bash
# Generate playlist for a YouTube channel
python generate_playlist.py @Mooncut01

# Sync all existing playlists (incremental, validates URLs)
python generate_playlist.py --sync

# List available IPTVX playlists
python generate_playlist.py --playlists

# Use a specific IPTVX playlist for credentials
python generate_playlist.py @Mooncut01 --playlist xtreme

# Show version
python generate_playlist.py --version
```

### CLI Options

```
positional arguments:
  channels              YouTube channel handles (e.g., @Mooncut01)

options:
  -h, --help            Show help message
  -V, --version         Show version
  --playlist, -p NAME   IPTVX playlist to use for credentials
  --playlists           List available IPTVX playlists
  --sync                Sync existing playlists with current catalog
  --workers N           Parallel workers for URL validation (default: 5)
  -o, --output FILE     Output file path (single channel only)
```

### Credential Sources

The tool looks for IPTV credentials in this order:

1. **IPTVX App** (automatic): Reads from IPTVX's SQLite database if installed
2. **Environment variables** (override):
   ```bash
   export IPTV_SERVER="http://your-provider.com"
   export IPTV_USERNAME="your_username"
   export IPTV_PASSWORD="your_password"
   ```

## Output

Playlists are saved to `./playlists/` directory:

```
playlists/
  mooncut01.m3u      # Channel playlist with metadata
```

### Using with IPTVX

1. Start a local HTTP server:
   ```bash
   python3 -m http.server 8080
   ```

2. In IPTVX, add a new M3U playlist:
   ```
   http://localhost:8080/playlists/mooncut01.m3u
   ```

## How It Works

1. **Fetch channel videos** - Lists all videos from a YouTube channel
2. **Parse descriptions** - Extracts movie titles and years from timestamp lines
3. **Match against VOD catalog** - Searches your provider's catalog for matches
4. **Generate M3U** - Creates playlist with stream URLs and metadata
5. **Track state** - Stores processed video IDs and match status for sync mode

### Sync Mode

The `--sync` flag enables incremental updates:
- Only fetches descriptions for new videos
- Re-validates all matched URLs (parallel, configurable workers)
- Tracks movies that become unavailable or return to catalog
- Updates match status when catalog changes

## Supported YouTube Channels

Any channel that lists movies with timestamps in video descriptions works. Example format:

```
0:00 Intro
1:23 The Thing (1982)
5:45 Alien (1979)
10:00 Blade Runner (1982)
```

## Development

### Running Tests

```bash
# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install test dependencies
pip install pytest pytest-cov

# Run tests with coverage
pytest
```

Tests require 80% coverage to pass (configured in `pyproject.toml`).

## License

MIT
