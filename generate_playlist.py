#!/usr/bin/env python3
"""
Mooncut01 Playlist Generator

Generates M3U playlists from YouTube movie recommendation channels.
Matches movies against an Xtream Codes IPTV provider.

Usage:
    python generate_playlist.py
    python generate_playlist.py --channel @Mooncut01 --output my-playlist.m3u
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# Default configuration
DEFAULT_CONFIG = {
    "youtube_channel": "@Mooncut01",
    "iptv_server": "",
    "iptv_username": "",
    "iptv_password": "",
    "output_file": "mooncut01-all.m3u",
    "vod_cache_file": "/tmp/vod_cache.json",
    "cache_max_age_hours": 24,
}

SCRIPT_DIR = Path(__file__).parent


def load_config():
    """Load config from file or environment, falling back to defaults."""
    config = DEFAULT_CONFIG.copy()

    # Load from config.json if exists
    config_file = SCRIPT_DIR / "config.json"
    if config_file.exists():
        with open(config_file) as f:
            file_config = json.load(f)
            config.update(file_config)

    # Override from environment variables
    if os.environ.get("IPTV_SERVER"):
        config["iptv_server"] = os.environ["IPTV_SERVER"]
    if os.environ.get("IPTV_USERNAME"):
        config["iptv_username"] = os.environ["IPTV_USERNAME"]
    if os.environ.get("IPTV_PASSWORD"):
        config["iptv_password"] = os.environ["IPTV_PASSWORD"]

    # Validate required fields
    if not config["iptv_server"] or not config["iptv_username"] or not config["iptv_password"]:
        print("Error: IPTV credentials not configured.")
        print("Either create config.json (copy from config.json.example) or set environment variables:")
        print("  IPTV_SERVER, IPTV_USERNAME, IPTV_PASSWORD")
        sys.exit(1)

    return config


def run_command(cmd, timeout=300):
    """Run a shell command and return output."""
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        print(f"Command timed out: {cmd[:50]}...")
        return "", 1


def fetch_channel_videos(channel):
    """Fetch list of videos from YouTube channel."""
    print(f"Fetching videos from {channel}...")

    cmd = f'yt-dlp --flat-playlist --print "%(id)s|%(title)s" "https://www.youtube.com/{channel}/videos"'
    output, code = run_command(cmd, timeout=60)

    if code != 0:
        print(f"Error fetching channel videos")
        return []

    videos = []
    for line in output.strip().split("\n"):
        if "|" in line:
            vid_id, title = line.split("|", 1)
            videos.append({"id": vid_id.strip(), "title": title.strip()})

    print(f"Found {len(videos)} videos")
    return videos


def fetch_video_description(video_id):
    """Fetch description for a single video."""
    cmd = f'yt-dlp --skip-download --print description "https://www.youtube.com/watch?v={video_id}"'
    output, code = run_command(cmd, timeout=30)
    return output if code == 0 else ""


def parse_movies_from_description(description):
    """Extract movie titles and years from video description timestamps."""
    movies = []

    for line in description.split("\n"):
        line = line.strip()
        # Match timestamp patterns: "0:00 Movie Name" or "00:00 Movie Name (Year)"
        match = re.match(r"^(\d{1,2}:\d{2})\s+(.+)$", line)
        if match:
            movie_text = match.group(2).strip()

            # Skip intro/outro
            skip_words = ["intro", "introduction", "outro", "conclusion", "subscribe", "thanks"]
            if any(sw in movie_text.lower() for sw in skip_words):
                continue

            # Extract year if present
            year_match = re.search(r"\((\d{4})\)\s*$", movie_text)
            if year_match:
                year = year_match.group(1)
                movie_name = movie_text[: year_match.start()].strip()
            else:
                year = None
                movie_name = movie_text

            # Clean up
            movie_name = re.sub(r"\s+", " ", movie_name).strip()

            if movie_name and len(movie_name) > 2:
                movies.append({"name": movie_name, "year": year})

    return movies


def fetch_vod_catalog(config):
    """Fetch VOD catalog from IPTV provider, with caching."""
    cache_file = Path(config["vod_cache_file"])

    # Check cache
    if cache_file.exists():
        age_hours = (datetime.now().timestamp() - cache_file.stat().st_mtime) / 3600
        if age_hours < config["cache_max_age_hours"]:
            print(f"Using cached VOD catalog ({age_hours:.1f}h old)")
            with open(cache_file) as f:
                return json.load(f)

    # Fetch fresh
    print("Fetching VOD catalog from provider...")
    url = f"{config['iptv_server']}/player_api.php?username={config['iptv_username']}&password={config['iptv_password']}&action=get_vod_streams"

    cmd = f'curl -s "{url}"'
    output, code = run_command(cmd, timeout=120)

    if code != 0 or not output:
        print("Error fetching VOD catalog")
        return []

    try:
        data = json.loads(output)
        # Cache it
        with open(cache_file, "w") as f:
            json.dump(data, f)
        print(f"Cached {len(data)} VOD items")
        return data
    except json.JSONDecodeError:
        print("Error parsing VOD catalog")
        return []


def build_vod_index(vod_data):
    """Build searchable index from VOD catalog."""
    index = {}
    for item in vod_data:
        name = item.get("name")
        if name:
            index[name.lower()] = item
    return index


def find_movie(title, year, vod_index):
    """Search for movie in VOD catalog."""
    title_lower = title.lower().strip()

    # Try exact match with year
    if year:
        for name, item in vod_index.items():
            if title_lower in name and year in name:
                return item

    # Try match with item's year field
    for name, item in vod_index.items():
        if title_lower in name:
            item_year = str(item.get("year", ""))
            if year and item_year == year:
                return item
            elif not year:
                return item

    # Try partial match - all significant words present
    words = [w for w in title_lower.split() if len(w) > 2 and w not in ("the", "and", "for")]
    if len(words) >= 2:
        for name, item in vod_index.items():
            if all(w in name for w in words):
                return item

    return None


def generate_m3u(video_data, vod_index, config):
    """Generate M3U playlist content."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = [
        "#EXTM3U",
        f"#PLAYLIST:Mooncut01 Complete Collection",
        f"# Last synced: {timestamp}",
        f"# Source: https://www.youtube.com/{config['youtube_channel']}",
        "",
    ]

    stats = {"found": 0, "not_found": 0}
    not_found = []
    seen_streams = set()

    for video in video_data:
        group_title = video["title"]

        for movie in video.get("movies", []):
            result = find_movie(movie["name"], movie.get("year"), vod_index)

            if result:
                stream_id = result.get("stream_id")
                key = (group_title, stream_id)
                if key in seen_streams:
                    continue
                seen_streams.add(key)

                stats["found"] += 1
                ext = result.get("container_extension", "mp4")
                icon = result.get("stream_icon", "")
                name = result.get("name", movie["name"])

                stream_url = f"{config['iptv_server']}/movie/{config['iptv_username']}/{config['iptv_password']}/{stream_id}.{ext}"

                lines.append(f'#EXTINF:-1 tvg-id="" tvg-name="{name}" tvg-logo="{icon}" group-title="{group_title}",{name}')
                lines.append(stream_url)
                lines.append("")
            else:
                stats["not_found"] += 1
                year_str = f" ({movie['year']})" if movie.get("year") else ""
                not_found.append(f"{movie['name']}{year_str}")

    return "\n".join(lines), stats, not_found


