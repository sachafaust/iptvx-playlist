#!/usr/bin/env python3
"""
YouTube Movie Playlist Generator

Generates M3U playlists from YouTube movie recommendation channels.
Matches movies against IPTVX local data or Xtream Codes IPTV provider.

Credentials are automatically pulled from IPTVX app configuration.

Usage:
    python generate_playlist.py @Mooncut01           # Full rebuild for channel
    python generate_playlist.py --sync               # Incremental sync
    python generate_playlist.py --playlist xtreme    # Use specific IPTVX playlist
    python generate_playlist.py --playlists          # List available IPTVX playlists
"""

import argparse
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
IPTVX_REALM_PATH = Path.home() / "Library/Containers/com.mgapps.iptvx/Data/Documents/realm-db.realm"
IPTVX_SQLITE_PATH = Path.home() / "Library/Containers/com.mgapps.iptvx/Data/Library/Application Support/IPTVX/CloudKit.sqlite"

# Google Drive configuration (rclone remote name and default folder)
GDRIVE_FOLDER_ID = '10i-MdLuzIZCWzRtW5AjBlWJSi7f8JXl3'

DEFAULT_CONFIG = {
    "iptv_server": "",
    "iptv_username": "",
    "iptv_password": "",
    "output_dir": "playlists",
}

# M3U Metadata markers
META_HEADER = "# @playlist_meta"
META_CHANNEL = "# @channel:"
META_SYNCED = "# @synced:"
META_VIDEOS = "# @videos_processed:"
META_VIDEO = "# @video:"
META_MOVIE = "# @movie:"
META_ENTRY_START = "# @entry_start"
META_ENTRY_END = "# @entry_end"
META_SOURCE = "# @source:"


# =============================================================================
# Google Drive Integration (using rclone)
# =============================================================================

RCLONE_REMOTE = "gdrive"


def _check_rclone():
    """Check if rclone is installed and configured."""
    result = subprocess.run(['which', 'rclone'], capture_output=True)
    if result.returncode != 0:
        return False, "rclone not installed. Run: brew install rclone"

    # Check if gdrive remote exists
    result = subprocess.run(['rclone', 'listremotes'], capture_output=True, text=True)
    if f"{RCLONE_REMOTE}:" not in result.stdout:
        return False, f"rclone remote '{RCLONE_REMOTE}' not configured. Run: rclone config"

    return True, None


def setup_gdrive_oauth():
    """Set up rclone for Google Drive access.

    Returns:
        True if setup succeeded, False otherwise.
    """
    # Check if rclone is installed
    result = subprocess.run(['which', 'rclone'], capture_output=True)
    if result.returncode != 0:
        print("Error: rclone not installed.")
        print("Run: brew install rclone")
        return False

    print("Setting up rclone for Google Drive...")
    print(f"This will configure access to folder: {GDRIVE_FOLDER_ID}")
    print()

    # Run rclone config interactively
    cmd = [
        'rclone', 'config', 'create', RCLONE_REMOTE, 'drive',
        'scope', 'drive.file',
        'root_folder_id', GDRIVE_FOLDER_ID
    ]

    result = subprocess.run(cmd)
    if result.returncode == 0:
        print("\nGoogle Drive setup complete!")
        return True
    else:
        print("\nSetup failed. Try running: rclone config")
        return False


def upload_to_gdrive(file_path, folder_id=None):
    """Upload a file to Google Drive using rclone.

    Args:
        file_path: Path to the local file to upload.
        folder_id: Ignored (rclone uses configured root folder).

    Returns:
        dict with 'file_id' and 'url', or None on failure.
    """
    ok, err = _check_rclone()
    if not ok:
        print(f"Error: {err}")
        return None

    file_path = Path(file_path)
    file_name = file_path.name

    # Upload/overwrite file using rclone copy
    cmd = ['rclone', 'copy', str(file_path), f'{RCLONE_REMOTE}:']
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error uploading to Google Drive: {result.stderr}")
        return None

    # Get file ID using rclone lsjson
    cmd = ['rclone', 'lsjson', f'{RCLONE_REMOTE}:{file_name}']
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"Error getting file info: {result.stderr}")
        return None

    try:
        file_info = json.loads(result.stdout)
        if file_info:
            file_id = file_info[0].get('ID')
            return {
                'file_id': file_id,
                'url': f"https://drive.google.com/uc?export=download&id={file_id}"
            }
    except (json.JSONDecodeError, IndexError, KeyError):
        pass

    return None


def share_file_public(file_id):
    """Set file to 'anyone with link can view' using rclone.

    Args:
        file_id: Google Drive file ID.

    Returns:
        Direct download URL, or None on failure.
    """
    ok, err = _check_rclone()
    if not ok:
        print(f"Error: {err}")
        return None

    # Use rclone link to set "anyone with link" permissions
    # Note: 'rclone backend publiclink' doesn't work reliably — use 'rclone link' instead
    cmd = ['rclone', 'link', f'{RCLONE_REMOTE}:']
    # We need the filename, so look it up
    lsjson_cmd = ['rclone', 'lsjson', f'{RCLONE_REMOTE}:', '--files-only']
    lsjson_result = subprocess.run(lsjson_cmd, capture_output=True, text=True)
    filename = None
    if lsjson_result.returncode == 0:
        try:
            for item in json.loads(lsjson_result.stdout):
                if item.get('ID') == file_id:
                    filename = item.get('Name')
                    break
        except (json.JSONDecodeError, KeyError):
            pass

    if filename:
        cmd = ['rclone', 'link', f'{RCLONE_REMOTE}:{filename}']
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"Warning: Failed to share file publicly: {result.stderr.strip()}")
    else:
        print(f"Warning: Could not find file with ID {file_id} for sharing")

    return f"https://drive.google.com/uc?export=download&id={file_id}"


# =============================================================================
# IPTVX Database Integration
# =============================================================================

def add_playlist_to_iptvx(name, url):
    """Update existing M3U playlist URL in IPTVX database.

    Note: Only updates existing playlists. New playlists must be added
    manually via IPTVX UI because direct database insertion doesn't work
    reliably (missing CloudKit sync fields).

    Args:
        name: Playlist name (e.g., "@Mooncut01").
        url: Direct download URL.

    Returns:
        True if updated, False if playlist doesn't exist.
    """
    if not IPTVX_SQLITE_PATH.exists():
        return False

    try:
        with sqlite3.connect(str(IPTVX_SQLITE_PATH)) as conn:
            cursor = conn.cursor()

            # Only update existing playlists - don't create new ones
            cursor.execute(
                "SELECT Z_PK FROM ZPLAYLISTCONFIGURATION WHERE ZNAME = ?",
                (name,)
            )
            existing = cursor.fetchone()

            if existing:
                cursor.execute(
                    "UPDATE ZPLAYLISTCONFIGURATION SET ZURLSTRING = ? WHERE ZNAME = ?",
                    (url, name)
                )
                conn.commit()
                return True
            else:
                # New playlist - user must add manually via IPTVX UI
                return False

    except sqlite3.Error:
        return False


