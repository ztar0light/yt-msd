#!/usr/bin/env python3
"""
YouTube Music Metadata Scraping Downloader (yt-msd)
Downloads audio from YouTube or YouTube Music and scrapes metadata from various sources.
"""

import sys
import subprocess
import json
import re
import traceback
import argparse
import shutil
import os
from pathlib import Path
from typing import Optional, Dict, List, Any
import urllib.request
import urllib.parse
from datetime import datetime
import time
from difflib import SequenceMatcher
import csv
import platform
import locale
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Try to import colorama for cross-platform color support
try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init(autoreset=True)
    COLORS_ENABLED = True
except ImportError:
    # Fallback if colorama not installed
    class Fore:
        RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = RESET = ''
    class Style:
        BRIGHT = DIM = RESET_ALL = ''
    COLORS_ENABLED = False

# Global options set after argparse (avoids passing through deep call chains)
OPTIONS = None

# Color helper functions
def color_info(text):
    """Cyan for informational messages"""
    return f"{Fore.CYAN}{text}{Style.RESET_ALL}" if COLORS_ENABLED else text

def color_success(text):
    """Green for success messages"""
    return f"{Fore.GREEN}{text}{Style.RESET_ALL}" if COLORS_ENABLED else text

def color_warning(text):
    """Yellow for warnings"""
    return f"{Fore.YELLOW}{text}{Style.RESET_ALL}" if COLORS_ENABLED else text

def color_error(text):
    """Red for errors"""
    return f"{Fore.RED}{text}{Style.RESET_ALL}" if COLORS_ENABLED else text

def color_highlight(text):
    """Bright white for highlights"""
    return f"{Style.BRIGHT}{Fore.WHITE}{text}{Style.RESET_ALL}" if COLORS_ENABLED else text

def color_dim(text):
    """Dim for less important info"""
    return f"{Style.DIM}{text}{Style.RESET_ALL}" if COLORS_ENABLED else text

# yt-dlp via Python module (works when yt-dlp not in PATH, e.g. Windows)
def _ytdlp_cmd():
    return [sys.executable, '-m', 'yt_dlp']

def _run_cmd(cmd, **kwargs):
    """Run subprocess with UTF-8 encoding (fixes Windows)."""
    return subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', **kwargs)

def _path_for_subprocess(p: Path) -> str:
    """Convert path for subprocess - use forward slashes for cross-platform compatibility."""
    return str(p.resolve().as_posix())

def _format_network_error(stderr: str) -> str:
    """Return user-friendly message for common network/DNS errors."""
    if not stderr:
        return "Network request failed"
    s = stderr.lower()
    if 'getaddrinfo failed' in s or 'errno 11001' in s or 'no such host' in s:
        return "DNS/network error: Could not resolve hostname. Check internet connection, DNS, and VPN/proxy."
    if 'connection refused' in s or 'connection reset' in s:
        return "Connection refused or reset. Check firewall or try again later."
    if 'timed out' in s or 'timeout' in s:
        return "Request timed out. Network may be slow or blocked."
    return stderr[:200] if len(stderr) > 200 else stderr

def _find_ffmpeg() -> str:
    """Find ffmpeg executable (Windows/Linux)."""
    exe = shutil.which('ffmpeg')
    return exe or 'ffmpeg'

class ParallelReport:
    """Thread-safe collector for parallel mode results."""
    def __init__(self):
        self.lock = threading.Lock()
        self.processing = []
        self.metadata_ok = []
        self.metadata_failed = []
        self.download_ok = []
        self.download_failed = []
        self.skipped = []
        self.metadata_apply_failed = []
    def add(self, name: str, status: str):
        with self.lock:
            if status == 'metadata_ok':
                self.metadata_ok.append(name)
            elif status == 'metadata_failed':
                self.metadata_failed.append(name)
            elif status == 'download_ok':
                self.download_ok.append(name)
            elif status == 'download_failed':
                self.download_failed.append(name)
            elif status == 'skipped':
                self.skipped.append(name)
            elif status == 'metadata_apply_failed':
                self.metadata_apply_failed.append(name)
    def add_processing(self, name: str):
        with self.lock:
            self.processing.append(name)
    def print_report(self):
        def fmt(lst, prefix="  "):
            return (prefix + ", ".join(lst)) if lst else None
        print("\n" + color_highlight("=" * 60))
        print(color_highlight("PARALLEL MODE REPORT"))
        print(color_highlight("=" * 60))
        if self.processing:
            print(color_info("\nProcessed tracks:"))
            print(color_dim(fmt(self.processing)))
        if self.metadata_ok:
            print(color_success("\nMetadata fetched successfully:"))
            print(color_dim(fmt(self.metadata_ok)))
        if self.metadata_failed:
            print(color_warning("\nMetadata failed (used fallback):"))
            print(color_dim(fmt(self.metadata_failed)))
        if self.download_ok:
            print(color_success("\nDownloads succeeded:"))
            print(color_dim(fmt(self.download_ok)))
        if self.download_failed:
            print(color_error("\nDownloads failed:"))
            print(color_dim(fmt(self.download_failed)))
        if self.metadata_apply_failed:
            print(color_error("\nMetadata apply failed:"))
            print(color_dim(fmt(self.metadata_apply_failed)))
        if self.skipped:
            print(color_info("\nSkipped (exists or dry-run):"))
            print(color_dim(fmt(self.skipped)))
        print(color_highlight("=" * 60))

# Configuration file path
CONFIG_FILE = Path.home() / '.ytmsd_config.json'

# MusicBrainz API compliance: User-Agent with contact, rate limit 1 req/sec
MB_USER_AGENT = 'ytmsd/1.0 ( https://github.com/ztar0light/ytmsd )'
_mb_last_request = 0
_mb_lock = threading.Lock()

def _mb_rate_limit():
    """Enforce MusicBrainz 1 request/second rate limit."""
    global _mb_last_request
    with _mb_lock:
        now = time.monotonic()
        elapsed = now - _mb_last_request
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        _mb_last_request = time.monotonic()

# Default configuration for metadata sources and settings
DEFAULT_CONFIG = {
    'sources': {
        'itunes': True,
        'youtube_music': True,
        'musicbrainz': True
    },
    'timeout': 30,  # Network request timeout (increased from 10 to 30 for yt-dlp)
    'fetch_timeout': 60,  # Download timeout
    'cover_size': '600x600',
    'format': 'mp3',  # Audio format
    'quality': 0,  # Audio quality (0=best, 9=worst for mp3)
    'max_filename_length': 200,  # Max filename length
    'output_template': '{artist}_{title}'  # Output filename template
}

def load_config() -> Dict:
    """Load configuration from ~/.ytmsd_config.json or return default config."""
    print(color_dim("Loading configuration..."))
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(color_error(f"Error loading config: {e}"), file=sys.stderr)
    print(color_info("Using default configuration"))
    return DEFAULT_CONFIG.copy()

def save_config(config: Dict):
    """Save configuration to ~/.ytmsd_config.json."""
    print(color_dim("Saving configuration..."))
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        print(color_success("Configuration saved"))
    except Exception as e:
        print(color_error(f"Error saving config: {e}"), file=sys.stderr)