def main():
    parser = argparse.ArgumentParser(description="Generate M3U playlist from YouTube movie recommendations")
    parser.add_argument("--channel", help="YouTube channel (e.g., @Mooncut01)")
    parser.add_argument("--output", "-o", help="Output M3U file")
    parser.add_argument("--refresh-cache", action="store_true", help="Force refresh VOD cache")
    args = parser.parse_args()

    config = load_config()

    if args.channel:
        config["youtube_channel"] = args.channel
    if args.output:
        config["output_file"] = args.output
    if args.refresh_cache:
        cache_file = Path(config["vod_cache_file"])
        if cache_file.exists():
            cache_file.unlink()

    # Step 1: Fetch channel videos
    videos = fetch_channel_videos(config["youtube_channel"])
    if not videos:
        print("No videos found")
        sys.exit(1)

    # Step 2: Fetch descriptions and parse movies
    print(f"Fetching descriptions for {len(videos)} videos...")
    video_data = []

    for i, video in enumerate(videos, 1):
        print(f"  [{i}/{len(videos)}] {video['title'][:50]}...")
        desc = fetch_video_description(video["id"])
        movies = parse_movies_from_description(desc)

        if movies:
            video_data.append({
                "title": video["title"],
                "movies": movies,
            })

    total_movies = sum(len(v["movies"]) for v in video_data)
    print(f"Parsed {total_movies} movies from {len(video_data)} videos")

    # Step 3: Fetch VOD catalog
    vod_data = fetch_vod_catalog(config)
    if not vod_data:
        print("No VOD data available")
        sys.exit(1)

    vod_index = build_vod_index(vod_data)
    print(f"VOD catalog: {len(vod_index)} items")

    # Step 4: Generate M3U
    print("Generating playlist...")
    m3u_content, stats, not_found = generate_m3u(video_data, vod_index, config)

    # Step 5: Write output
    output_path = Path(config["output_file"])
    output_path.write_text(m3u_content)

    print(f"\n{'='*50}")
    print(f"Output: {output_path}")
    print(f"Movies found: {stats['found']}")
    print(f"Movies not found: {stats['not_found']}")

    if not_found and len(not_found) <= 20:
        print(f"\nNot found:")
        for nf in not_found:
            print(f"  - {nf}")
    elif not_found:
        print(f"\nNot found (first 20):")
        for nf in not_found[:20]:
            print(f"  - {nf}")
        print(f"  ... and {len(not_found) - 20} more")


if __name__ == "__main__":
    main()