def upload_and_register(file_path, playlist_name, folder_id=None):
    """Upload playlist to Google Drive and update IPTVX if playlist exists.

    Args:
        file_path: Path to the M3U file.
        playlist_name: Name for the playlist in IPTVX.
        folder_id: Optional Google Drive folder ID.

    Returns:
        dict with 'uploaded', 'url', 'iptvx_updated', 'needs_manual_add'.
    """
    result = {'uploaded': False, 'url': None, 'iptvx_updated': False, 'needs_manual_add': False}

    # Upload to Google Drive
    upload_result = upload_to_gdrive(file_path, folder_id)
    if not upload_result:
        return result

    result['uploaded'] = True
    file_id = upload_result['file_id']

    # Share publicly
    url = share_file_public(file_id)
    if not url:
        return result

    result['url'] = url

    # Try to update existing IPTVX playlist
    if add_playlist_to_iptvx(playlist_name, url):
        result['iptvx_updated'] = True
    else:
        result['needs_manual_add'] = True

    return result


def get_iptvx_playlists():
    """Get playlists with Xtream credentials from IPTVX database."""
    if not IPTVX_SQLITE_PATH.exists():
        return []

    try:
        with sqlite3.connect(str(IPTVX_SQLITE_PATH)) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT ZNAME, ZURLSTRING, ZUSERNAME, ZPASSWORD
                FROM ZPLAYLISTCONFIGURATION
                WHERE ZUSERNAME IS NOT NULL AND ZUSERNAME <> ''
            """)
            rows = cursor.fetchall()

        playlists = []
        for name, url, username, password in rows:
            if url and username and password:
                # Normalize URL (remove trailing slash)
                url = url.rstrip("/")
                playlists.append({
                    "name": name,
                    "server": url,
                    "username": username,
                    "password": password,
                })
        return playlists
    except sqlite3.Error as e:
        print(f"Warning: Could not read IPTVX database: {e}")
        return []
    except Exception as e:
        print(f"Warning: Unexpected error reading IPTVX database: {e}")
        return []


def select_playlist(playlists, playlist_name=None):
    """Select a playlist from available options."""
    if not playlists:
        return None

    # If name specified, find it
    if playlist_name:
        for p in playlists:
            if p["name"].lower() == playlist_name.lower():
                return p
        print(f"Error: Playlist '{playlist_name}' not found.")
        print("Available playlists:")
        for p in playlists:
            print(f"  - {p['name']}")
        sys.exit(1)

    # If only one, use it
    if len(playlists) == 1:
        print(f"Using IPTVX playlist: {playlists[0]['name']}")
        return playlists[0]

    # Multiple playlists - prompt user
    print("Multiple IPTVX playlists with credentials found:")
    for i, p in enumerate(playlists, 1):
        print(f"  {i}. {p['name']} ({p['server']})")

    while True:
        try:
            choice = input("Select playlist number (or 'q' to quit): ").strip()
            if choice.lower() == 'q':
                sys.exit(0)
            idx = int(choice) - 1
            if 0 <= idx < len(playlists):
                return playlists[idx]
            print("Invalid selection.")
        except ValueError:
            print("Enter a number.")
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)


def load_configs(playlist_name=None):
    """Load IPTV configs from IPTVX database or environment.

    Returns a list of config dicts. When no --playlist flag is given and
    multiple IPTVX playlists exist, ALL are returned (no interactive prompt).
    With --playlist or env vars, returns a single-element list.

    Args:
        playlist_name: Optional name to select a single playlist.

    Returns:
        List of config dicts, each with iptv_server/username/password/output_dir.
    """
    # Check environment override first — always single source
    env_server = os.environ.get("IPTV_SERVER", "")
    env_user = os.environ.get("IPTV_USERNAME", "")
    env_pass = os.environ.get("IPTV_PASSWORD", "")
    if all([env_server, env_user, env_pass]):
        config = DEFAULT_CONFIG.copy()
        config["iptv_server"] = env_server
        config["iptv_username"] = env_user
        config["iptv_password"] = env_pass
        return [config]

    playlists = get_iptvx_playlists()

    if playlist_name:
        # Specific playlist requested — use select_playlist (may exit on error)
        selected = select_playlist(playlists, playlist_name)
        if selected:
            config = DEFAULT_CONFIG.copy()
            config["iptv_server"] = selected["server"]
            config["iptv_username"] = selected["username"]
            config["iptv_password"] = selected["password"]
            return [config]

    if playlists:
        # No specific playlist — use ALL available playlists
        configs = []
        for p in playlists:
            config = DEFAULT_CONFIG.copy()
            config["iptv_server"] = p["server"]
            config["iptv_username"] = p["username"]
            config["iptv_password"] = p["password"]
            config["_name"] = p["name"]
            configs.append(config)
        names = ", ".join(c["_name"] for c in configs)
        print(f"Using {len(configs)} IPTVX playlists: {names}")
        return configs

    print("Error: IPTV credentials not found.")
    print("Options:")
    print("  - Configure a playlist in IPTVX app")
    print("  - Set IPTV_SERVER, IPTV_USERNAME, IPTV_PASSWORD environment variables")
    sys.exit(1)


def load_config(playlist_name=None):
    """Load single IPTV config. Thin wrapper around load_configs() for backward compat."""
    configs = load_configs(playlist_name)
    return configs[0]


def run_command(cmd, timeout=300):
    """Run command and return output.

    Args:
        cmd: List of command arguments (not a shell string)
        timeout: Command timeout in seconds

    Returns:
        Tuple of (stdout, returncode)
    """
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        return "", 1
    except FileNotFoundError:
        return "", 1


def check_stream_url(url, timeout=10, retries=3):
    """Check if a stream URL is accessible and contains valid video data."""
    import urllib.request
    import time

    for attempt in range(retries + 1):
        try:
            # Create request with range header
            req = urllib.request.Request(url, headers={
                'Range': 'bytes=0-255',
                'User-Agent': 'Mozilla/5.0'
            })

            with urllib.request.urlopen(req, timeout=timeout) as response:
                header = response.read(256)

            if len(header) < 8:
                if attempt < retries:
                    time.sleep(0.3 * (attempt + 1))
                    continue
                return False

            # MP4/M4V: 'ftyp' at offset 4
            if header[4:8] == b'ftyp':
                return True

            # MKV/WebM: EBML header (0x1A45DFA3)
            if header[0:4] == b'\x1a\x45\xdf\xa3':
                return True

            # AVI: 'RIFF' + 'AVI '
            if header[0:4] == b'RIFF' and header[8:12] == b'AVI ':
                return True

            # MPEG-TS: sync byte 0x47
            if header[0] == 0x47:
                return True

            # FLV
            if header[0:3] == b'FLV':
                return True

            # MPEG-PS/VOB: pack start code
            if header[0:4] == b'\x00\x00\x01\xba':
                return True

            # Check if it's an HTML error page (common for 404s that return 200)
            if b'<html' in header.lower() or b'<!doctype' in header.lower():
                return False

            # Got data that's not HTML - likely valid video
            return len(header) >= 64

        except urllib.error.HTTPError as e:
            # 404, 502, etc - definitely broken
            if e.code in (404, 502, 503, 500):
                return False
            if attempt < retries:
                time.sleep(0.3 * (attempt + 1))
                continue
            return False

        except Exception:
            if attempt < retries:
                time.sleep(0.3 * (attempt + 1))
                continue
            return False

    return False


def fetch_channel_videos(channel_handle):
    """Fetch list of videos from YouTube channel."""
    print(f"  Fetching video list from YouTube...")
    cmd = [
        'yt-dlp', '--flat-playlist', '--print', '%(id)s|%(title)s',
        f'https://www.youtube.com/{channel_handle}/videos'
    ]
    output, code = run_command(cmd, timeout=60)

    if code != 0:
        return []

    videos = []
    for line in output.strip().split("\n"):
        if "|" in line:
            vid_id, title = line.split("|", 1)
            videos.append({"id": vid_id.strip(), "title": title.strip()})

    return videos


def fetch_video_description(video_id):
    """Fetch description for a single video."""
    cmd = [
        'yt-dlp', '--skip-download', '--print', 'description',
        f'https://www.youtube.com/watch?v={video_id}'
    ]
    output, code = run_command(cmd, timeout=30)
    return output if code == 0 else ""


def parse_movies_from_description(description):
    """Extract movie titles and years from timestamps."""
    movies = []
    skip_words = ["intro", "introduction", "outro", "conclusion", "subscribe", "thanks", "final thoughts"]

    for line in description.split("\n"):
        line = line.strip()
        match = re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", line)
        if not match:
            continue

        movie_text = match.group(2).strip()

        if any(sw in movie_text.lower() for sw in skip_words):
            continue

        year_match = re.search(r"\((\d{4})\)\s*$", movie_text)
        if year_match:
            year = year_match.group(1)
            movie_name = movie_text[:year_match.start()].strip()
        else:
            year = None
            movie_name = movie_text

        movie_name = re.sub(r"\s+", " ", movie_name).strip()

        if movie_name and len(movie_name) > 2:
            movies.append({"name": movie_name, "year": year})

    return movies


def _fetch_catalog(config, action, label):
    """Fetch a catalog from the Xtream Codes API.

    Args:
        config: IPTV config dict with server/username/password.
        action: API action (e.g. 'get_vod_streams', 'get_series').
        label: Human-readable label for log messages (e.g. 'VOD', 'series').

    Returns:
        List of catalog items, or empty list on failure.
    """
    print(f"Fetching {label} catalog from provider...")
    url = (f"{config['iptv_server']}/player_api.php?"
           f"username={config['iptv_username']}&password={config['iptv_password']}"
           f"&action={action}")

    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = ['curl', '-s', '--max-time', '180', '-o', tmp_path, url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=200)

        if result.returncode != 0:
            print(f"Error: Failed to fetch {label} catalog from provider.")
            return []

        with open(tmp_path, 'r') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} {label} items from provider")
        return data
    except json.JSONDecodeError:
        print(f"Error: {label.capitalize()} catalog response is not valid JSON.")
        return []
    except subprocess.TimeoutExpired:
        print(f"Error: {label.capitalize()} catalog fetch timed out.")
        return []
    except FileNotFoundError:
        print("Error: 'curl' command not found. Please install curl.")
        return []
    except Exception as e:
        print(f"Error fetching {label} catalog: {e}")
        return []
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def fetch_vod_catalog(config):
    """Fetch VOD catalog from provider API."""
    return _fetch_catalog(config, "get_vod_streams", "VOD")


def fetch_series_catalog(config):
    """Fetch series catalog from provider API."""
    return _fetch_catalog(config, "get_series", "series")


def _fetch_multi_catalog(configs, fetch_fn):
    """Fetch and merge catalogs from multiple providers.

    Each item is stamped with a _source dict so stream URLs use the correct credentials.
    """
    merged = []
    for config in configs:
        name = config.get("_name", config["iptv_server"])
        data = fetch_fn(config)
        for item in data:
            item["_source"] = {
                "name": name,
                "server": config["iptv_server"],
                "username": config["iptv_username"],
                "password": config["iptv_password"],
            }
        merged.extend(data)
    return merged


def fetch_multi_vod_catalog(configs):
    """Fetch and merge VOD catalogs from multiple providers."""
    return _fetch_multi_catalog(configs, fetch_vod_catalog)


def fetch_multi_series_catalog(configs):
    """Fetch and merge series catalogs from multiple providers."""
    return _fetch_multi_catalog(configs, fetch_series_catalog)


def fetch_series_episodes(config, series_id):
    """Fetch episode list for a series.

    Args:
        config: IPTV config dict.
        series_id: Series ID from the catalog.

    Returns:
        List of episode dicts with 'id', 'title', 'season', 'episode_num',
        'container_extension' keys.
    """
    url = (f"{config['iptv_server']}/player_api.php?"
           f"username={config['iptv_username']}&password={config['iptv_password']}"
           f"&action=get_series_info&series_id={series_id}")

    try:
        cmd = ['curl', '-s', '--max-time', '30', url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        if result.returncode != 0:
            return []

        data = json.loads(result.stdout)
        episodes = []
        for season_num, eps in data.get("episodes", {}).items():
            for ep in eps:
                episodes.append({
                    "id": ep.get("id"),
                    "title": ep.get("title", ""),
                    "season": int(ep.get("season", season_num)),
                    "episode_num": int(ep.get("episode_num", 0)),
                    "container_extension": ep.get("container_extension", "mp4"),
                })
        return episodes
    except Exception:
        return []


def build_series_stream_url(series_item, episode_id, ext="mp4"):
    """Build stream URL for a series episode.

    Args:
        series_item: Series dict (may contain _source with credentials).
        episode_id: Episode stream ID.
        ext: File extension.
    """
    source = series_item.get("_source", {})
    server = source.get("server", "")
    username = source.get("username", "")
    password = source.get("password", "")
    return f"{server}/series/{username}/{password}/{episode_id}.{ext}"


def build_vod_index(vod_data):
    """Build searchable index. First-wins dedup preserves priority order."""
    index = {}
    for item in vod_data:
        name = item.get("name")
        if name:
            key = name.lower()
            if key not in index:
                index[key] = item
    return index


def find_movie(title, year, vod_index):
    """Search for movie in VOD catalog."""
    title_lower = title.lower().strip()

    # Exact match with year
    if year:
        for name, item in vod_index.items():
            if title_lower in name and year in name:
                return item

    # Match with item's year field
    for name, item in vod_index.items():
        if title_lower in name:
            item_year = str(item.get("year", ""))
            if year and item_year == year:
                return item
            elif not year:
                return item

    # Partial match
    words = [w for w in title_lower.split() if len(w) > 2 and w not in ("the", "and", "for")]
    if len(words) >= 2:
        for name, item in vod_index.items():
            if all(w in name for w in words):
                return item

    return None


def build_stream_url(result, config=None):
    """Build stream URL from a VOD result, using embedded _source or fallback config.

    Args:
        result: VOD item dict (may contain _source with server credentials).
        config: Fallback config dict with iptv_server/username/password.

    Returns:
        Full stream URL string.
    """
    source = result.get("_source", {})
    server = source.get("server") or config["iptv_server"]
    username = source.get("username") or config["iptv_username"]
    password = source.get("password") or config["iptv_password"]
    stream_id = result.get("stream_id")
    ext = result.get("container_extension", "mp4")
    return f"{server}/movie/{username}/{password}/{stream_id}.{ext}"


def sanitize_filename(name):
    """Convert channel name to safe filename."""
    name = name.lstrip("@").lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


def parse_movie_file(filepath):
    """Parse a CSV file containing movie information.

    Expected columns: title, year (required), title_en (optional).
    All columns are preserved for use with --group-by.

    Args:
        filepath: Path to the CSV file.

    Returns:
        List of dicts with all CSV columns (values stripped, empty year → None).
    """
    filepath = Path(filepath)
    if not filepath.exists():
        print(f"Error: File not found: {filepath}")
        sys.exit(1)

    movies = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            entry = {k: v.strip() for k, v in row.items()}
            # Normalize start_year → year (for TV shows CSV)
            if "start_year" in entry and "year" not in entry:
                entry["year"] = entry["start_year"]
            if not entry.get("year", ""):
                entry["year"] = None
            title = entry.get("title", "")
            title_en = entry.get("title_en", "")
            if title or title_en:
                movies.append(entry)

    return movies


def movies_from_file(parsed_movies, playlist_name, group_by=None):
    """Convert parsed CSV movies to common movie entry format.

    Args:
        parsed_movies: List of dicts from parse_movie_file().
        playlist_name: Playlist name for group-title fallback.
        group_by: CSV column name or list of column names for group-title.
            Multiple columns create duplicate entries so each movie appears
            under each group independently (e.g. under '2024' AND 'Drame').

    Returns:
        List of common movie entry dicts.
    """
    if isinstance(group_by, str):
        group_by = [group_by]

    entries = []
    for movie in parsed_movies:
        title = movie.get("title", "")
        title_en = movie.get("title_en", "")
        # Use the first non-empty title column as display name
        name = title or title_en
        if not name:
            # Try any column ending in title_ prefix (title_kr, title_fr, etc.)
            for k, v in movie.items():
                if k.startswith("title_") and v:
                    name = v
                    break

        search_titles = []
        if title:
            search_titles.append(title)
        if title_en:
            search_titles.append(title_en)
        # Add any other title_* columns as search alternatives
        for k, v in movie.items():
            if k.startswith("title_") and k != "title_en" and v and v not in search_titles:
                search_titles.append(v)

        meta = []
        if title_en and title:
            meta.append(f"# @title_en: {title_en}")

        groups = []
        if group_by:
            groups = [movie[col] for col in group_by if movie.get(col)]
        if not groups:
            groups = [playlist_name]

        for group in groups:
            entries.append({
                "name": name,
                "year": movie.get("year"),
                "search_titles": search_titles,
                "group": group,
                "meta_lines": list(meta),
            })
    return entries


def movies_from_video_data(video_data):
    """Convert YouTube video_data to common movie entry format.

    Args:
        video_data: List of dicts from process_channel().

    Returns:
        List of common movie entry dicts.
    """
    entries = []
    for video in video_data:
        video_line = f"{META_VIDEO} {video['video_id']} | {video['title']}"
        for movie in video.get("movies", []):
            entries.append({
                "name": movie["name"],
                "year": movie.get("year"),
                "search_titles": [movie["name"]],
                "group": video["title"],
                "meta_lines": [video_line],
            })
    return entries


def preprocess_existing_entries(existing_metadata, vod_index):
    """Re-match existing M3U entries against current VOD catalog.

    Handles state transitions: matched->unavailable, unmatched->matched, etc.

    Args:
        existing_metadata: Dict from parse_m3u_metadata().
        vod_index: Current VOD search index.

    Returns:
        Dict of movie_key -> entry dicts with updated states.
    """
    entries = {}
    for entry in existing_metadata.get("entries", []):
        movie = entry.get("movie", {})
        movie_key = f"{movie.get('name', '')}|{movie.get('year', '')}"

        if movie_key in entries:
            continue

        result = find_movie(movie.get("name", ""), movie.get("year"), vod_index)

        meta_lines = []
        video = entry.get("video")
        if video:
            meta_lines.append(f"{META_VIDEO} {video['id']} | {video['title']}")

        group = video.get("title", "Movies") if video else "Movies"

        if result:
            if entry["state"] == "unavailable":
                state = "restored"
            elif entry["state"] == "unmatched":
                state = "new_matched"
            else:
                state = "matched"

            entries[movie_key] = {
                "movie": movie,
                "state": state,
                "result": result,
                "group": group,
                "meta_lines": meta_lines,
            }
        elif entry["state"] == "matched":
            entries[movie_key] = {
                "movie": movie,
                "state": "unavailable",
                "result": None,
                "group": group,
                "meta_lines": meta_lines,
                "prev_stream_id": entry.get("stream_id"),
            }
        else:
            entries[movie_key] = {
                "movie": movie,
                "state": "unmatched",
                "result": None,
                "group": group,
                "meta_lines": meta_lines,
            }

    return entries


def parse_m3u_metadata(filepath):
    """Parse existing M3U file to extract metadata."""
    if not filepath.exists():
        return None

    content = filepath.read_text()
    metadata = {
        "channel": None,
        "synced": None,
        "videos_processed": set(),
        "entries": [],  # List of movie entries with their state
    }

    lines = content.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.startswith(META_CHANNEL):
            metadata["channel"] = line[len(META_CHANNEL):].strip()
        elif line.startswith(META_SYNCED):
            metadata["synced"] = line[len(META_SYNCED):].strip()
        elif line.startswith(META_VIDEOS):
            vids = line[len(META_VIDEOS):].strip()
            if vids:
                metadata["videos_processed"] = set(vids.split(","))
        elif line.startswith(META_ENTRY_START):
            # Parse entry block
            entry = {"lines": [], "movie": None, "video": None, "state": None, "stream_id": None, "source": None}
            i += 1
            while i < len(lines) and not lines[i].strip().startswith(META_ENTRY_END):
                entry_line = lines[i]
                entry["lines"].append(entry_line)

                if entry_line.strip().startswith(META_VIDEO):
                    parts = entry_line.strip()[len(META_VIDEO):].strip().split("|")
                    if len(parts) >= 2:
                        entry["video"] = {"id": parts[0].strip(), "title": parts[1].strip()}
                elif entry_line.strip().startswith(META_MOVIE):
                    parts = entry_line.strip()[len(META_MOVIE):].strip().split("|")
                    movie_info = {}
                    for part in parts:
                        part = part.strip()
                        if ":" in part:
                            k, v = part.split(":", 1)
                            movie_info[k.strip()] = v.strip()
                        elif part in ("matched", "unmatched", "unavailable"):
                            movie_info["state"] = part
                        else:
                            movie_info["name"] = part
                    entry["movie"] = movie_info
                    entry["state"] = movie_info.get("state", "unmatched")
                elif entry_line.strip().startswith(META_SOURCE):
                    entry["source"] = entry_line.strip()[len(META_SOURCE):].strip()
                elif "stream_id:" in entry_line:
                    match = re.search(r"stream_id:\s*(\d+)", entry_line)
                    if match:
                        entry["stream_id"] = match.group(1)

                i += 1
            metadata["entries"].append(entry)
        i += 1

    return metadata


def generate_m3u(playlist_name, movies, config, vod_index,
                 existing_entries=None, validate_urls=False, workers=5,
                 header_lines=None):
    """Unified M3U generation pipeline.

    Takes movies from any source (YouTube, CSV, etc.), matches against VOD
    catalog, optionally validates stream URLs, and renders an M3U playlist.
    Same quality checks regardless of source.

    Args:
        playlist_name: Display name for the playlist.
        movies: List of common movie entry dicts:
            - name: Display name (required)
            - year: Year string or None
            - search_titles: Titles to try matching in order (defaults to [name])
            - group: M3U group-title (defaults to playlist_name)
            - meta_lines: Extra comment lines for the M3U entry
        config: Primary IPTV config (for stream URL building).
        vod_index: Pre-built VOD search index.
        existing_entries: Dict of movie_key -> entry dicts from
            preprocess_existing_entries() (sync mode).
        validate_urls: Whether to check stream accessibility.
        workers: Parallel workers for URL validation.
        header_lines: Extra lines for M3U header (after standard metadata).

    Returns:
        Tuple of (m3u_content_string, stats_dict).
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "#EXTM3U",
        META_HEADER,
        f"{META_SYNCED} {timestamp}",
    ]
    if header_lines:
        lines.extend(header_lines)
    lines.append("")

    stats = {"matched": 0, "unmatched": 0, "unavailable": 0,
             "restored": 0, "new_matched": 0, "broken": 0}
    entries_by_movie_key = {}

    # Merge pre-processed existing entries (sync mode)
    if existing_entries:
        for key, entry in existing_entries.items():
            entries_by_movie_key[key] = entry
            state = entry.get("state", "unmatched")
            if state == "restored":
                stats["restored"] += 1
                entry["state"] = "matched"
            elif state == "new_matched":
                stats["new_matched"] += 1
                entry["state"] = "matched"
            elif state == "matched":
                stats["matched"] += 1
            elif state == "unavailable":
                stats["unavailable"] += 1
            else:
                stats["unmatched"] += 1

    # Match new movies
    # Cache match results by movie identity to avoid redundant VOD lookups
    # when the same movie appears under multiple groups.
    match_cache = {}
    for movie in movies:
        name = movie["name"]
        year = movie.get("year")
        group = movie.get("group", playlist_name)
        movie_key = f"{name}|{year or ''}|{group}"

        if movie_key in entries_by_movie_key:
            continue

        # Reuse match result if we already looked up this movie
        identity = f"{name}|{year or ''}"
        if identity in match_cache:
            result = match_cache[identity]
        else:
            search_titles = movie.get("search_titles", [name])
            result = None
            for title in search_titles:
                if title:
                    result = find_movie(title, year, vod_index)
                if result:
                    break
            match_cache[identity] = result

        state = "matched" if result else "unmatched"
        stats[state] += 1

        entries_by_movie_key[movie_key] = {
            "movie": {"name": name, "year": year},
            "state": state,
            "result": result,
            "group": movie.get("group", playlist_name),
            "meta_lines": movie.get("meta_lines", []),
        }

    # Validate URLs (parallelized)
    if validate_urls:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        matched_entries = [(k, e) for k, e in entries_by_movie_key.items()
                          if e["state"] == "matched" and e.get("result")]

        # Deduplicate by stream_id so the same URL isn't checked twice
        # (happens when a movie appears under multiple groups)
        seen_streams = {}  # stream_id -> list of (movie_key, entry)
        for k, e in matched_entries:
            sid = e["result"].get("stream_id")
            seen_streams.setdefault(sid, []).append((k, e))
        unique_checks = [(entries[0][0], entries[0][1]) for entries in seen_streams.values()]

        total = len(unique_checks)
        if total > 0:
            print(f"  Validating {total} unique stream URLs (parallel)...")

            def check_entry(movie_key, entry):
                result = entry["result"]
                stream_id = result.get("stream_id")
                url = build_stream_url(result, config)
                is_valid = check_stream_url(url)
                return movie_key, entry, stream_id, is_valid

            completed = [0]
            broken_count = [0]
            lock = threading.Lock()

            def update_progress():
                with lock:
                    pct = (completed[0] / total) * 100
                    sys.stdout.write(f"\r  [{completed[0]}/{total}] {pct:.0f}% complete, {broken_count[0]} broken")
                    sys.stdout.flush()

            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(check_entry, k, e): (k, e)
                          for k, e in unique_checks}

                for future in as_completed(futures):
                    movie_key, entry, stream_id, is_valid = future.result()
                    completed[0] += 1

                    if not is_valid:
                        # Mark ALL entries sharing this stream_id as broken
                        # but only count once per unique stream in stats
                        entries_for_stream = seen_streams.get(stream_id, [])
                        stats["broken"] += 1
                        stats["matched"] -= len(entries_for_stream)
                        for mk, me in entries_for_stream:
                            me["state"] = "unavailable"
                            me["prev_stream_id"] = stream_id
                            me["result"] = None
                        broken_count[0] += 1

                    if completed[0] % 10 == 0 or completed[0] == total:
                        update_progress()

            print()  # newline after progress

    # Render entry blocks
    for movie_key, entry in entries_by_movie_key.items():
        movie = entry["movie"]
        state = entry["state"]
        result = entry.get("result")
        group = entry.get("group", playlist_name)
        meta_lines = entry.get("meta_lines", [])

        movie_name = movie.get("name", "Unknown")
        year_str = f" | year: {movie['year']}" if movie.get("year") else ""

        if not movie_name or movie_name == "Unknown":
            continue

        lines.append(META_ENTRY_START)
        for ml in meta_lines:
            lines.append(ml)
        lines.append(f"{META_MOVIE} {movie_name}{year_str} | {state}")

        if state == "matched" and result:
            stream_id = result.get("stream_id")
            icon = result.get("stream_icon", "")
            name = result.get("name", movie_name)
            url = build_stream_url(result, config)

            source_name = result.get("_source", {}).get("name", "")
            if source_name:
                lines.append(f"{META_SOURCE} {source_name}")

            lines.append(f"# stream_id: {stream_id}")
            lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{name}" tvg-logo="{icon}" group-title="{group}",{name}')
            lines.append(url)

        elif state == "unavailable":
            prev_id = entry.get("prev_stream_id", "unknown")
            lines.append(f"# stream_id: {prev_id} (unavailable since {timestamp})")
            lines.append(f'# #EXTINF:-1 group-title="{group}",{movie_name}')
            lines.append(f"# (stream unavailable)")

        lines.append(META_ENTRY_END)
        lines.append("")

    return "\n".join(lines), stats


