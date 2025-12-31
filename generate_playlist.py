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
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
IPTVX_REALM_PATH = Path.home() / "Library/Containers/27DB3F00-3088-4A00-BCCF-C8F6CB49A29F/Data/Documents/realm-db.realm"
IPTVX_SQLITE_PATH = Path.home() / "Library/Containers/27DB3F00-3088-4A00-BCCF-C8F6CB49A29F/Data/Library/Application Support/IPTVX/CloudKit.sqlite"

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


def load_config(playlist_name=None):
    """Load IPTV config from IPTVX database or environment."""
    config = DEFAULT_CONFIG.copy()

    # Try IPTVX database first
    playlists = get_iptvx_playlists()
    if playlists:
        selected = select_playlist(playlists, playlist_name)
        if selected:
            config["iptv_server"] = selected["server"]
            config["iptv_username"] = selected["username"]
            config["iptv_password"] = selected["password"]

    # Override from environment
    for key in ["iptv_server", "iptv_username", "iptv_password"]:
        env_key = key.upper()
        if os.environ.get(env_key):
            config[key] = os.environ[env_key]

    if not all([config["iptv_server"], config["iptv_username"], config["iptv_password"]]):
        print("Error: IPTV credentials not found.")
        print("Options:")
        print("  - Configure a playlist in IPTVX app")
        print("  - Set IPTV_SERVER, IPTV_USERNAME, IPTV_PASSWORD environment variables")
        sys.exit(1)

    return config


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


def fetch_vod_catalog(config):
    """Fetch VOD catalog from provider API."""
    print("Fetching VOD catalog from provider...")
    url = f"{config['iptv_server']}/player_api.php?username={config['iptv_username']}&password={config['iptv_password']}&action=get_vod_streams"

    # Use temp file for large response
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        cmd = ['curl', '-s', '--max-time', '180', '-o', tmp_path, url]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=200)

        if result.returncode != 0:
            print("Error: Failed to fetch VOD catalog from provider.")
            print(f"  Check your network connection and verify the server is accessible.")
            return []

        with open(tmp_path, 'r') as f:
            data = json.load(f)

        print(f"Loaded {len(data)} VOD items from provider")
        return data
    except json.JSONDecodeError:
        print("Error: VOD catalog response is not valid JSON.")
        print("  The server may be down or credentials may be incorrect.")
        return []
    except subprocess.TimeoutExpired:
        print("Error: VOD catalog fetch timed out after 3 minutes.")
        return []
    except FileNotFoundError:
        print("Error: 'curl' command not found. Please install curl.")
        return []
    except Exception as e:
        print(f"Error fetching VOD catalog: {e}")
        return []
    finally:
        # Clean up temp file
        import os
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def build_vod_index(vod_data):
    """Build searchable index."""
    index = {}
    for item in vod_data:
        name = item.get("name")
        if name:
            index[name.lower()] = item
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


def sanitize_filename(name):
    """Convert channel name to safe filename."""
    name = name.lstrip("@").lower()
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


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
            entry = {"lines": [], "movie": None, "video": None, "state": None, "stream_id": None}
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
                elif "stream_id:" in entry_line:
                    match = re.search(r"stream_id:\s*(\d+)", entry_line)
                    if match:
                        entry["stream_id"] = match.group(1)

                i += 1
            metadata["entries"].append(entry)
        i += 1

    return metadata


