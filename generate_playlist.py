#!/usr/bin/env python3
"""
YouTube Movie Playlist Generator

Generates M3U playlists from YouTube movie recommendation channels.
Matches movies against an Xtream Codes IPTV provider.

Usage:
    python generate_playlist.py                    # Build all configured channels
    python generate_playlist.py @Mooncut01         # Build specific channel
    python generate_playlist.py @Mooncut01 @CinemaTyler  # Multiple channels
    python generate_playlist.py --add @NewChannel  # Add new channel to config
    python generate_playlist.py --list             # List configured channels
    python generate_playlist.py --combined         # Generate combined playlist
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
CHANNELS_FILE = SCRIPT_DIR / "channels.json"

DEFAULT_CONFIG = {
    "iptv_server": "",
    "iptv_username": "",
    "iptv_password": "",
    "output_dir": "playlists",
    "vod_cache_file": "/tmp/vod_cache.json",
    "cache_max_age_hours": 24,
}


def load_config():
    """Load IPTV config from file or environment."""
    config = DEFAULT_CONFIG.copy()

    if CONFIG_FILE.exists():
        with open(CONFIG_FILE) as f:
            config.update(json.load(f))

    # Override from environment
    for key in ["iptv_server", "iptv_username", "iptv_password"]:
        env_key = key.upper()
        if os.environ.get(env_key):
            config[key] = os.environ[env_key]

    if not all([config["iptv_server"], config["iptv_username"], config["iptv_password"]]):
        print("Error: IPTV credentials not configured.")
        print("Create config.json or set IPTV_SERVER, IPTV_USERNAME, IPTV_PASSWORD")
        sys.exit(1)

    return config


def load_channels():
    """Load configured YouTube channels."""
    if CHANNELS_FILE.exists():
        with open(CHANNELS_FILE) as f:
            return json.load(f)
    return {"channels": []}


def save_channels(data):
    """Save channels configuration."""
    with open(CHANNELS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def run_command(cmd, timeout=300):
    """Run shell command and return output."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        return "", 1


def fetch_channel_videos(channel_handle):
    """Fetch list of videos from YouTube channel."""
    print(f"  Fetching video list...")
    cmd = f'yt-dlp --flat-playlist --print "%(id)s|%(title)s" "https://www.youtube.com/{channel_handle}/videos"'
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
    cmd = f'yt-dlp --skip-download --print description "https://www.youtube.com/watch?v={video_id}"'
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
    """Fetch VOD catalog with caching."""
    cache_file = Path(config["vod_cache_file"])

    if cache_file.exists():
        age_hours = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
        if age_hours < config["cache_max_age_hours"]:
            print(f"Using cached VOD catalog ({age_hours:.1f}h old)")
            with open(cache_file) as f:
                return json.load(f)

    print("Fetching VOD catalog from provider...")
    url = f"{config['iptv_server']}/player_api.php?username={config['iptv_username']}&password={config['iptv_password']}&action=get_vod_streams"
    output, code = run_command(f'curl -s "{url}"', timeout=120)

    if code != 0 or not output:
        print("Error fetching VOD catalog")
        return []

    try:
        data = json.loads(output)
        with open(cache_file, "w") as f:
            json.dump(data, f)
        print(f"Cached {len(data)} VOD items")
        return data
    except json.JSONDecodeError:
        print("Error parsing VOD catalog")
        return []


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


def process_channel(channel_handle, config, vod_index):
    """Process a single YouTube channel and return video data."""
    print(f"\nProcessing {channel_handle}...")

    videos = fetch_channel_videos(channel_handle)
    if not videos:
        print(f"  No videos found")
        return []

    print(f"  Found {len(videos)} videos, fetching descriptions...")
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