def process_channel(channel_handle, config, vod_index, existing_metadata=None, new_videos_only=False):
    """Process a single YouTube channel and return video data."""
    print(f"\nProcessing {channel_handle}...")

    videos = fetch_channel_videos(channel_handle)
    if not videos:
        print(f"  No videos found")
        return []

    # Filter to new videos only if syncing
    if new_videos_only and existing_metadata:
        processed = existing_metadata["videos_processed"]
        videos = [v for v in videos if v["id"] not in processed]
        if not videos:
            print(f"  No new videos to process")
            return []
        print(f"  Found {len(videos)} new videos")
    else:
        print(f"  Found {len(videos)} videos")

    print(f"  Fetching descriptions...")
    video_data = []

    for i, video in enumerate(videos, 1):
        sys.stdout.write(f"\r  [{i}/{len(videos)}] {video['title'][:50]:<50}")
        sys.stdout.flush()

        desc = fetch_video_description(video["id"])
        movies = parse_movies_from_description(desc)

        if movies:
            video_data.append({
                "video_id": video["id"],
                "title": video["title"],
                "movies": movies,
            })

    print()  # newline after progress
    total = sum(len(v["movies"]) for v in video_data)
    print(f"  Parsed {total} movies from {len(video_data)} videos with timestamps")

    return video_data