def settings_menu():
    """Display an interactive menu to configure metadata sources and defaults."""
    config = load_config()

    while True:
        print(color_highlight("\nytmsd Settings"))
        print(color_dim("=" * 60))
        print(color_info("\nMetadata Sources:"))
        sources = config['sources']
        source_list = list(sources.keys())

        for i, (name, enabled) in enumerate(sources.items(), 1):
            status = color_success("Enabled") if enabled else color_dim("Disabled")
            display_name = name.replace('_', ' ').title()
            print(f"  {i}. [{status}] {display_name}")

        print(color_info("\nDefault Settings:"))
        settings_start = len(source_list) + 1
        print(f"  {settings_start}. Timeout: {color_highlight(str(config.get('timeout', 10)) + 's')}")
        print(f"  {settings_start + 1}. Fetch Timeout: {color_highlight(str(config.get('fetch_timeout', 60)) + 's')}")
        print(f"  {settings_start + 2}. Format: {color_highlight(config.get('format', 'mp3'))}")
        print(f"  {settings_start + 3}. Quality: {color_highlight(str(config.get('quality', 0)))}")
        print(f"  {settings_start + 4}. Max Filename Length: {color_highlight(str(config.get('max_filename_length', 200)))}")
        print(f"  {settings_start + 5}. Output Template: {color_highlight(config.get('output_template', '{artist}_{title}'))}")
        print(f"  {settings_start + 6}. Cover Size: {color_highlight(config.get('cover_size', '600x600'))}")

        print(f"\n  {settings_start + 7}. {color_success('Save and Exit')}")
        print(f"  {settings_start + 8}. {color_warning('Exit without saving')}")

        try:
            choice = input("\nSelect option: ").strip()
            choice = int(choice)

            if choice == settings_start + 7:
                save_config(config)
                print("\nSettings saved")
                break
            elif choice == settings_start + 8:
                print("\nChanges discarded")
                break
            elif 1 <= choice <= len(source_list):
                source_name = source_list[choice - 1]
                config['sources'][source_name] = not config['sources'][source_name]
            elif choice == settings_start:
                val = input(f"Enter timeout in seconds (current: {config.get('timeout', 10)}): ").strip()
                if val:
                    config['timeout'] = int(val)
            elif choice == settings_start + 1:
                val = input(f"Enter fetch timeout in seconds (current: {config.get('fetch_timeout', 60)}): ").strip()
                if val:
                    config['fetch_timeout'] = int(val)
            elif choice == settings_start + 2:
                val = input(f"Enter format [mp3/opus/m4a/flac] (current: {config.get('format', 'mp3')}): ").strip()
                if val in ['mp3', 'opus', 'm4a', 'flac']:
                    config['format'] = val
            elif choice == settings_start + 3:
                val = input(f"Enter quality 0-9 (current: {config.get('quality', 0)}): ").strip()
                if val:
                    config['quality'] = int(val)
            elif choice == settings_start + 4:
                val = input(f"Enter max filename length (current: {config.get('max_filename_length', 200)}): ").strip()
                if val:
                    config['max_filename_length'] = int(val)
            elif choice == settings_start + 5:
                val = input(f"Enter output template (current: {config.get('output_template', '{artist}_{title}')}): ").strip()
                if val:
                    config['output_template'] = val
            elif choice == settings_start + 6:
                val = input(f"Enter cover size (current: {config.get('cover_size', '600x600')}): ").strip()
                if val:
                    config['cover_size'] = val
            else:
                print("Invalid option")
        except (ValueError, KeyboardInterrupt):
            print("\nChanges discarded")
            break

class MetadataSource:
    """Base class for metadata sources."""
    def search(self, query: str) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def get_cover_url(self, metadata: Dict[str, Any]) -> Optional[str]:
        return metadata.get('thumbnail')