def generate_m3u_content(channel_handle, video_data, config, vod_index, existing_metadata=None, validate_urls=False, workers=5):
    """Generate M3U playlist content with metadata."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Collect all processed video IDs
    processed_videos = set()
    if existing_metadata:
        processed_videos = existing_metadata["videos_processed"].copy()
    for video in video_data:
        processed_videos.add(video["video_id"])

    lines = [
        "#EXTM3U",
        META_HEADER,
        f"{META_CHANNEL} {channel_handle}",
        f"{META_SYNCED} {timestamp}",
        f"{META_VIDEOS} {','.join(sorted(processed_videos))}",
        f"# Source: https://www.youtube.com/{channel_handle}",
        "",
    ]

    stats = {"matched": 0, "unmatched": 0, "unavailable": 0, "restored": 0, "new_matched": 0, "broken": 0}
    entries_by_movie_key = {}  # Track entries to avoid duplicates
    urls_to_validate = []  # Collect URLs for batch validation

    # Process existing entries first (for sync mode)
    if existing_metadata:
        for entry in existing_metadata["entries"]:
            movie = entry.get("movie", {})
            movie_key = f"{movie.get('name', '')}|{movie.get('year', '')}"

            if movie_key in entries_by_movie_key:
                continue

            # Try to re-match
            result = find_movie(movie.get("name", ""), movie.get("year"), vod_index)

            if result:
                if entry["state"] == "unavailable":
                    stats["restored"] += 1
                    new_state = "matched"
                elif entry["state"] == "unmatched":
                    stats["new_matched"] += 1
                    new_state = "matched"
                else:
                    stats["matched"] += 1
                    new_state = "matched"

                entries_by_movie_key[movie_key] = {
                    "movie": movie,
                    "video": entry.get("video"),
                    "state": new_state,
                    "result": result,
                }
            elif entry["state"] == "matched":
                # Was matched, now unavailable in catalog
                stats["unavailable"] += 1
                entries_by_movie_key[movie_key] = {
                    "movie": movie,
                    "video": entry.get("video"),
                    "state": "unavailable",
                    "result": None,
                    "prev_stream_id": entry.get("stream_id"),
                }
            else:
                stats["unmatched"] += 1
                entries_by_movie_key[movie_key] = {
                    "movie": movie,
                    "video": entry.get("video"),
                    "state": "unmatched",
                    "result": None,
                }

    # Process new video data
    for video in video_data:
        group_title = video["title"]
        video_info = {"id": video["video_id"], "title": video["title"]}

        for movie in video.get("movies", []):
            movie_key = f"{movie['name']}|{movie.get('year', '')}"

            if movie_key in entries_by_movie_key:
                continue

            result = find_movie(movie["name"], movie.get("year"), vod_index)

            if result:
                stats["matched"] += 1
                entries_by_movie_key[movie_key] = {
                    "movie": {"name": movie["name"], "year": movie.get("year")},
                    "video": video_info,
                    "state": "matched",
                    "result": result,
                }
            else:
                stats["unmatched"] += 1
                entries_by_movie_key[movie_key] = {
                    "movie": {"name": movie["name"], "year": movie.get("year")},
                    "video": video_info,
                    "state": "unmatched",
                    "result": None,
                }

    # Validate URLs if requested (parallelized for speed)
    if validate_urls:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading

        matched_entries = [(k, e) for k, e in entries_by_movie_key.items() if e["state"] == "matched" and e.get("result")]
        total = len(matched_entries)
        print(f"  Validating {total} stream URLs (parallel)...")

        # Prepare URL check tasks
        def check_entry(movie_key, entry):
            result = entry["result"]
            stream_id = result.get("stream_id")
            ext = result.get("container_extension", "mp4")
            url = f"{config['iptv_server']}/movie/{config['iptv_username']}/{config['iptv_password']}/{stream_id}.{ext}"
            is_valid = check_stream_url(url)
            return movie_key, entry, stream_id, is_valid

        # Progress tracking
        completed = [0]
        broken_count = [0]
        lock = threading.Lock()

        def update_progress():
            with lock:
                pct = (completed[0] / total) * 100 if total > 0 else 100
                sys.stdout.write(f"\r  [{completed[0]}/{total}] {pct:.0f}% complete, {broken_count[0]} broken")
                sys.stdout.flush()

        # Run parallel validation
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(check_entry, k, e): (k, e) for k, e in matched_entries}

            for future in as_completed(futures):
                movie_key, entry, stream_id, is_valid = future.result()
                completed[0] += 1

                if not is_valid:
                    stats["broken"] += 1
                    stats["matched"] -= 1
                    entry["state"] = "unavailable"
                    entry["prev_stream_id"] = stream_id
                    entry["result"] = None
                    broken_count[0] += 1

                # Update progress every 10 completions or at the end
                if completed[0] % 10 == 0 or completed[0] == total:
                    update_progress()

        print()  # newline after progress

    # Generate entry blocks
    for movie_key, entry in entries_by_movie_key.items():
        movie = entry["movie"]
        video = entry["video"]
        state = entry["state"]
        result = entry.get("result")

        movie_name = movie.get("name", "Unknown")
        year_str = f" | year: {movie['year']}" if movie.get("year") else ""
        video_str = f"{video['id']} | {video['title']}" if video else "unknown"

        # Skip entries with no movie name
        if not movie_name or movie_name == "Unknown":
            continue

        lines.append(META_ENTRY_START)
        lines.append(f"{META_VIDEO} {video_str}")
        lines.append(f"{META_MOVIE} {movie_name}{year_str} | {state}")

        if state == "matched" and result:
            stream_id = result.get("stream_id")
            ext = result.get("container_extension", "mp4")
            icon = result.get("stream_icon", "")
            name = result.get("name", movie_name)
            group = video["title"] if video else "Movies"

            url = f"{config['iptv_server']}/movie/{config['iptv_username']}/{config['iptv_password']}/{stream_id}.{ext}"

            lines.append(f"# stream_id: {stream_id}")
            lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{name}" tvg-logo="{icon}" group-title="{group}",{name}')
            lines.append(url)

        elif state == "unavailable":
            prev_id = entry.get("prev_stream_id", "unknown")
            lines.append(f"# stream_id: {prev_id} (unavailable since {timestamp})")
            lines.append(f"# #EXTINF:-1 group-title=\"{video['title'] if video else 'Movies'}\",{movie_name}")
            lines.append(f"# (stream unavailable)")

        # unmatched entries just have the metadata, no EXTINF

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


def sync_playlists(config, workers=5):
    """Sync all existing playlists with current catalog."""
    output_dir = SCRIPT_DIR / config["output_dir"]
    if not output_dir.exists():
        print("No playlists directory found. Run a full build first.")
        return

    # Fetch fresh VOD catalog
    vod_data = fetch_vod_catalog(config)
    if not vod_data:
        print("Failed to fetch VOD catalog")
        return
    vod_index = build_vod_index(vod_data)
    print(f"VOD catalog: {len(vod_index)} items")

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

        # Generate updated M3U with URL validation
        m3u_content, stats = generate_m3u_content(channel_handle, video_data, config, vod_index, metadata, validate_urls=True, workers=workers)
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
        """
    )
    parser.add_argument("--version", "-V", action="version", version="%(prog)s 0.1.0")
    parser.add_argument("channels", nargs="*", help="YouTube channel handles (e.g., @Mooncut01)")
    parser.add_argument("--playlist", "-p", metavar="NAME", help="IPTVX playlist to use for credentials")
    parser.add_argument("--playlists", action="store_true", help="List available IPTVX playlists")
    parser.add_argument("--sync", action="store_true", help="Sync existing playlists with current catalog")
    parser.add_argument("--workers", type=int, default=5, help="Parallel workers for URL validation (default: 5)")
    parser.add_argument("-o", "--output", help="Output file (for single channel)")
    args = parser.parse_args()

    # Handle --playlists
    if args.playlists:
        selected = list_iptvx_playlists()
        if selected:
            print(f"\nSelected: {selected['name']}")
            print(f"  Server: {selected['server']}")
            print(f"  Username: {selected['username']}")
        return

    # Load config
    config = load_config(args.playlist)

    # Handle --sync
    if args.sync:
        sync_playlists(config, workers=args.workers)
        return

    # Full rebuild mode - require channel argument
    if not args.channels:
        parser.print_help()
        sys.exit(1)

    channels = [{"handle": ch if ch.startswith("@") else f"@{ch}", "name": ch.lstrip("@")} for ch in args.channels]

    # Fetch VOD catalog
    vod_data = fetch_vod_catalog(config)
    if not vod_data:
        sys.exit(1)
    vod_index = build_vod_index(vod_data)
    print(f"VOD catalog: {len(vod_index)} items")

    # Create output directory
    output_dir = SCRIPT_DIR / config["output_dir"]
    output_dir.mkdir(exist_ok=True)

    # Process channels
    for channel in channels:
        video_data = process_channel(channel["handle"], config, vod_index)

        if video_data:
            m3u_content, stats = generate_m3u_content(channel["handle"], video_data, config, vod_index)

            if args.output and len(channels) == 1:
                output_file = Path(args.output)
            else:
                output_file = output_dir / f"{sanitize_filename(channel['handle'])}.m3u"

            output_file.write_text(m3u_content)

            print(f"\nResults for {channel['handle']}:")
            print(f"  Matched: {stats['matched']} movies")
            print(f"  Unmatched: {stats['unmatched']} movies")
            print(f"  Output: {output_file}")


if __name__ == "__main__":
    main()