def sync_playlists(configs, workers=5, upload=False, gdrive_folder=None):
    """Sync all existing playlists with current catalog.

    Args:
        configs: List of config dicts (or single config dict for backward compat).
    """
    # Accept single config for backward compat
    if isinstance(configs, dict):
        configs = [configs]

    config = configs[0]  # Primary config for output_dir and fallback
    output_dir = SCRIPT_DIR / config["output_dir"]
    if not output_dir.exists():
        print("No playlists directory found. Run a full build first.")
        return

    # Fetch merged VOD catalog from all sources
    vod_data = fetch_multi_vod_catalog(configs)
    if not vod_data:
        print("Failed to fetch VOD catalog")
        return
    vod_index = build_vod_index(vod_data)
    print(f"VOD catalog: {len(vod_index)} unique items from {len(configs)} source(s)")

    # Find all M3U files
    m3u_files = list(output_dir.glob("*.m3u"))
    if not m3u_files:
        print("No M3U files found to sync")
        return

    for m3u_file in m3u_files:
        print(f"\nSyncing {m3u_file.name}...")
        metadata = parse_m3u_metadata(m3u_file)

        if not metadata or not metadata["channel"]:
            print(f"  Skipping - no metadata found")
            continue

        channel_handle = metadata["channel"]

        # Check for new videos
        video_data = process_channel(channel_handle, config, vod_index, metadata, new_videos_only=True)

        # Pre-process existing entries and convert new videos to common format
        existing_entries = preprocess_existing_entries(metadata, vod_index)
        new_movies = movies_from_video_data(video_data)

        # Compute processed video IDs for header
        processed_videos = metadata["videos_processed"].copy()
        for video in video_data:
            processed_videos.add(video["video_id"])

        header = [
            f"{META_CHANNEL} {channel_handle}",
            f"{META_VIDEOS} {','.join(sorted(processed_videos))}",
            f"# Source: https://www.youtube.com/{channel_handle}",
        ]

        # Generate updated M3U with URL validation
        m3u_content, stats = generate_m3u(
            channel_handle, new_movies, config, vod_index,
            existing_entries=existing_entries,
            validate_urls=True, workers=workers,
            header_lines=header,
        )
        m3u_file.write_text(m3u_content)

        print(f"  Results:")
        print(f"    Matched: {stats['matched']}")
        if stats["new_matched"]:
            print(f"    Newly matched: {stats['new_matched']}")
        if stats["restored"]:
            print(f"    Restored: {stats['restored']}")
        if stats["broken"]:
            print(f"    Broken streams: {stats['broken']}")
        if stats["unavailable"]:
            print(f"    Unavailable: {stats['unavailable']}")
        print(f"    Unmatched: {stats['unmatched']}")

        # Upload to Google Drive if requested
        if upload:
            playlist_name = channel_handle if channel_handle.startswith("@") else f"@{channel_handle}"
            result = upload_and_register(m3u_file, playlist_name, gdrive_folder)
            if result['uploaded']:
                print(f"    Uploaded: {result['url']}")
                if result['iptvx_updated']:
                    print(f"    Updated in IPTVX: {playlist_name}")
                elif result['needs_manual_add']:
                    print(f"    Add to IPTVX manually: {playlist_name}")
            else:
                print(f"    Upload failed")