def generate_m3u(video_data, channel_name, config, vod_index):
    """Generate M3U playlist content."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "#EXTM3U",
        f"#PLAYLIST:{channel_name} Movie Picks",
        f"# Last synced: {timestamp}",
        f"# Source: https://www.youtube.com/{channel_name}",
        "",
    ]

    stats = {"found": 0, "not_found": 0}
    not_found = []
    seen = set()

    for video in video_data:
        group_title = video["title"]

        for movie in video.get("movies", []):
            result = find_movie(movie["name"], movie.get("year"), vod_index)

            if result:
                stream_id = result.get("stream_id")
                key = (group_title, stream_id)
                if key in seen:
                    continue
                seen.add(key)

                stats["found"] += 1
                ext = result.get("container_extension", "mp4")
                icon = result.get("stream_icon", "")
                name = result.get("name", movie["name"])

                url = f"{config['iptv_server']}/movie/{config['iptv_username']}/{config['iptv_password']}/{stream_id}.{ext}"

                lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{name}" tvg-logo="{icon}" group-title="{group_title}",{name}')
                lines.append(url)
                lines.append("")
            else:
                stats["not_found"] += 1
                year_str = f" ({movie['year']})" if movie.get("year") else ""
                not_found.append(f"{movie['name']}{year_str}")

    return "\n".join(lines), stats, not_found


def sanitize_filename(name):
    """Convert channel name to safe filename."""
    # Remove @ prefix and convert to lowercase
    name = name.lstrip("@").lower()
    # Replace non-alphanumeric with dash
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


def add_channel(channel_handle):
    """Add a new channel to configuration."""
    if not channel_handle.startswith("@"):
        channel_handle = f"@{channel_handle}"

    channels_data = load_channels()

    # Check if already exists
    for ch in channels_data["channels"]:
        if ch["handle"].lower() == channel_handle.lower():
            print(f"Channel {channel_handle} already configured")
            return

    # Verify channel exists
    print(f"Verifying {channel_handle}...")
    videos = fetch_channel_videos(channel_handle)
    if not videos:
        print(f"Error: Could not find channel {channel_handle}")
        return

    channels_data["channels"].append({
        "handle": channel_handle,
        "name": channel_handle.lstrip("@"),
        "enabled": True,
    })
    save_channels(channels_data)
    print(f"Added {channel_handle} ({len(videos)} videos found)")


def list_channels():
    """List all configured channels."""
    channels_data = load_channels()

    if not channels_data["channels"]:
        print("No channels configured. Add one with:")
        print("  python generate_playlist.py --add @ChannelName")
        return

    print("Configured channels:")
    for ch in channels_data["channels"]:
        status = "✓" if ch.get("enabled", True) else "✗"
        print(f"  {status} {ch['handle']}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate M3U playlists from YouTube movie recommendation channels",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_playlist.py                     # Build all channels
  python generate_playlist.py @Mooncut01          # Build one channel
  python generate_playlist.py --add @CinemaTyler  # Add new channel
  python generate_playlist.py --list              # List channels
  python generate_playlist.py --combined          # Single combined playlist
        """
    )
    parser.add_argument("channels", nargs="*", help="YouTube channel handles to process")
    parser.add_argument("--add", metavar="CHANNEL", help="Add a new channel")
    parser.add_argument("--list", action="store_true", help="List configured channels")
    parser.add_argument("--combined", action="store_true", help="Generate single combined playlist")
    parser.add_argument("--refresh-cache", action="store_true", help="Force refresh VOD cache")
    parser.add_argument("-o", "--output", help="Output file (for single channel or combined)")
    args = parser.parse_args()

    # Handle --add
    if args.add:
        add_channel(args.add)
        return

    # Handle --list
    if args.list:
        list_channels()
        return

    # Load config
    config = load_config()

    # Handle --refresh-cache
    if args.refresh_cache:
        cache_file = Path(config["vod_cache_file"])
        if cache_file.exists():
            cache_file.unlink()
            print("Cache cleared")

    # Determine which channels to process
    if args.channels:
        # Channels from command line
        channels = [{"handle": ch if ch.startswith("@") else f"@{ch}", "name": ch.lstrip("@")} for ch in args.channels]
    else:
        # All configured channels
        channels_data = load_channels()
        channels = [ch for ch in channels_data["channels"] if ch.get("enabled", True)]

    if not channels:
        print("No channels to process. Specify channels or add with --add")
        sys.exit(1)

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
    all_video_data = []
    results = []

    for channel in channels:
        video_data = process_channel(channel["handle"], config, vod_index)

        if video_data:
            all_video_data.extend(video_data)

            if not args.combined:
                # Generate individual playlist
                m3u, stats, not_found = generate_m3u(video_data, channel["handle"], config, vod_index)

                if args.output and len(channels) == 1:
                    output_file = Path(args.output)
                else:
                    output_file = output_dir / f"{sanitize_filename(channel['handle'])}.m3u"

                output_file.write_text(m3u)
                results.append({
                    "channel": channel["handle"],
                    "file": output_file,
                    "found": stats["found"],
                    "not_found": stats["not_found"],
                })

    # Generate combined playlist if requested
    if args.combined and all_video_data:
        m3u, stats, not_found = generate_m3u(all_video_data, "Combined", config, vod_index)
        output_file = Path(args.output) if args.output else output_dir / "combined.m3u"
        output_file.write_text(m3u)
        results.append({
            "channel": "Combined",
            "file": output_file,
            "found": stats["found"],
            "not_found": stats["not_found"],
        })

    # Summary
    print(f"\n{'='*60}")
    print("Results:")
    for r in results:
        print(f"  {r['channel']}: {r['found']} movies → {r['file']}")
        if r["not_found"] > 0:
            print(f"    ({r['not_found']} not found in provider)")


if __name__ == "__main__":
    main()