class YouTubeMusicSource(MetadataSource):
    """Handles metadata scraping and audio downloading from YouTube Music."""
    @lru_cache(maxsize=100)
    def search(self, query: str) -> tuple:
        print(f"Searching YouTube Music for: {query}")
        cmd = _ytdlp_cmd() + [
            '--dump-json',
            '--default-search', 'ytsearch3',
            '--skip-download',
            '--no-warnings',
            '--extractor-args', 'youtube:player_client=web_music,android',
            query
        ]
        max_retries = 1 if OPTIONS.no_search_retry else 3
        for attempt in range(max_retries):
            try:
                print(f"Search attempt {attempt + 1}/{max_retries}")
                result = _run_cmd(cmd, timeout=DEFAULT_CONFIG['timeout'])
                results = []
                for line in result.stdout.strip().split('\n'):
                    if line:
                        try:
                            data = json.loads(line)
                            thumbnail = self._select_thumbnail(data)
                            results.append({
                                'title': data.get('track') or data.get('title'),
                                'artist': data.get('artist') or data.get('uploader'),
                                'album': data.get('album'),
                                'release_date': data.get('release_date') or data.get('upload_date'),
                                'thumbnail': thumbnail,
                                'url': data.get('webpage_url'),
                                'source': 'YouTube Music'
                            })
                        except json.JSONDecodeError:
                            continue
                print(f"Found {len(results)} results from YouTube Music")
                return tuple(results[:3])
            except subprocess.TimeoutExpired:
                print(f"YouTube Music search timed out (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(1)
            except Exception as e:
                print(f"Error searching YouTube Music: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if OPTIONS.debug:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(1)
        print("All YouTube Music search attempts failed", file=sys.stderr)
        return tuple()

    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        print(f"Fetching YouTube Music metadata from: {url}")
        cmd = _ytdlp_cmd() + [
            '--dump-json',
            '--skip-download',
            '--no-warnings',
            '--extractor-args', 'youtube:player_client=web_music,android',
            url
        ]
        max_retries = 1 if OPTIONS.no_search_retry else 3
        for attempt in range(max_retries):
            try:
                print(f"Metadata fetch attempt {attempt + 1}/{max_retries}")
                result = _run_cmd(cmd, timeout=DEFAULT_CONFIG['timeout'])
                if not (result.stdout or '').strip():
                    msg = _format_network_error(result.stderr or "")
                    print(f"YouTube Music unavailable: {msg}", file=sys.stderr)
                    if OPTIONS.debug and result.stderr:
                        print(f"  (raw stderr: {result.stderr[:300]})", file=sys.stderr)
                    raise ValueError("Empty response from yt-dlp")
                data = json.loads(result.stdout)
                thumbnail = self._select_thumbnail(data)
                metadata = {
                    'title': data.get('track') or data.get('title'),
                    'artist': data.get('artist') or data.get('uploader'),
                    'album': data.get('album'),
                    'release_date': data.get('release_date') or data.get('upload_date'),
                    'thumbnail': thumbnail,
                    'description': data.get('description'),
                    'duration': data.get('duration'),
                    'source': 'YouTube Music'
                }
                if metadata['title'] and metadata['artist']:
                    print("Metadata fetched from YouTube Music")
                    return metadata
                else:
                    print("Insufficient metadata from YouTube Music")
                    return None
            except subprocess.TimeoutExpired:
                print(f"YouTube Music metadata fetch timed out (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(1)
            except (json.JSONDecodeError, ValueError) as e:
                # Empty/invalid response - YTM often unavailable for some videos, skip retries
                print(f"YouTube Music metadata unavailable: {e}", file=sys.stderr)
                break
            except Exception as e:
                print(f"Error fetching YouTube Music metadata: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if OPTIONS.debug:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(1)
        print("All YouTube Music metadata fetch attempts failed", file=sys.stderr)
        return None

    def _select_thumbnail(self, data: Dict[str, Any]) -> Optional[str]:
        thumbnails = data.get('thumbnails', [])
        default_thumbnail = data.get('thumbnail')

        for thumb in thumbnails:
            url = thumb.get('url', '')
            if 'lh3.googleusercontent.com' in url and 'w' in url and 'h' in url:
                print(f"Selected YouTube Music thumbnail: {url}")
                return url

        if default_thumbnail:
            print(f"Falling back to default thumbnail: {default_thumbnail}")
            return default_thumbnail

        print("No suitable thumbnail found", file=sys.stderr)
        return None

class MusicBrainzSource(MetadataSource):
    BASE_URL = "https://musicbrainz.org/ws/2"
    COVER_ART_URL = "https://coverartarchive.org/release"

    @lru_cache(maxsize=100)
    def search(self, query: str) -> tuple:
        print(f"Searching MusicBrainz for: {query}")
        url = f"{self.BASE_URL}/recording/?query={urllib.parse.quote(query)}&fmt=json&limit=3"
        max_retries = 1 if OPTIONS and OPTIONS.no_search_retry else 3
        for attempt in range(max_retries):
            try:
                _mb_rate_limit()
                print(f"Search attempt {attempt + 1}/{max_retries}")
                req = urllib.request.Request(url, headers={'User-Agent': MB_USER_AGENT})
                with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
                    data = json.loads(response.read())
                    results = []
                    for rec in data.get('recordings', [])[:3]:
                        artist = rec.get('artist-credit', [{}])[0].get('name', 'Unknown')
                        release = rec.get('releases', [{}])[0] if rec.get('releases') else {}
                        results.append({
                            'title': rec.get('title'),
                            'artist': artist,
                            'album': release.get('title'),
                            'release_date': release.get('date'),
                            'source': 'MusicBrainz',
                            'mbid': rec.get('id'),
                            'release_mbid': release.get('id') if release else None
                        })
                    print(f"Found {len(results)} results from MusicBrainz")
                    return tuple(results)
            except Exception as e:
                print(f"Error searching MusicBrainz: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if OPTIONS and OPTIONS.debug:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(1)
        print("All MusicBrainz search attempts failed", file=sys.stderr)
        return tuple()

    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        print(f"Fetching MusicBrainz metadata from: {url}")
        match = re.search(r'/recording/([a-f0-9-]+)', url)
        if not match:
            return None

        mbid = match.group(1)
        api_url = f"{self.BASE_URL}/recording/{mbid}?inc=artists+releases&fmt=json"
        max_retries = 1 if OPTIONS and OPTIONS.no_search_retry else 3
        for attempt in range(max_retries):
            try:
                _mb_rate_limit()
                print(f"Metadata fetch attempt {attempt + 1}/{max_retries}")
                req = urllib.request.Request(api_url, headers={'User-Agent': MB_USER_AGENT})
                with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
                    data = json.loads(response.read())
                    artist = data.get('artist-credit', [{}])[0].get('name', 'Unknown')
                    release = data.get('releases', [{}])[0] if data.get('releases') else {}
                    print("Metadata fetched from MusicBrainz")
                    return {
                        'title': data.get('title'),
                        'artist': artist,
                        'album': release.get('title'),
                        'release_date': release.get('date'),
                        'source': 'MusicBrainz',
                        'release_mbid': release.get('id') if release else None
                    }
            except Exception as e:
                print(f"Error fetching MusicBrainz metadata: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if OPTIONS and OPTIONS.debug:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(1)
        print("All MusicBrainz metadata fetch attempts failed", file=sys.stderr)
        return None

    def get_cover_url(self, metadata: Dict[str, Any]) -> Optional[str]:
        release_mbid = metadata.get('release_mbid')
        if not release_mbid:
            return None
        cover_url = f"{self.COVER_ART_URL}/{release_mbid}/front"
        max_retries = 1 if OPTIONS and OPTIONS.no_search_retry else 3
        for attempt in range(max_retries):
            try:
                _mb_rate_limit()
                print(f"Cover art check attempt {attempt + 1}/{max_retries}")
                req = urllib.request.Request(cover_url, headers={'User-Agent': MB_USER_AGENT}, method='HEAD')
                with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
                    if response.getcode() == 200:
                        return cover_url
            except Exception as e:
                print(f"Error checking cover art: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(1)
        print("No cover art found in Cover Art Archive", file=sys.stderr)
        return None

class iTunesSource(MetadataSource):
    BASE_URL = "https://itunes.apple.com/search"

    @lru_cache(maxsize=100)
    def search(self, query: str) -> tuple:
        print(f"Searching iTunes for: {query}")
        params = urllib.parse.urlencode({
            'term': query,
            'media': 'music',
            'entity': 'song',
            'limit': 3
        })
        url = f"{self.BASE_URL}?{params}"
        max_retries = 1 if OPTIONS.no_search_retry else 3
        for attempt in range(max_retries):
            try:
                print(f"Search attempt {attempt + 1}/{max_retries}")
                req = urllib.request.Request(url, headers={'User-Agent': 'ytmsd/1.0'})
                with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
                    data = json.loads(response.read())
                    results = []
                    for track in data.get('results', [])[:3]:
                        results.append({
                            'title': track.get('trackName'),
                            'artist': track.get('artistName'),
                            'album': track.get('collectionName'),
                            'release_date': track.get('releaseDate', '')[:10],
                            'thumbnail': track.get('artworkUrl100', '').replace('100x100', DEFAULT_CONFIG['cover_size']),
                            'source': 'iTunes'
                        })
                    print(f"Found {len(results)} results from iTunes")
                    return tuple(results)
            except Exception as e:
                print(f"Error searching iTunes: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if OPTIONS.debug:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(1)
        print("All iTunes search attempts failed", file=sys.stderr)
        return tuple()

    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        print(f"Fetching iTunes metadata from: {url}")
        match = re.search(r'id(\d+)', url)
        if not match:
            return None
        track_id = match.group(1)
        lookup_url = f"https://itunes.apple.com/lookup?id={track_id}&entity=song"
        max_retries = 1 if OPTIONS.no_search_retry else 3
        for attempt in range(max_retries):
            try:
                print(f"Metadata fetch attempt {attempt + 1}/{max_retries}")
                req = urllib.request.Request(lookup_url, headers={'User-Agent': 'ytmsd/1.0'})
                with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
                    data = json.loads(response.read())
                    track = data.get('results', [{}])[0]
                    if not track:
                        return None
                    print("Metadata fetched from iTunes")
                    return {
                        'title': track.get('trackName'),
                        'artist': track.get('artistName'),
                        'album': track.get('collectionName'),
                        'release_date': track.get('releaseDate', '')[:10],
                        'thumbnail': track.get('artworkUrl100', '').replace('100x100', DEFAULT_CONFIG['cover_size']),
                        'source': 'iTunes'
                    }
            except Exception as e:
                print(f"Error fetching iTunes metadata: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if OPTIONS.debug:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(1)
        print("All iTunes metadata fetch attempts failed", file=sys.stderr)
        return None

def download_audio(url: str, output_path: str, is_youtube_music: bool = False) -> bool:
    print(f"Preparing to download audio from: {url}")
    fmt = getattr(OPTIONS, 'format', 'mp3')
    quality = getattr(OPTIONS, 'quality', 0)
    timeout_sec = getattr(OPTIONS, 'timeout', 60) or DEFAULT_CONFIG['fetch_timeout']
    # yt-dlp needs .%(ext)s for post-processing; use Path for cross-platform
    p = Path(output_path)
    out_tpl = str((p.parent / p.stem).as_posix()) + '.%(ext)s'
    cmd = _ytdlp_cmd() + [
        '-x', '--audio-format', fmt,
        '-f', 'bestaudio/best', '--extract-audio', '--no-playlist',
        '--no-warnings', '--prefer-free-formats',
        '--extractor-args', f'youtube:player_client={"web_music,android" if is_youtube_music else "android,web"}',
        '-o', out_tpl, url
    ]
    if fmt == 'mp3':
        cmd.insert(4, str(quality))
        cmd.insert(4, '--audio-quality')
    if OPTIONS.debug:
        cmd.insert(len(_ytdlp_cmd()), '--verbose')
    max_retries = 1 if OPTIONS.no_search_retry else 3
    for attempt in range(max_retries):
        try:
            print(f"Downloading (Attempt {attempt + 1}/{max_retries})...")
            result = _run_cmd(cmd, timeout=timeout_sec)
            if result.returncode == 0:
                print("Audio download complete")
                return True
            else:
                print(f"Download failed: {result.stderr}", file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying with alternative method...")
                    cmd[cmd.index('--extractor-args') + 1] = 'youtube:player_client=ios,web'
        except subprocess.TimeoutExpired:
            print(f"Download timed out after {timeout_sec} seconds (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(1)
        except Exception as e:
            print(f"Error downloading audio: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if OPTIONS.debug:
                traceback.print_exc(file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(1)
    return False

def download_cover(url: str, output_path: str) -> bool:
    print(f"Attempting to download cover from: {url}")
    user_agents = [
        'ytmsd/1.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15'
    ]
    max_retries = len(user_agents) if not OPTIONS.no_search_retry else 1
    for attempt, user_agent in enumerate(user_agents, 1):
        try:
            print(f"Trying with User-Agent {attempt}/{max_retries}...")
            req = urllib.request.Request(url, headers={'User-Agent': user_agent})
            with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
                with open(output_path, 'wb') as f:
                    f.write(response.read())
            print("Cover download successful")
            return True
        except Exception as e:
            print(f"Error downloading cover: {e} (attempt {attempt}/{max_retries})", file=sys.stderr)
            if OPTIONS.debug:
                traceback.print_exc(file=sys.stderr)
            if attempt < max_retries:
                print("Retrying with different User-Agent...")
                time.sleep(1)
    print("All cover download attempts failed", file=sys.stderr)
    return False

def apply_metadata(audio_file: str, metadata: Dict[str, Any], cover_path: Optional[str] = None) -> bool:
    print(f"Preparing to apply metadata to: {audio_file}")
    audio_path = Path(audio_file).absolute()
    if not audio_path.exists():
        print(f"Audio file not found: {audio_file}", file=sys.stderr)
        return False

    output_path = audio_path.parent / f"{audio_path.stem}.tagged{audio_path.suffix}"
    cmd = [_find_ffmpeg(), '-i', str(audio_path), '-y', '-loglevel', 'error']

    metadata = {k: v for k, v in metadata.items() if v and isinstance(v, str)}

    use_original = True
    cover_fixed = None
    if cover_path and Path(cover_path).exists():
        cover_path = Path(cover_path).absolute()
        print(f"Processing cover art: {cover_path}")
        cover_url = metadata.get('thumbnail', '')
        cover_fixed = cover_path.parent / f"{cover_path.stem}.fixed.jpg"

        if 'ytimg.com' in cover_url:
            print("Detected YouTube thumbnail, applying crop and scale...")
            try:
                ffmpeg_cmd = [
                    _find_ffmpeg(), '-i', str(cover_path), '-y', '-loglevel', 'error',
                    '-filter_complex', "crop='min(iw,ih):min(iw,ih):(iw-min(iw,ih))/2:(ih-min(iw,ih))/2',scale=600:600",
                    str(cover_fixed)
                ]
                print(f"Running FFmpeg command: {' '.join(ffmpeg_cmd)}")
                result = _run_cmd(ffmpeg_cmd)
                if result.returncode != 0:
                    raise subprocess.CalledProcessError(result.returncode, ffmpeg_cmd, result.stdout, result.stderr)
                print("Cover art processed")
                use_original = False
            except Exception as e:
                print(f"Could not process cover art: {e}. Falling back to original cover.", file=sys.stderr)
                if OPTIONS.debug:
                    traceback.print_exc(file=sys.stderr)
                use_original = True
        else:
            print("Detected YouTube Music or other square thumbnail, skipping crop...")

        if use_original:
            cmd.extend([
                '-i', str(cover_path),
                '-map', '0:a',
                '-map', '1:0',
                '-c:a', 'copy',
                '-c:v', 'mjpeg',
                '-disposition:v', 'attached_pic',
                '-id3v2_version', '3'
            ])
        else:
            cmd.extend([
                '-i', str(cover_fixed),
                '-map', '0:a',
                '-map', '1:0',
                '-c:a', 'copy',
                '-c:v', 'mjpeg',
                '-disposition:v', 'attached_pic',
                '-id3v2_version', '3'
            ])
    else:
        cmd.extend(['-c', 'copy'])

    for key in ['title', 'artist', 'album']:
        if metadata.get(key):
            cmd.extend(['-metadata', f'{key}={metadata[key]}'])
    if metadata.get('release_date'):
        try:
            year = metadata['release_date'][:4] if len(metadata['release_date']) >= 4 else metadata['release_date']
            datetime.strptime(year, '%Y')
            cmd.extend(['-metadata', f'date={year}'])
        except ValueError:
            print(f"Invalid release date format: {metadata['release_date']}", file=sys.stderr)

    cmd.append(str(output_path))

    try:
        print("Applying metadata...")
        print(f"Running FFmpeg command: {' '.join(cmd)}")
        result = _run_cmd(cmd)
        if result.returncode == 0:
            audio_path.unlink(missing_ok=True)
            output_path.rename(audio_path)

            if cover_path and cover_path.exists():
                if cover_fixed and cover_fixed.exists():
                    cover_fixed.unlink(missing_ok=True)
                if not use_original:
                    cover_path.unlink(missing_ok=True)

            print("Metadata applied successfully")
            return True
        else:
            print(f"Failed to apply metadata: {result.stderr}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Error applying metadata: {e}", file=sys.stderr)
        if OPTIONS.debug:
            traceback.print_exc(file=sys.stderr)
        return False

def display_results(results: List[Dict[str, Any]]):
    print(color_info("\nFound the following matches:\n"))
    for i, result in enumerate(results, 1):
        title = result.get('title', 'Unknown')
        artist = result.get('artist', 'Unknown')
        print(f"{color_highlight(str(i))}. {color_success(title)} - {color_info(artist)}")
        if result.get('album'):
            print(f"   Album: {color_dim(result['album'])}")
        if result.get('release_date'):
            print(f"   Released: {color_dim(result['release_date'])}")
        print(f"   Source: {color_dim(result['source'])}")
        print()

def get_user_choice(max_choice: int, first_time: bool = False, no_results: bool = False, is_youtube_music: bool = False) -> int:
    prompt = f"Select option (1-{max_choice}, 0 for link/query, 00 for {'YouTube Music' if is_youtube_music else 'YouTube'} metadata): " if not no_results else f"Enter 0 to provide link/name (timeout will use {'YouTube Music' if is_youtube_music else 'YouTube'} metadata): "
    timeout = 10

    print(prompt, end='', flush=True)
    if platform.system() == 'Windows':
        import msvcrt
        start_time = time.time()
        choice_str = ''
        print("Countdown: ", end='', flush=True)
        last_print = start_time
        remaining = timeout
        while time.time() - start_time < timeout:
            if msvcrt.kbhit():
                byte_arr = msvcrt.getch()
                if byte_arr == b'\r':  # Enter key
                    break
                elif byte_arr >= b'0' and byte_arr <= b'9' or byte_arr == b'0':
                    choice_str += byte_arr.decode('utf-8')
                    print(byte_arr.decode('utf-8'), end='', flush=True)
            if time.time() - last_print >= 1:
                print(f"{remaining}... ", end='', flush=True)
                remaining -= 1
                last_print = time.time()
            time.sleep(0.01)
    else:
        import select
        choice_str = ''
        print("Countdown: ", end='', flush=True)
        start_time = time.time()
        last_print = start_time
        remaining = timeout
        while time.time() - start_time < timeout:
            rlist, _, _ = select.select([sys.stdin], [], [], 1)
            if rlist:
                choice_str = sys.stdin.readline().strip()
                break
            if time.time() - last_print >= 1:
                print(f"{remaining}... ", end='', flush=True)
                remaining -= 1
                last_print = time.time()

    if not choice_str:
        print(f"\nTimeout, using {'YouTube Music' if is_youtube_music else 'YouTube'} metadata")
        return -1

    try:
        if choice_str == "00":
            return -1
        choice = int(choice_str)
        if 0 <= choice <= max_choice:
            return choice
        else:
            print(f"Invalid, choose 0-{max_choice} or 00")
    except ValueError:
        print(f"Invalid, enter number 0-{max_choice} or 00")

    print(f"Invalid input, using {'YouTube Music' if is_youtube_music else 'YouTube'} metadata")
    return -1

def manual_input() -> Dict[str, Any]:
    print("\nManual metadata input:")
    metadata = {}
    while not metadata.get('title'):
        metadata['title'] = input("Title: ").strip()
        if not metadata['title']:
            print("Title is required")
    while not metadata.get('artist'):
        metadata['artist'] = input("Artist: ").strip()
        if not metadata['artist']:
            print("Artist is required")
    metadata['album'] = input("Album (optional): ").strip() or None
    release_date = input("Release date (YYYY-MM-DD, optional): ").strip()
    if release_date:
        try:
            datetime.strptime(release_date, '%Y-%m-%d')
            metadata['release_date'] = release_date
        except ValueError:
            print("Invalid date format, skipping release date", file=sys.stderr)
            metadata['release_date'] = None
    else:
        metadata['release_date'] = None
    metadata['thumbnail'] = input("Cover art URL (optional): ").strip() or None
    metadata['source'] = 'Manual'
    return metadata

def extract_search_query(entry: Dict) -> str:
    title = entry.get('title', '')
    uploader = entry.get('uploader', '')

    # Enhanced query cleaning
    title = re.sub(r'\s*[\(\[]?(?:Official|Audio|Video|MV|Lyrics|中日羅歌詞|\s+-+\s+.*?|\s*f(ea)?t\.?\s+.*?|\s*【.*?】)[\)\]]?', '', title, flags=re.IGNORECASE)
    title = re.sub(r'[^\w\s\-/&]', '', title).strip()

    uploader = re.sub(r'\s*-\s*Topic', '', uploader, flags=re.IGNORECASE)
    uploader = re.sub(r'\s*VEVO', '', uploader, flags=re.IGNORECASE)
    uploader = re.sub(r'Official', '', uploader, flags=re.IGNORECASE)

    if ' - ' in title:
        parts = title.split(' - ', 1)
        if len(parts) == 2:
            artist_part = parts[0].strip()
            title_part = parts[1].strip()
            return f"{artist_part} {title_part}"

    return f"{uploader} {title}".strip()

def get_youtube_fallback_metadata(entry: Dict, url: str, is_youtube_music: bool = False) -> Dict[str, Any]:
    source_name = 'YouTube Music Fallback' if is_youtube_music else 'YouTube Fallback'
    print(f"Fetching {source_name} metadata for: {url}")
    cmd = _ytdlp_cmd() + [
        '--dump-json',
        '--skip-download',
        '--no-warnings',
        '--extractor-args', 'youtube:player_client=android,web',
        url
    ]
    if OPTIONS.debug:
        cmd.insert(len(_ytdlp_cmd()), '--verbose')
    max_retries = 1 if OPTIONS.no_search_retry else 3
    for attempt in range(max_retries):
        try:
            print(f"Metadata fetch attempt {attempt + 1}/{max_retries}")
            result = _run_cmd(cmd, timeout=DEFAULT_CONFIG['timeout'])
            if not (result.stdout or '').strip():
                msg = _format_network_error(result.stderr or "")
                print(f"YouTube fallback unavailable: {msg}", file=sys.stderr)
                if OPTIONS.debug and result.stderr:
                    print(f"  (raw stderr: {result.stderr[:300]})", file=sys.stderr)
                raise ValueError("Empty response from yt-dlp")
            data = json.loads(result.stdout)
            artist = data.get('uploader', 'Unknown')
            artist = re.sub(r'\s*-\s*Topic', '', artist, flags=re.IGNORECASE)
            artist = re.sub(r'\s*VEVO', '', artist, flags=re.IGNORECASE)
            artist = re.sub(r'Official', '', artist, flags=re.IGNORECASE)
            print(f"Metadata fetched from {source_name}")
            return {
                'title': data.get('title', 'Unknown'),
                'artist': artist,
                'album': None,
                'release_date': data.get('upload_date'),
                'thumbnail': data.get('thumbnail'),
                'source': source_name
            }
        except subprocess.TimeoutExpired:
            print(f"{source_name} metadata fetch timed out (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(1)
        except (json.JSONDecodeError, ValueError):
            # Empty/invalid response - use entry data immediately
            break
        except Exception as e:
            print(f"Error fetching {source_name} metadata: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if OPTIONS.debug:
                traceback.print_exc(file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(1)
    print(f"All {source_name} metadata fetch attempts failed, using entry data", file=sys.stderr)
    artist = entry.get('uploader', 'Unknown')
    artist = re.sub(r'\s*-\s*Topic', '', artist, flags=re.IGNORECASE)
    artist = re.sub(r'\s*VEVO', '', artist, flags=re.IGNORECASE)
    artist = re.sub(r'Official', '', artist, flags=re.IGNORECASE)
    return {
        'title': entry.get('title', 'Unknown'),
        'artist': artist,
        'album': None,
        'release_date': entry.get('upload_date'),
        'thumbnail': entry.get('thumbnail'),
        'source': source_name
    }

def get_enabled_sources(config: Dict) -> List[MetadataSource]:
    print("Loading enabled metadata sources...")
    sources = []
    source_map = {
        'itunes': iTunesSource,
        'youtube_music': YouTubeMusicSource,
        'musicbrainz': MusicBrainzSource
    }
    source_order = ['itunes', 'youtube_music', 'musicbrainz']

    for name in source_order:
        if config['sources'].get(name, False):
            sources.append(source_map[name]())
            display_name = 'iTunes' if name == 'itunes' else name.replace('_', ' ').title()
            print(f"Enabled: {display_name}")

    return sources

def check_thumbnail_url(url: str) -> bool:
    print(f"Checking thumbnail availability: {url}")
    max_retries = 1 if OPTIONS.no_search_retry else 3
    for attempt in range(max_retries):
        try:
            print(f"Thumbnail check attempt {attempt + 1}/{max_retries}")
            req = urllib.request.Request(url, headers={'User-Agent': 'ytmsd/1.0'}, method='HEAD')
            with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
                print(f"Thumbnail accessible: {url}")
                return response.getcode() == 200
        except Exception as e:
            print(f"Thumbnail not accessible: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(1)
    print("All thumbnail check attempts failed", file=sys.stderr)
    return False

def get_metadata_from_source(source_name: str, sources: List[MetadataSource], query: str, entry: Dict, youtube_url: str) -> Optional[Dict[str, Any]]:
    source_map = {
        'yt': None,
        'ytm': YouTubeMusicSource,
        'mb': MusicBrainzSource,
        'it': iTunesSource
    }

    if source_name == 'yt':
        print("Using YouTube metadata as specified")
        return get_youtube_fallback_metadata(entry, youtube_url)

    source_class = source_map.get(source_name)
    if not source_class:
        print(f"Invalid metadata source: {source_name}. Using YouTube metadata.", file=sys.stderr)
        return get_youtube_fallback_metadata(entry, youtube_url)

    source_instance = next((s for s in sources if isinstance(s, source_class)), None)
    if not source_instance:
        source_instance = source_class()

    print(f"Fetching metadata from {source_name} with query: {query}")
    results = source_instance.search(query)
    if results:
        results = list(results)  # Convert tuple from cache to list
        results.sort(key=lambda r: SequenceMatcher(None, query.lower(), (r.get('artist', '') + ' ' + r.get('title', '')).lower()).ratio(), reverse=True)
        print(f"Metadata found from {source_name}")
        return results[0]

    print(f"No metadata found from {source_name}, falling back to YouTube metadata")
    return get_youtube_fallback_metadata(entry, youtube_url)

def is_youtube_music_url(url: str) -> bool:
    return 'music.youtube.com' in url.lower()

def get_youtube_url_from_ytm(url: str) -> str:
    return url.replace('music.youtube.com', 'youtube.com')

def get_ytm_url_from_yt(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    # Remove www. prefix and replace youtube.com with music.youtube.com
    netloc = parsed.netloc.replace('www.', '').replace('youtube.com', 'music.youtube.com')
    return urllib.parse.urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

def is_playlist_url(url: str) -> bool:
    return 'list=' in url or '/playlist?' in url

def clean_video_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query.pop('list', None)
    query.pop('pp', None)
    clean_query = urllib.parse.urlencode(query, doseq=True)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, parsed.fragment))

def process_track(entry: Dict, sources: List[MetadataSource], metadata_url: Optional[str], output_dir: Path, meta_source: Optional[str] = None, is_youtube_music: bool = False, no_interactive: bool = False, report: Optional['ParallelReport'] = None):
    quiet = report is not None
    video_url = entry.get('webpage_url') or entry.get('url')
    youtube_url = get_youtube_url_from_ytm(video_url) if is_youtube_music else video_url
    ytm_url = get_ytm_url_from_yt(video_url) if not is_youtube_music else video_url
    track_title = entry.get('title', 'Unknown')
    if report:
        report.add_processing(track_title)
    if not quiet:
        print(f"\nProcessing track: {track_title}")
        print(f"URL: {video_url}")

    query = entry.get('track') or entry.get('title', '')
    artist = entry.get('artist') or entry.get('uploader', '')
    if artist:
        query = f"{artist} {query}"
    else:
        query = extract_search_query(entry)

    print(f"Using query: {query}")

    all_results = []
    metadata = None
    # Always try YouTube Music metadata first, even for YouTube URLs
    print(f"Attempting YouTube Music metadata fetch from: {ytm_url}")
    ytm_source = YouTubeMusicSource()
    metadata = ytm_source.get_metadata(ytm_url)
    if not metadata:
        print("Falling back to YouTube metadata")
        metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)

    if not metadata and meta_source:
        valid_sources = {'yt', 'ytm', 'mb', 'it'}
        if meta_source.lower() in valid_sources:
            print(f"Using specified metadata source: {meta_source}")
            metadata = get_metadata_from_source(meta_source.lower(), sources, query, entry, youtube_url)
        else:
            print(f"Invalid meta_source '{meta_source}' in CSV, using YouTube metadata", file=sys.stderr)
            metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)

    if not metadata and metadata_url:
        print(f"Attempting direct metadata fetch from: {metadata_url}")
        for source in sources:
            direct_meta = source.get_metadata(metadata_url)
            if direct_meta:
                metadata = direct_meta
                print("Direct metadata fetched successfully")
                break
        if not metadata:
            print("Direct metadata fetch failed, falling back to YouTube metadata")
            metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)

    if not metadata:
        print("Performing metadata search...")
        try:
            for source in sources:
                results = source.search(query)
                all_results.extend(results)
        except Exception as e:
            print(f"Metadata search failed: {e}, falling back to YouTube metadata", file=sys.stderr)
            metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)

        if all_results:
            def similarity(a, b):
                return SequenceMatcher(None, a.lower(), b.lower()).ratio()

            all_results.sort(key=lambda r: similarity(query, (r.get('artist', '') + ' ' + r.get('title', ''))), reverse=True)

            if no_interactive:
                metadata = all_results[0]
            else:
                display_results(all_results)
                choice = get_user_choice(len(all_results), first_time=True, is_youtube_music=False)

                if choice == -1:
                    print("User selected YouTube metadata")
                    metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)
                elif choice == 0:
                    user_input = input("\nEnter metadata link or search query: ").strip()
                    if user_input:
                        if user_input.startswith(('http://', 'https://')):
                            print(f"Fetching metadata from provided link: {user_input}")
                            found = False
                            for source in sources:
                                direct_meta = source.get_metadata(user_input)
                                if direct_meta:
                                    metadata = direct_meta
                                    found = True
                                    break
                            if not found:
                                print("Fetch from link failed, using YouTube metadata")
                                metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)
                        else:
                            print(f"Searching for user query: {user_input}")
                            new_results = []
                            try:
                                for source in sources:
                                    results = source.search(user_input)
                                    new_results.extend(results)
                            except Exception as e:
                                print(f"Search for user query failed: {e}, using YouTube metadata", file=sys.stderr)
                                metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)
                            if new_results:
                                new_results.sort(key=lambda r: similarity(user_input, (r.get('artist', '') + ' ' + r.get('title', ''))).lower(), reverse=True)
                                display_results(new_results)
                                choice = get_user_choice(len(new_results), first_time=False, is_youtube_music=False)
                                if choice == -1:
                                    print("User selected YouTube metadata")
                                    metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)
                                elif choice == 0:
                                    print("No selection made, using YouTube metadata")
                                    metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)
                                else:
                                    metadata = new_results[choice - 1]
                            else:
                                print("No results for user query, using YouTube metadata")
                                metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)
                    else:
                        print("No input provided, using YouTube metadata")
                        metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)
                else:
                    metadata = all_results[choice - 1]
        else:
            print("No metadata found from any source, using YouTube metadata")
            metadata = get_youtube_fallback_metadata(entry, youtube_url, is_youtube_music=False)

    title = metadata.get('title', 'Unknown')
    artist = metadata.get('artist', 'Unknown')
    album = metadata.get('album', '') or ''
    date = (metadata.get('release_date') or '')[:4]  # year only
    fmt = getattr(OPTIONS, 'format', 'mp3')
    ext = fmt
    template = getattr(OPTIONS, 'output_template', '{artist}_{title}')
    safe = lambda s: re.sub(r'[^\w\s-]', '', (s or '')).strip().replace(' ', '_')
    try:
        filename = template.format(artist=safe(artist), title=safe(title), album=safe(album), date=date or '')
    except KeyError:
        filename = f"{safe(artist)}_{safe(title)}"
    max_len = getattr(OPTIONS, 'max_filename_length', 200)
    if len(filename) > max_len:
        filename = filename[:max_len].rstrip('_')
    output_file = output_dir / f"{filename}.{ext}"

    if getattr(OPTIONS, 'skip_existing', False) and output_file.exists():
        if not quiet:
            print(f"Skipping (exists): {output_file}")
        if report:
            report.add(f"{artist} - {title}", 'skipped')
        return

    if getattr(OPTIONS, 'dry_run', False):
        if not quiet:
            print(f"[DRY-RUN] Would download: {title} by {artist} -> {output_file}")
        if report:
            report.add(f"{artist} - {title}", 'skipped')
        return

    if not quiet:
        print(f"Downloading to: {output_file}")

    # Try YouTube Music first for audio, even for YouTube URLs
    success = download_audio(ytm_url, str(output_file), True)
    if not success:
        print("YouTube Music download failed, trying YouTube...")
        success = download_audio(youtube_url, str(output_file), False)

    if success:
        cover_path = None
        if not getattr(OPTIONS, 'no_cover', False) and metadata.get('thumbnail'):
            if check_thumbnail_url(metadata['thumbnail']):
                safe_filename = re.sub(r'[^\w\s-]', '', f"{artist} {title}").strip().replace(' ', '_')
                cover_path = output_dir / f"{safe_filename}.jpg"
                if download_cover(metadata['thumbnail'], str(cover_path)):
                    print(f"Cover downloaded to: {cover_path}")
                else:
                    print("Cover download failed, proceeding without cover")
                    cover_path = None
            else:
                print("Thumbnail URL not accessible, proceeding without cover")
                cover_path = None

        if apply_metadata(str(output_file), metadata, cover_path):
            if not quiet:
                print(f"Track processed successfully: {title} by {artist}")
            if report:
                report.add(f"{artist} - {title}", 'download_ok')
        else:
            if not quiet:
                print(f"Failed to apply metadata for {title} by {artist}", file=sys.stderr)
            if report:
                report.add(f"{artist} - {title}", 'metadata_apply_failed')

        if cover_path and Path(cover_path).exists():
            Path(cover_path).unlink(missing_ok=True)
    else:
        if not quiet:
            print(f"Failed to download audio for {title} by {artist}", file=sys.stderr)
        if report:
            report.add(f"{artist} - {title}", 'download_failed')

def process_one_task(task_data: tuple, task_idx: int, total: int, output_dir: Path, sources: List[MetadataSource], no_interactive: bool, report: Optional['ParallelReport'] = None) -> None:
    """Process a single download task (one URL)."""
    # Handle both 3-tuple and 4-tuple (with playlist folder)
    if len(task_data) == 4:
        download_url, metadata_url, meta_source, playlist_folder = task_data
        output_dir = Path(playlist_folder)
    else:
        download_url, metadata_url, meta_source = task_data
    
    print(color_highlight(f"\nProcessing task {task_idx}/{total}: {download_url}"))
    if not download_url.startswith(('http://', 'https://')):
        print(color_error(f"Invalid download URL: {download_url}. Skipping task."), file=sys.stderr)
        return
    is_youtube_music = is_youtube_music_url(download_url)
    print(color_info(f"Source: {'YouTube Music' if is_youtube_music else 'YouTube'}"))
    print(color_dim("Fetching info from URL..."))
    cmd = _ytdlp_cmd() + [
        '--dump-json',
        '--skip-download',
        '--no-warnings',
        '--extractor-args', 'youtube:player_client=android,web',
        '--no-playlist',
        download_url
    ]
    if OPTIONS.debug:
        cmd.insert(len(_ytdlp_cmd()), '--verbose')
    max_retries = 1 if OPTIONS.no_search_retry else 3
    entries = []
    for attempt in range(max_retries):
        try:
            print(f"Fetch attempt {attempt + 1}/{max_retries}")
            result = _run_cmd(cmd, timeout=DEFAULT_CONFIG['timeout'])
            if result.returncode != 0:
                if attempt < max_retries - 1:
                    time.sleep(1)
                continue
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            if entries:
                break
            if attempt < max_retries - 1:
                time.sleep(1)
        except subprocess.TimeoutExpired:
            if attempt < max_retries - 1:
                time.sleep(1)
        except Exception as e:
            if OPTIONS.debug:
                traceback.print_exc(file=sys.stderr)
            if attempt < max_retries - 1:
                time.sleep(1)
    if not entries:
        if not report:
            print(f"Failed to fetch entries for URL {download_url} after {max_retries} attempts", file=sys.stderr)
        return
    for entry in entries:
        process_track(entry, sources, metadata_url, output_dir, meta_source, is_youtube_music, no_interactive, report=report)

def install_yt_dlp():
    print("yt-dlp not found. Attempting to install via pip...")
    try:
        subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'yt-dlp'])
        print("yt-dlp installed successfully.")
    except Exception as e:
        print(f"Failed to install yt-dlp: {e}", file=sys.stderr)
        sys.exit(1)

def _parse_args():
    parser = argparse.ArgumentParser(
        prog='ytmsd',
        description='YouTube Music Metadata Scraping Downloader - downloads audio from YouTube/YouTube Music with metadata from various sources.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ytmsd https://youtube.com/watch?v=...
  ytmsd https://music.youtube.com/watch?v=...
  ytmsd tracks.csv
  ytmsd https://youtube.com/playlist?list=...
  ytmsd https://music.youtube.com/watch?v=... --meta ytm
  ytmsd https://youtube.com/watch?v=... --meta_link https://music.youtube.com/watch?v=...
  ytmsd tracks.csv --mode parallel --output ./downloads
  CSV format: download_url,metadata_url,meta_source (metadata_url and meta_source optional)
"""
    )
    parser.add_argument('input', nargs='*', help='Download URL(s), playlist URL(s), or CSV file path')
    parser.add_argument('--meta', '-m', choices=['yt', 'ytm', 'it', 'mb'], help='Force metadata source: yt, ytm, it, mb')
    parser.add_argument('--meta_link', '-l', metavar='URL', help='Metadata URL to fetch metadata directly')
    parser.add_argument('--output', '-o', metavar='DIR', help='Output directory (default: current dir or playlist-named subdir)')
    parser.add_argument('--mode', '-M', choices=['sequential', 'parallel'], default='sequential',
                        help='Process multiple links: sequential (default) or parallel. Parallel disables interactive metadata selection.')
    parser.add_argument('--jobs', '-j', type=int, default=8, metavar='N',
                        help='Max concurrent downloads in parallel mode (default: 8)')
    parser.add_argument('--format', '-f', choices=['mp3', 'opus', 'm4a', 'flac'], default='mp3',
                        help='Audio format (default: mp3)')
    parser.add_argument('--quality', '-q', type=int, default=0, metavar='0-9',
                        help='Audio quality 0-9 for mp3 (0=best, 9=worst). Ignored for lossless formats. (default: 0)')
    parser.add_argument('--output-template', '-t', metavar='TPL', default='{artist}_{title}',
                        help='Output filename template. Placeholders: {artist}, {title}, {album}, {date}. Ext auto-added. (default: {artist}_{title})')
    parser.add_argument('--no-cover', action='store_true', help='Skip cover art download')
    parser.add_argument('--dry-run', action='store_true', help='List what would be downloaded without downloading')
    parser.add_argument('--limit', type=int, metavar='N', help='For playlists: only process first N tracks')
    parser.add_argument('--skip-existing', action='store_true', help='Skip tracks whose output file already exists')
    parser.add_argument('--timeout', type=int, default=60, metavar='SEC', help='Download timeout in seconds (default: 60)')
    parser.add_argument('--max-filename-length', type=int, default=200, metavar='N',
                        help='Max filename length; longer names are truncated (default: 200)')
    parser.add_argument('--debug', '-d', action='store_true', help='Enable verbose error output')
    parser.add_argument('--no-search-retry', '-n', action='store_true', help='Reduce retries from 3 to 1 for searches/fetches')
    parser.add_argument('--settings', '-s', action='store_true', help='Open interactive settings menu')
    return parser.parse_args()

def main():
    global OPTIONS
    args = _parse_args()

    # Load config for defaults
    config = load_config()

    # Build OPTIONS namespace for compatibility (functions expect OPTIONS.debug etc)
    class Options:
        pass
    OPTIONS = Options()
    OPTIONS.debug = args.debug
    OPTIONS.no_search_retry = args.no_search_retry
    # Use config defaults if args use default values
    OPTIONS.format = args.format if args.format != 'mp3' else config.get('format', 'mp3')
    OPTIONS.quality = args.quality if args.quality != 0 else config.get('quality', 0)
    OPTIONS.output_template = args.output_template if args.output_template != '{artist}_{title}' else config.get('output_template', '{artist}_{title}')
    OPTIONS.timeout = args.timeout if args.timeout != 60 else config.get('fetch_timeout', 60)
    OPTIONS.max_filename_length = args.max_filename_length if args.max_filename_length != 200 else config.get('max_filename_length', 200)
    OPTIONS.no_cover = args.no_cover
    OPTIONS.dry_run = args.dry_run
    OPTIONS.skip_existing = args.skip_existing
    OPTIONS.timeout = args.timeout
    OPTIONS.max_filename_length = args.max_filename_length
    OPTIONS.jobs = args.jobs
    OPTIONS.limit = args.limit

    if args.settings:
        settings_menu()
        sys.exit(0)

    print("Starting ytmsd - YouTube Music Metadata Scraping Downloader")
    print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not args.input:
        print("Error: input (URL or CSV) is required", file=sys.stderr)
        print("Run with --help for usage.", file=sys.stderr)
        sys.exit(1)

    # Handle multiple inputs
    input_args = args.input if isinstance(args.input, list) else [args.input]
    meta_source = args.meta
    metadata_url = args.meta_link

    try:
        print("Checking dependencies...")
        try:
            result = _run_cmd(_ytdlp_cmd() + ['--version'])
            print(f"yt-dlp found: {result.stdout.strip()}")
        except FileNotFoundError:
            install_yt_dlp()
        except Exception as e:
            print(f"Error checking yt-dlp: {e}", file=sys.stderr)
            sys.exit(1)

        try:
            result = _run_cmd([_find_ffmpeg(), '-version'])
            print(f"ffmpeg found: {result.stdout.splitlines()[0]}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Warning: ffmpeg not found in PATH: {e}. Metadata tagging will be limited.", file=sys.stderr)
            print("Please install ffmpeg from https://ffmpeg.org/download.html and add to PATH.", file=sys.stderr)
            if OPTIONS.debug:
                traceback.print_exc(file=sys.stderr)

        config = load_config()
        sources = get_enabled_sources(config)

        if not sources:
            print("No metadata sources enabled. Using YouTube metadata as fallback.")

        tasks = []
        
        # Process each input
        for input_arg in input_args:
            if input_arg.endswith('.csv'):
                print(f"Reading CSV file: {input_arg}")
                try:
                    with open(input_arg, newline='') as csvfile:
                        reader = csv.reader(csvfile)
                        for row in reader:
                            if not row or not row[0].strip():
                                continue
                            download_url = row[0].strip()
                            metadata_url_csv = row[1].strip() if len(row) > 1 else None
                            meta_source_csv = row[2].strip() if len(row) > 2 else None
                            tasks.append((download_url, metadata_url_csv or metadata_url, meta_source_csv or meta_source))
                    print(f"Found {len(tasks)} valid tasks in CSV")
                except Exception as e:
                    print(f"Error reading CSV file: {e}", file=sys.stderr)
                    if OPTIONS.debug:
                        traceback.print_exc(file=sys.stderr)
                    sys.exit(1)
            elif is_playlist_url(input_arg):
                print(color_info(f"Detected playlist URL: {input_arg}"))
                
                # First, get playlist metadata (title and uploader)
                playlist_info_cmd = _ytdlp_cmd() + [
                    '--dump-json',
                    '--playlist-items', '0',
                    '--no-warnings',
                    '--extractor-args', 'youtube:player_client=android,web',
                    input_arg
                ]
                playlist_title = None
                playlist_uploader = None
                try:
                    print(color_dim("Fetching playlist metadata..."))
                    result = _run_cmd(playlist_info_cmd, timeout=DEFAULT_CONFIG['timeout'])
                    if result.stdout.strip():
                        playlist_data = json.loads(result.stdout.strip().split('\n')[0])
                        playlist_title = playlist_data.get('playlist_title') or playlist_data.get('title', 'Playlist')
                        playlist_uploader = playlist_data.get('uploader') or playlist_data.get('channel', 'Unknown')
                        # Sanitize for folder name
                        playlist_title = re.sub(r'[<>:"/\\|?*]', '', playlist_title)
                        playlist_uploader = re.sub(r'[<>:"/\\|?*]', '', playlist_uploader)
                        print(color_success(f"Playlist: {playlist_title} by {playlist_uploader}"))
                except Exception as e:
                    print(color_warning(f"Could not fetch playlist metadata: {e}"))
                    if OPTIONS.debug:
                        traceback.print_exc(file=sys.stderr)
                
                # Now get playlist entries
                cmd = _ytdlp_cmd() + [
                    '--flat-playlist',
                    '--dump-json',
                    '--no-warnings',
                    '--extractor-args', 'youtube:player_client=android,web',
                    input_arg
                ]
                if OPTIONS.debug:
                    cmd.insert(-1, '--verbose')
                max_retries = 1 if OPTIONS.no_search_retry else 3
                player_clients = ['android', 'web', 'ios']
                entries = []
                for attempt, client in enumerate(player_clients, 1):
                    try:
                        print(color_dim(f"Fetching playlist entries (attempt {attempt}/{max_retries}) with player_client={client}..."))
                        cmd[cmd.index('--extractor-args') + 1] = f'youtube:player_client={client}'
                        print(color_dim(f"Executing command: {' '.join(cmd)}"))
                        result = _run_cmd(cmd, timeout=DEFAULT_CONFIG['fetch_timeout'])
                        if result.returncode != 0:
                            print(color_error(f"Error fetching playlist entries: {result.stderr}"), file=sys.stderr)
                            if attempt < max_retries:
                                print(color_warning(f"Retrying with player_client={player_clients[attempt]}..."))
                                time.sleep(1)
                            continue
                        for line in result.stdout.strip().split('\n'):
                            if line:
                                try:
                                    entry = json.loads(line)
                                    clean_url = clean_video_url(entry.get('url', ''))
                                    tasks.append((clean_url, metadata_url, meta_source))
                                    entries.append(entry)
                                except json.JSONDecodeError as e:
                                    print(color_error(f"Error parsing playlist entry: {e}"), file=sys.stderr)
                                    continue
                        if entries:
                            break
                        else:
                            print(color_warning("No entries found in playlist"), file=sys.stderr)
                            if attempt < max_retries:
                                print(color_warning(f"Retrying with player_client={player_clients[attempt]}..."))
                                time.sleep(1)
                    except subprocess.TimeoutExpired:
                        print(color_error(f"Playlist fetch timed out after {DEFAULT_CONFIG['fetch_timeout']} seconds (attempt {attempt}/{max_retries})"), file=sys.stderr)
                        if attempt < max_retries:
                            print(color_warning(f"Retrying with player_client={player_clients[attempt]}..."))
                            time.sleep(1)
                    except Exception as e:
                        print(color_error(f"Error fetching playlist: {e} (attempt {attempt}/{max_retries})"), file=sys.stderr)
                        if OPTIONS.debug:
                            traceback.print_exc(file=sys.stderr)
                        if attempt < max_retries:
                            print(color_warning(f"Retrying with player_client={player_clients[attempt]}..."))
                            time.sleep(1)

                if not entries:
                    print(color_error(f"Failed to fetch playlist entries for {input_arg} after {max_retries} attempts"), file=sys.stderr)
                    continue  # Skip this input and continue with next

                print(color_success(f"Found {len(entries)} videos in playlist"))
                
                # Create playlist subfolder if we have playlist metadata
                if playlist_title and playlist_uploader and not args.output:
                    playlist_folder_name = f"{playlist_title}-{playlist_uploader}"
                    # Truncate if too long
                    if len(playlist_folder_name) > 100:
                        playlist_folder_name = playlist_folder_name[:100]
                    playlist_output_dir = Path.cwd() / playlist_folder_name
                    playlist_output_dir.mkdir(parents=True, exist_ok=True)
                    print(color_highlight(f"Created playlist folder: {playlist_folder_name}"))
                    # Store playlist folder for this batch of tasks
                    for i in range(len(entries)):
                        task_idx = len(tasks) - len(entries) + i
                        if task_idx >= 0 and task_idx < len(tasks):
                            # Add playlist folder info to task tuple
                            old_task = tasks[task_idx]
                            tasks[task_idx] = (old_task[0], old_task[1], old_task[2], str(playlist_output_dir))
                
                if args.limit and args.limit > 0:
                    current_tasks = tasks[-len(entries):][:args.limit]
                    tasks = tasks[:-len(entries)] + current_tasks
                    print(color_info(f"Limited to first {len(current_tasks)} tracks"))
            else:
                if not input_arg.startswith(('http://', 'https://')):
                    print(f"Invalid download URL: {input_arg}. Must start with http:// or https://", file=sys.stderr)
                    continue  # Skip this input and continue with next
                tasks.append((clean_video_url(input_arg), metadata_url, meta_source))
        
        if not tasks:
            print("No valid tasks to process", file=sys.stderr)
            sys.exit(1)
        
        print(f"\nTotal tasks to process: {len(tasks)}")

        base_output = Path(args.output) if args.output else Path.cwd()
        output_dir = base_output
        base_output.mkdir(parents=True, exist_ok=True)

        no_interactive = (args.mode == 'parallel')
        if args.mode == 'parallel':
            print(f"Processing {len(tasks)} tasks in parallel mode (non-interactive, up to {OPTIONS.jobs} concurrent)")
            max_workers = min(len(tasks), max(1, OPTIONS.jobs))
            parallel_report = ParallelReport()
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(process_one_task, t, i, len(tasks), output_dir, sources, no_interactive, parallel_report): t
                    for i, t in enumerate(tasks, 1)
                }
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        print(f"Task error: {e}", file=sys.stderr)
                        if OPTIONS.debug:
                            traceback.print_exc(file=sys.stderr)
            parallel_report.print_report()
        else:
            for task_idx, task_data in enumerate(tasks, 1):
                process_one_task(task_data, task_idx, len(tasks), output_dir, sources, no_interactive)

        print("\nAll tasks processed")

    except Exception as e:
        print(f"Fatal error in main: {e}", file=sys.stderr)
        if OPTIONS.debug:
            traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