def list_iptvx_playlists():
    """List available IPTVX playlists with Xtream credentials."""
    playlists = get_iptvx_playlists()
    if not playlists:
        print("No IPTVX playlists with Xtream credentials found.")
        print("Configure a playlist in IPTVX app with username/password.")
        return None

    print("Available IPTVX playlists:")
    for i, p in enumerate(playlists, 1):
        print(f"  {i}. {p['name']} ({p['server']})")

    # Allow selection
    if len(playlists) == 1:
        return playlists[0]

    while True:
        try:
            choice = input("\nSelect playlist number (or 'q' to quit): ").strip()
            if choice.lower() == 'q':
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(playlists):
                return playlists[idx]
            print("Invalid selection.")
        except ValueError:
            print("Enter a number.")
        except (EOFError, KeyboardInterrupt):
            print()
            return None


def generate_series_m3u(playlist_name, movie_entries, series_index, config, workers=5):
    """Generate M3U lines for series content.

    Matches titles against series catalog, fetches episodes in parallel,
    and returns M3U entry lines with stats.

    Args:
        playlist_name: Playlist name for metadata.
        movie_entries: Common-format entries from movies_from_file().
        series_index: Pre-built series search index.
        config: Primary IPTV config for fallback credentials.
        workers: Parallel workers for episode fetching.

    Returns:
        Tuple of (lines_list, matched_count, total_episodes, total_input).
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    # Match titles, collecting all groups per series
    match_cache = {}
    series_matches = {}
    for entry in movie_entries:
        identity = f"{entry['name']}|{entry.get('year', '')}"
        if identity in match_cache:
            result = match_cache[identity]
        else:
            result = None
            for title in entry.get("search_titles", [entry["name"]]):
                if title:
                    result = find_movie(title, entry.get("year"), series_index)
                    if result:
                        break
            match_cache[identity] = result
        if result:
            if identity not in series_matches:
                series_matches[identity] = {"result": result, "groups": []}
            series_matches[identity]["groups"].append(entry["group"])

    matched_series = list(series_matches.values())
    print(f"Matched {len(matched_series)} series, fetching episodes...")

    # Fetch episodes in parallel
    def _fetch_eps(match):
        series = match["result"]
        source = series.get("_source", {})
        series_config = {
            "iptv_server": source.get("server", config["iptv_server"]),
            "iptv_username": source.get("username", config["iptv_username"]),
            "iptv_password": source.get("password", config["iptv_password"]),
        }
        series_id = series.get("series_id")
        return match, fetch_series_episodes(series_config, series_id)

    episode_results = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_fetch_eps, m): m for m in matched_series}
        for i, future in enumerate(as_completed(futures), 1):
            match, episodes = future.result()
            series_name = match["result"].get("title") or match["result"].get("name", "Unknown")
            sys.stdout.write(f"\r  [{i}/{len(matched_series)}] {series_name[:50]:<50}")
            sys.stdout.flush()
            if episodes:
                episode_results.append((match, episodes))

    # Render M3U entry lines
    lines = []
    total_episodes = 0
    for match, episodes in episode_results:
        series = match["result"]
        groups = match["groups"]
        source = series.get("_source", {})
        series_name = series.get("title") or series.get("name", "Unknown")
        cover = series.get("cover", "")

        for group in groups:
            for ep in episodes:
                ep_name = f"{series_name} S{ep['season']:02d}E{ep['episode_num']:02d}"
                if ep.get("title"):
                    ep_name += f" - {ep['title']}"
                url = build_series_stream_url(series, ep["id"], ep.get("container_extension", "mp4"))

                lines.append(META_ENTRY_START)
                lines.append(f"{META_MOVIE} {series_name} | year: {series.get('year', 'unknown')} | matched")
                source_name = source.get("name", "")
                if source_name:
                    lines.append(f"{META_SOURCE} {source_name}")
                lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{ep_name}" tvg-logo="{cover}" group-title="{group}",{ep_name}')
                lines.append(url)
                lines.append(META_ENTRY_END)
                lines.append("")
                total_episodes += 1

    print(f"\n  Found {total_episodes} episodes from {len(matched_series)} series")
    return lines, len(matched_series), total_episodes


def main():
    parser = argparse.ArgumentParser(
        description="Generate M3U playlists from YouTube movie recommendation channels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_playlist.py @Mooncut01           # Full rebuild for channel
  python generate_playlist.py --sync               # Sync all playlists
  python generate_playlist.py --playlist xtreme    # Use specific IPTVX playlist
  python generate_playlist.py --playlists          # List IPTVX playlists
  python generate_playlist.py --from-file movies.csv --name "My Movies"  # From CSV file
        """
    )
    parser.add_argument("--version", "-V", action="version", version="%(prog)s 0.1.0")
    parser.add_argument("channels", nargs="*", help="YouTube channel handles (e.g., @Mooncut01)")
    parser.add_argument("--playlist", "-p", metavar="NAME", help="IPTVX playlist to use for credentials")
    parser.add_argument("--playlists", action="store_true", help="List available IPTVX playlists")
    parser.add_argument("--sync", action="store_true", help="Sync existing playlists with current catalog")
    parser.add_argument("--workers", type=int, default=5, help="Parallel workers for URL validation (default: 5)")
    parser.add_argument("-o", "--output", help="Output file (for single channel)")
    parser.add_argument("--from-file", metavar="FILE", help="CSV file with movie titles to search (columns: title, title_en, year)")
    parser.add_argument("--name", metavar="NAME", help="Playlist name (required with --from-file)")
    parser.add_argument("--group-by", nargs="+", metavar="COLUMN", help="CSV column(s) to group entries by (e.g. year genre)")
    parser.add_argument("--type", choices=["movie", "series"], default="movie", help="Content type to search (default: movie)")
    parser.add_argument("--append", action="store_true", help="Append to existing playlist instead of replacing")
    parser.add_argument("--upload", action="store_true", help="Upload to Google Drive, share publicly, add to IPTVX")
    parser.add_argument("--setup-gdrive", action="store_true", help="Set up Google Drive OAuth authentication")
    parser.add_argument("--gdrive-folder", metavar="ID", help="Google Drive folder ID (default: iptvx-playlists)")
    args = parser.parse_args()

    # Handle --setup-gdrive
    if args.setup_gdrive:
        success = setup_gdrive_oauth()
        sys.exit(0 if success else 1)

    # Handle --playlists
    if args.playlists:
        selected = list_iptvx_playlists()
        if selected:
            print(f"\nSelected: {selected['name']}")
            print(f"  Server: {selected['server']}")
            print(f"  Username: {selected['username']}")
        return

    # Load configs (multi-credential)
    configs = load_configs(args.playlist)
    config = configs[0]  # Primary config for output_dir and fallback

    # Handle --sync
    if args.sync:
        sync_playlists(configs, workers=args.workers, upload=args.upload, gdrive_folder=args.gdrive_folder)
        return

    # Handle --from-file
    if args.from_file:
        if not args.name:
            print("Error: --name is required with --from-file")
            sys.exit(1)

        parsed = parse_movie_file(args.from_file)
        content_type = args.type
        print(f"Loaded {len(parsed)} titles from {args.from_file} (type: {content_type})")

        output_dir = SCRIPT_DIR / config["output_dir"]
        output_dir.mkdir(exist_ok=True)
        output_file = Path(args.output) if args.output else output_dir / f"{sanitize_filename(args.name)}.m3u"

        if content_type == "series":
            # Fetch series catalog
            series_data = fetch_multi_series_catalog(configs)
            if not series_data:
                sys.exit(1)
            series_index = build_vod_index(series_data)
            print(f"Series catalog: {len(series_index)} unique items from {len(configs)} source(s)")

            movie_entries = movies_from_file(parsed, args.name, group_by=args.group_by)
            series_lines, matched_count, total_episodes = generate_series_m3u(
                args.name, movie_entries, series_index, config, workers=args.workers,
            )

            # Append or create
            if args.append and output_file.exists():
                existing = output_file.read_text().rstrip("\n")
                new_content = existing + "\n" + "\n".join(series_lines)
            else:
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                header = [
                    "#EXTM3U",
                    META_HEADER,
                    f"{META_SYNCED} {timestamp}",
                    f"# @name: {args.name}",
                    f"# @source_type: file",
                    "",
                ]
                new_content = "\n".join(header + series_lines)

            output_file.write_text(new_content)
            print(f"\nResults for '{args.name}' (series):")
            print(f"  Matched: {matched_count} / {len(parsed)} shows")
            print(f"  Episodes: {total_episodes}")
            print(f"  Output: {output_file}")

        else:
            # Movie flow
            vod_data = fetch_multi_vod_catalog(configs)
            if not vod_data:
                sys.exit(1)
            vod_index = build_vod_index(vod_data)
            print(f"VOD catalog: {len(vod_index)} unique items from {len(configs)} source(s)")

            movie_entries = movies_from_file(parsed, args.name, group_by=args.group_by)
            header = [
                f"# @name: {args.name}",
                f"# @source_type: file",
            ]
            m3u_content, stats = generate_m3u(
                args.name, movie_entries, config, vod_index,
                validate_urls=True, workers=args.workers,
                header_lines=header,
            )

            if args.append and output_file.exists():
                existing = output_file.read_text().rstrip("\n")
                # Extract just the entry blocks from new content (skip header)
                new_lines = m3u_content.split("\n")
                entry_start = next((i for i, l in enumerate(new_lines) if l.strip() == META_ENTRY_START), len(new_lines))
                new_content = existing + "\n" + "\n".join(new_lines[entry_start:])
            else:
                new_content = m3u_content

            output_file.write_text(new_content)

            print(f"\nResults for '{args.name}':")
            print(f"  Matched: {stats['matched']} / {len(parsed)} movies")
            print(f"  Unmatched: {stats['unmatched']} movies")
            if stats["broken"]:
                print(f"  Broken streams: {stats['broken']}")
            print(f"  Output: {output_file}")

        if args.upload:
            result = upload_and_register(output_file, args.name, args.gdrive_folder)
            if result['uploaded']:
                print(f"  Uploaded: {result['url']}")
                if result['iptvx_updated']:
                    print(f"  Updated in IPTVX: {args.name}")
                elif result['needs_manual_add']:
                    print(f"  Add to IPTVX manually: {args.name}")
            else:
                print(f"  Upload failed")
        return

    # Full rebuild mode - require channel argument
    if not args.channels:
        parser.print_help()
        sys.exit(1)

    channels = [{"handle": ch if ch.startswith("@") else f"@{ch}", "name": ch.lstrip("@")} for ch in args.channels]

    # Fetch merged VOD catalog from all sources
    vod_data = fetch_multi_vod_catalog(configs)
    if not vod_data:
        sys.exit(1)
    vod_index = build_vod_index(vod_data)
    print(f"VOD catalog: {len(vod_index)} unique items from {len(configs)} source(s)")

    # Create output directory
    output_dir = SCRIPT_DIR / config["output_dir"]
    output_dir.mkdir(exist_ok=True)

    # Process channels
    for channel in channels:
        video_data = process_channel(channel["handle"], config, vod_index)

        if video_data:
            movie_entries = movies_from_video_data(video_data)
            processed_videos = {v["video_id"] for v in video_data}
            header = [
                f"{META_CHANNEL} {channel['handle']}",
                f"{META_VIDEOS} {','.join(sorted(processed_videos))}",
                f"# Source: https://www.youtube.com/{channel['handle']}",
            ]
            m3u_content, stats = generate_m3u(
                channel["handle"], movie_entries, config, vod_index,
                header_lines=header,
            )

            if args.output and len(channels) == 1:
                output_file = Path(args.output)
            else:
                output_file = output_dir / f"{sanitize_filename(channel['handle'])}.m3u"

            output_file.write_text(m3u_content)

            print(f"\nResults for {channel['handle']}:")
            print(f"  Matched: {stats['matched']} movies")
            print(f"  Unmatched: {stats['unmatched']} movies")
            print(f"  Output: {output_file}")

            # Upload to Google Drive if requested
            if args.upload:
                playlist_name = channel["handle"]  # Already has @ prefix
                folder_id = args.gdrive_folder
                result = upload_and_register(output_file, playlist_name, folder_id)
                if result['uploaded']:
                    print(f"  Uploaded: {result['url']}")
                    if result['iptvx_updated']:
                        print(f"  Updated in IPTVX: {playlist_name}")
                    elif result['needs_manual_add']:
                        print(f"  Add to IPTVX manually: {playlist_name}")
                else:
                    print(f"  Upload failed")


if __name__ == "__main__":
    main()
