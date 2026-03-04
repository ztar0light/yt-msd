#!/usr/bin/env python3
"""
YouTube Music Metadata Scraping Downloader (yt-msd) v2.0
Downloads audio from YouTube or YouTube Music and scrapes metadata from various sources.

REFACTORED VERSION with:
- Clean architecture with retry decorator
- Beautiful minimal UI with debug levels
- Better error handling and bug prevention
- QoL improvements
"""

import sys
import subprocess
import json
import re
import traceback
import argparse
import shutil
from pathlib import Path
from typing import Optional, Dict, List, Any, Callable, TypeVar, Tuple
import urllib.request
import urllib.parse
from datetime import datetime
import time
from difflib import SequenceMatcher
import csv
import platform
from functools import lru_cache, wraps
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
from dataclasses import dataclass, field
from enum import Enum

# Try to import colorama for cross-platform color support
try:
    from colorama import init as colorama_init, Fore, Style
    colorama_init(autoreset=True)
    COLORS_ENABLED = True
except ImportError:
    class Fore:
        RED = GREEN = YELLOW = BLUE = MAGENTA = CYAN = WHITE = RESET = ''
    class Style:
        BRIGHT = DIM = RESET_ALL = ''
    COLORS_ENABLED = False


# ============================================================================
# Constants
# ============================================================================

VERSION = "2.3.0"
DEFAULT_TIMEOUT = 30  # Increased from 10 to 30 for yt-dlp operations
DEFAULT_FETCH_TIMEOUT = 60
DEFAULT_COVER_SIZE = '600x600'
DEFAULT_MAX_FILENAME_LENGTH = 200
DEFAULT_QUALITY = 0
PLAYLIST_FOLDER_MAX_LENGTH = 100
SEARCH_RESULT_LIMIT = 3
COUNTDOWN_SECONDS = 10
MB_USER_AGENT = 'ytmsd/2.0 ( https://github.com/ztar0light/ytmsd )'


# ============================================================================
# Debug Levels
# ============================================================================

class DebugLevel(Enum):
    """Debug verbosity levels."""
    QUIET = 0      # Only errors
    NORMAL = 1     # Normal output
    VERBOSE = 2    # Detailed progress
    DEBUG = 3      # Everything including API calls


# ============================================================================
# Configuration and Data Classes
# ============================================================================

@dataclass
class AppConfig:
    """Application configuration."""
    debug_level: DebugLevel = DebugLevel.NORMAL
    no_search_retry: bool = False
    jobs: int = 8
    format: str = 'mp3'
    quality: int = 0
    timeout: int = DEFAULT_FETCH_TIMEOUT
    no_cover: bool = False
    dry_run: bool = False
    skip_existing: bool = False
    max_filename_length: int = DEFAULT_MAX_FILENAME_LENGTH
    output_template: str = '{artist}_{title}'
    fetch_timeout: int = DEFAULT_FETCH_TIMEOUT
    network_timeout: int = 30  # Increased from 10 to 30 seconds for yt-dlp operations

    @property
    def max_retries(self) -> int:
        """Calculate max retries based on no_search_retry flag."""
        return 1 if self.no_search_retry else 3

    @property
    def is_quiet(self) -> bool:
        return self.debug_level == DebugLevel.QUIET

    @property
    def is_verbose(self) -> bool:
        return self.debug_level.value >= DebugLevel.VERBOSE.value

    @property
    def is_debug(self) -> bool:
        return self.debug_level == DebugLevel.DEBUG


@dataclass
class Metadata:
    """Metadata container."""
    title: str
    artist: str
    album: Optional[str] = None
    release_date: Optional[str] = None
    thumbnail: Optional[str] = None
    source: str = 'Unknown'
    description: Optional[str] = None
    duration: Optional[int] = None
    url: Optional[str] = None
    mbid: Optional[str] = None
    release_mbid: Optional[str] = None

    def is_complete(self) -> bool:
        """Check if metadata has minimum required fields."""
        return bool(self.title and self.artist)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for compatibility."""
        return {
            'title': self.title,
            'artist': self.artist,
            'album': self.album,
            'release_date': self.release_date,
            'thumbnail': self.thumbnail,
            'source': self.source,
            'description': self.description,
            'duration': self.duration,
            'url': self.url,
            'mbid': self.mbid,
            'release_mbid': self.release_mbid
        }


@dataclass
class PlaylistInfo:
    """Playlist information."""
    title: str
    uploader: str
    url: str
    entry_count: int = 0

    def get_folder_name(self) -> str:
        """Get sanitized folder name for playlist."""
        name = f"{self.uploader}'s {self.title}"
        name = sanitize_filename(name, PLAYLIST_FOLDER_MAX_LENGTH)
        return name


# ============================================================================
# UI/Output System with Debug Levels
# ============================================================================

class UI:
    """Centralized UI system with debug levels and beautiful output."""

    def __init__(self, config: AppConfig):
        self.config = config
        self._lock = threading.Lock()

    def _should_print(self, min_level: DebugLevel) -> bool:
        """Check if message should be printed based on debug level."""
        return self.config.debug_level.value >= min_level.value

    def _print(self, message: str, color_func: Callable = None):
        """Thread-safe print with optional color."""
        with self._lock:
            if color_func and COLORS_ENABLED:
                print(color_func(message))
            else:
                print(message)

    # Error messages (always shown unless QUIET)
    def error(self, message: str):
        """Print error message (red)."""
        if not self.config.is_quiet:
            self._print(f"✗ {message}", lambda m: f"{Fore.RED}{m}{Style.RESET_ALL}")

    def warning(self, message: str):
        """Print warning message (yellow)."""
        if self._should_print(DebugLevel.NORMAL):
            self._print(f"⚠ {message}", lambda m: f"{Fore.YELLOW}{m}{Style.RESET_ALL}")

    # Normal output
    def success(self, message: str):
        """Print success message (green)."""
        if self._should_print(DebugLevel.NORMAL):
            self._print(f"✓ {message}", lambda m: f"{Fore.GREEN}{m}{Style.RESET_ALL}")

    def info(self, message: str):
        """Print info message (cyan)."""
        if self._should_print(DebugLevel.NORMAL):
            self._print(f"ℹ {message}", lambda m: f"{Fore.CYAN}{m}{Style.RESET_ALL}")

    def highlight(self, message: str):
        """Print highlighted message (bright white)."""
        if self._should_print(DebugLevel.NORMAL):
            self._print(message, lambda m: f"{Style.BRIGHT}{Fore.WHITE}{m}{Style.RESET_ALL}")

    # Verbose output
    def verbose(self, message: str):
        """Print verbose message (dim)."""
        if self._should_print(DebugLevel.VERBOSE):
            self._print(f"  {message}", lambda m: f"{Style.DIM}{m}{Style.RESET_ALL}")

    def progress(self, message: str):
        """Print progress message (dim cyan)."""
        if self._should_print(DebugLevel.VERBOSE):
            self._print(f"→ {message}", lambda m: f"{Style.DIM}{Fore.CYAN}{m}{Style.RESET_ALL}")

    # Debug output
    def debug(self, message: str):
        """Print debug message (magenta)."""
        if self._should_print(DebugLevel.DEBUG):
            self._print(f"[DEBUG] {message}", lambda m: f"{Fore.MAGENTA}{m}{Style.RESET_ALL}")

    # Special formatting
    def header(self, message: str):
        """Print section header."""
        if self._should_print(DebugLevel.NORMAL):
            self._print(f"\n{'═' * 60}", lambda m: f"{Style.BRIGHT}{Fore.CYAN}{m}{Style.RESET_ALL}")
            self._print(message, lambda m: f"{Style.BRIGHT}{Fore.WHITE}{m}{Style.RESET_ALL}")
            self._print(f"{'═' * 60}", lambda m: f"{Style.BRIGHT}{Fore.CYAN}{m}{Style.RESET_ALL}")

    def separator(self):
        """Print separator line."""
        if self._should_print(DebugLevel.NORMAL):
            self._print(f"{'─' * 60}", lambda m: f"{Style.DIM}{m}{Style.RESET_ALL}")

    def task_header(self, current: int, total: int, description: str):
        """Print task header."""
        if self._should_print(DebugLevel.NORMAL):
            self._print(
                f"\n[{current}/{total}] {description}",
                lambda m: f"{Style.BRIGHT}{Fore.CYAN}{m}{Style.RESET_ALL}"
            )


# ============================================================================
# Utility Functions
# ============================================================================

def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Sanitize string for use as filename."""
    # Remove invalid characters
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    # Remove leading/trailing spaces and dots
    name = name.strip('. ')
    # Truncate if too long
    if len(name) > max_length:
        name = name[:max_length].strip()
    return name or 'untitled'


def run_command(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    """Run subprocess with UTF-8 encoding (fixes Windows)."""
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        **kwargs
    )


def get_ytdlp_cmd() -> List[str]:
    """Get yt-dlp command (works when yt-dlp not in PATH)."""
    return [sys.executable, '-m', 'yt_dlp']


def find_ffmpeg() -> str:
    """Find ffmpeg executable."""
    return shutil.which('ffmpeg') or 'ffmpeg'


def format_network_error(stderr: str) -> str:
    """Return user-friendly message for common network/DNS errors."""
    if not stderr:
        return "Network request failed"

    s = stderr.lower()
    error_patterns = {
        ('getaddrinfo failed', 'errno 11001', 'no such host'):
            "DNS/network error: Could not resolve hostname",
        ('connection refused', 'connection reset'):
            "Connection refused or reset",
        ('timed out', 'timeout'):
            "Request timed out"
    }

    for keywords, message in error_patterns.items():
        if any(kw in s for kw in keywords):
            return message

    return stderr[:200]


def clean_video_url(url: str) -> str:
    """Clean video URL by removing unnecessary parameters."""
    if not url:
        return url

    # Remove list parameter and other tracking params
    url = re.sub(r'[&?]list=[^&]*', '', url)
    url = re.sub(r'[&?]index=[^&]*', '', url)
    url = re.sub(r'[&?]t=[^&]*', '', url)

    return url


def is_youtube_music_url(url: str) -> bool:
    """Check if URL is from YouTube Music."""
    return 'music.youtube.com' in url


def is_playlist_url(url: str) -> bool:
    """Check if URL is a playlist."""
    return 'list=' in url or '/playlist' in url


def get_ytm_url_from_yt(yt_url: str) -> str:
    """Convert YouTube URL to YouTube Music URL."""
    parsed = urllib.parse.urlparse(yt_url)
    netloc = parsed.netloc.replace('www.', '').replace('youtube.com', 'music.youtube.com')
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc))


# ============================================================================
# Retry Decorator - DRY Principle
# ============================================================================

T = TypeVar('T')

def with_retry(
    ui: UI,
    operation_name: str = "Operation",
    exceptions: tuple = (Exception,)
):
    """
    Decorator for retrying functions on failure.

    Args:
        ui: UI instance for output
        operation_name: Name of operation for logging
        exceptions: Tuple of exceptions to catch
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(config: AppConfig, *args, **kwargs) -> T:
            max_retries = config.max_retries
            last_exception = None

            for attempt in range(max_retries):
                try:
                    ui.debug(f"{operation_name} attempt {attempt + 1}/{max_retries}")
                    return func(config, *args, **kwargs)

                except subprocess.TimeoutExpired as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        ui.warning(f"{operation_name} timed out (attempt {attempt + 1}/{max_retries})")
                        ui.verbose("Retrying...")
                        time.sleep(1)
                    else:
                        ui.error(f"{operation_name} timed out after {max_retries} attempts")

                except (json.JSONDecodeError, ValueError) as e:
                    # Don't retry on parse errors
                    ui.debug(f"Parse error in {operation_name}: {e}")
                    raise

                except exceptions as e:
                    last_exception = e
                    if attempt < max_retries - 1:
                        ui.warning(f"{operation_name} failed: {str(e)[:100]}")
                        ui.verbose("Retrying...")
                        if config.is_debug:
                            traceback.print_exc(file=sys.stderr)
                        time.sleep(1)
                    else:
                        ui.error(f"{operation_name} failed after {max_retries} attempts")

            # All retries failed
            if last_exception:
                raise last_exception
            return None

        return wrapper
    return decorator


# ============================================================================
# MusicBrainz Rate Limiting
# ============================================================================

class MusicBrainzRateLimiter:
    """Thread-safe rate limiter for MusicBrainz API (1 req/sec)."""

    def __init__(self):
        self._last_request = 0
        self._lock = threading.Lock()

    def wait(self):
        """Enforce 1 request/second rate limit."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_request
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
            self._last_request = time.monotonic()


# Global rate limiter instance
mb_rate_limiter = MusicBrainzRateLimiter()


# ============================================================================
# Configuration Management
# ============================================================================

CONFIG_FILE = Path.home() / '.ytmsd_config.json'

DEFAULT_CONFIG = {
    'sources': {
        'itunes': True,
        'youtube_music': True,
        'musicbrainz': True
    },
    'timeout': DEFAULT_TIMEOUT,
    'fetch_timeout': DEFAULT_FETCH_TIMEOUT,
    'cover_size': DEFAULT_COVER_SIZE,
    'format': 'mp3',
    'quality': DEFAULT_QUALITY,
    'max_filename_length': DEFAULT_MAX_FILENAME_LENGTH,
    'output_template': '{artist}_{title}'
}


def load_config() -> Dict:
    """Load configuration from file or return defaults."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: Dict):
    """Save configuration to file."""
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}", file=sys.stderr)


def settings_menu(ui: UI):
    """Interactive settings menu."""
    config = load_config()

    while True:
        ui.header("ytmsd Settings")

        print(f"\n{Fore.CYAN}Metadata Sources:{Style.RESET_ALL}")
        sources = config['sources']
        source_list = list(sources.keys())

        for i, (name, enabled) in enumerate(sources.items(), 1):
            status = f"{Fore.GREEN}✓ Enabled{Style.RESET_ALL}" if enabled else f"{Style.DIM}✗ Disabled{Style.RESET_ALL}"
            display_name = name.replace('_', ' ').title()
            print(f"  {i}. [{status}] {display_name}")

        print(f"\n{Fore.CYAN}Default Settings:{Style.RESET_ALL}")
        settings_start = len(source_list) + 1
        print(f"  {settings_start}. Timeout: {Style.BRIGHT}{config.get('timeout', 10)}s{Style.RESET_ALL}")
        print(f"  {settings_start + 1}. Fetch Timeout: {Style.BRIGHT}{config.get('fetch_timeout', 60)}s{Style.RESET_ALL}")
        print(f"  {settings_start + 2}. Format: {Style.BRIGHT}{config.get('format', 'mp3')}{Style.RESET_ALL}")
        print(f"  {settings_start + 3}. Quality: {Style.BRIGHT}{config.get('quality', 0)}{Style.RESET_ALL}")
        print(f"  {settings_start + 4}. Max Filename Length: {Style.BRIGHT}{config.get('max_filename_length', 200)}{Style.RESET_ALL}")
        print(f"  {settings_start + 5}. Output Template: {Style.BRIGHT}{config.get('output_template', '{artist}_{title}')}{Style.RESET_ALL}")
        print(f"  {settings_start + 6}. Cover Size: {Style.BRIGHT}{config.get('cover_size', '600x600')}{Style.RESET_ALL}")

        print(f"\n  {settings_start + 7}. {Fore.GREEN}Save and Exit{Style.RESET_ALL}")
        print(f"  {settings_start + 8}. {Fore.YELLOW}Exit without saving{Style.RESET_ALL}")

        try:
            choice = input(f"\n{Fore.CYAN}Select option:{Style.RESET_ALL} ").strip()
            choice = int(choice)

            if choice == settings_start + 7:
                save_config(config)
                ui.success("Settings saved")
                break
            elif choice == settings_start + 8:
                ui.info("Changes discarded")
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
                ui.warning("Invalid option")
        except (ValueError, KeyboardInterrupt):
            ui.info("Changes discarded")
            break


# ============================================================================
# Parallel Report
# ============================================================================

class ParallelReport:
    """Thread-safe collector for parallel mode results."""

    def __init__(self, ui: UI):
        self.ui = ui
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
        def fmt(lst):
            return "  " + ", ".join(lst) if lst else None

        self.ui.header("PARALLEL MODE REPORT")

        if self.processing:
            self.ui.info(f"Processed tracks: {len(self.processing)}")
            if self.ui.config.is_verbose:
                print(f"{Style.DIM}{fmt(self.processing)}{Style.RESET_ALL}")

        if self.metadata_ok:
            self.ui.success(f"Metadata fetched: {len(self.metadata_ok)}")
            if self.ui.config.is_verbose:
                print(f"{Style.DIM}{fmt(self.metadata_ok)}{Style.RESET_ALL}")

        if self.metadata_failed:
            self.ui.warning(f"Metadata failed (used fallback): {len(self.metadata_failed)}")
            if self.ui.config.is_verbose:
                print(f"{Style.DIM}{fmt(self.metadata_failed)}{Style.RESET_ALL}")

        if self.download_ok:
            self.ui.success(f"Downloads succeeded: {len(self.download_ok)}")
            if self.ui.config.is_verbose:
                print(f"{Style.DIM}{fmt(self.download_ok)}{Style.RESET_ALL}")

        if self.download_failed:
            self.ui.error(f"Downloads failed: {len(self.download_failed)}")
            print(f"{Style.DIM}{fmt(self.download_failed)}{Style.RESET_ALL}")

        if self.metadata_apply_failed:
            self.ui.error(f"Metadata apply failed: {len(self.metadata_apply_failed)}")
            print(f"{Style.DIM}{fmt(self.metadata_apply_failed)}{Style.RESET_ALL}")

        if self.skipped:
            self.ui.info(f"Skipped: {len(self.skipped)}")
            if self.ui.config.is_verbose:
                print(f"{Style.DIM}{fmt(self.skipped)}{Style.RESET_ALL}")

        self.ui.separator()


# ============================================================================
# Metadata Sources - Refactored with retry decorator
# ============================================================================

class MetadataSource:
    """Base class for metadata sources."""

    def __init__(self, config: AppConfig, ui: UI):
        self.config = config
        self.ui = ui

    def search(self, query: str) -> List[Metadata]:
        """Search for metadata. Returns list of Metadata objects."""
        raise NotImplementedError

    def get_metadata(self, url: str) -> Optional[Metadata]:
        """Get metadata from URL. Returns Metadata object or None."""
        raise NotImplementedError

    def get_cover_url(self, metadata: Metadata) -> Optional[str]:
        """Get cover art URL from metadata."""
        return metadata.thumbnail


class YouTubeMusicSource(MetadataSource):
    """YouTube Music metadata source."""

    @lru_cache(maxsize=100)
    def search(self, query: str) -> Tuple[Metadata, ...]:
        """Search YouTube Music - cached."""
        self.ui.progress(f"Searching YouTube Music: {query}")

        cmd = get_ytdlp_cmd() + [
            '--dump-json',
            '--default-search', 'ytsearch3',
            '--skip-download',
            '--no-warnings',
            '--extractor-args', 'youtube:player_client=web_music,android',
            query
        ]

        @with_retry(self.ui, "YouTube Music search")
        def _search(config: AppConfig):
            result = run_command(cmd, timeout=config.network_timeout)
            results = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        data = json.loads(line)
                        results.append(Metadata(
                            title=data.get('track') or data.get('title') or 'Unknown',
                            artist=data.get('artist') or data.get('uploader') or 'Unknown',
                            album=data.get('album'),
                            release_date=data.get('release_date') or data.get('upload_date'),
                            thumbnail=self._select_thumbnail(data),
                            url=data.get('webpage_url'),
                            source='YouTube Music'
                        ))
                    except json.JSONDecodeError:
                        continue
            return results[:SEARCH_RESULT_LIMIT]

        try:
            results = _search(self.config)
            self.ui.verbose(f"Found {len(results)} results from YouTube Music")
            return tuple(results)
        except Exception as e:
            self.ui.debug(f"YouTube Music search failed: {e}")
            return tuple()

    def get_metadata(self, url: str) -> Optional[Metadata]:
        """Get metadata from YouTube Music URL."""
        self.ui.progress(f"Fetching YouTube Music metadata")

        cmd = get_ytdlp_cmd() + [
            '--dump-json',
            '--skip-download',
            '--no-warnings',
            '--extractor-args', 'youtube:player_client=web_music,android',
            url
        ]

        @with_retry(self.ui, "YouTube Music metadata fetch")
        def _fetch(config: AppConfig):
            result = run_command(cmd, timeout=config.network_timeout)
            if not result.stdout.strip():
                raise ValueError("Empty response from yt-dlp")

            data = json.loads(result.stdout)
            metadata = Metadata(
                title=data.get('track') or data.get('title') or 'Unknown',
                artist=data.get('artist') or data.get('uploader') or 'Unknown',
                album=data.get('album'),
                release_date=data.get('release_date') or data.get('upload_date'),
                thumbnail=self._select_thumbnail(data),
                description=data.get('description'),
                duration=data.get('duration'),
                url=url,
                source='YouTube Music'
            )

            if not metadata.is_complete():
                raise ValueError("Insufficient metadata")

            return metadata

        try:
            return _fetch(self.config)
        except Exception as e:
            self.ui.debug(f"YouTube Music metadata fetch failed: {e}")
            return None

    def _select_thumbnail(self, data: Dict[str, Any]) -> Optional[str]:
        """Select best thumbnail from data."""
        thumbnails = data.get('thumbnails', [])

        # Prefer YouTube Music thumbnails (high quality, square)
        for thumb in thumbnails:
            url = thumb.get('url', '')
            if 'lh3.googleusercontent.com' in url and 'w' in url and 'h' in url:
                self.ui.debug(f"Selected YTM thumbnail: {url[:50]}...")
                return url

        # Fallback to default
        default = data.get('thumbnail')
        if default:
            self.ui.debug(f"Using default thumbnail: {default[:50]}...")
        return default


class MusicBrainzSource(MetadataSource):
    """MusicBrainz metadata source."""

    BASE_URL = "https://musicbrainz.org/ws/2"
    COVER_ART_URL = "https://coverartarchive.org/release"

    @lru_cache(maxsize=100)
    def search(self, query: str) -> Tuple[Metadata, ...]:
        """Search MusicBrainz - cached."""
        self.ui.progress(f"Searching MusicBrainz: {query}")

        url = f"{self.BASE_URL}/recording/?query={urllib.parse.quote(query)}&fmt=json&limit={SEARCH_RESULT_LIMIT}"

        @with_retry(self.ui, "MusicBrainz search")
        def _search(config: AppConfig):
            mb_rate_limiter.wait()
            req = urllib.request.Request(url, headers={'User-Agent': MB_USER_AGENT})
            with urllib.request.urlopen(req, timeout=config.network_timeout) as response:
                data = json.loads(response.read())
                results = []
                for rec in data.get('recordings', [])[:SEARCH_RESULT_LIMIT]:
                    artist = rec.get('artist-credit', [{}])[0].get('name', 'Unknown')
                    release = rec.get('releases', [{}])[0] if rec.get('releases') else {}
                    results.append(Metadata(
                        title=rec.get('title') or 'Unknown',
                        artist=artist,
                        album=release.get('title'),
                        release_date=release.get('date'),
                        source='MusicBrainz',
                        mbid=rec.get('id'),
                        release_mbid=release.get('id') if release else None
                    ))
                return results

        try:
            results = _search(self.config)
            self.ui.verbose(f"Found {len(results)} results from MusicBrainz")
            return tuple(results)
        except Exception as e:
            self.ui.debug(f"MusicBrainz search failed: {e}")
            return tuple()

    def get_metadata(self, url: str) -> Optional[Metadata]:
        """Get metadata from MusicBrainz URL."""
        match = re.search(r'/recording/([a-f0-9-]+)', url)
        if not match:
            return None

        mbid = match.group(1)
        self.ui.progress(f"Fetching MusicBrainz metadata: {mbid}")

        api_url = f"{self.BASE_URL}/recording/{mbid}?inc=artists+releases&fmt=json"

        @with_retry(self.ui, "MusicBrainz metadata fetch")
        def _fetch(config: AppConfig):
            mb_rate_limiter.wait()
            req = urllib.request.Request(api_url, headers={'User-Agent': MB_USER_AGENT})
            with urllib.request.urlopen(req, timeout=config.network_timeout) as response:
                data = json.loads(response.read())
                artist = data.get('artist-credit', [{}])[0].get('name', 'Unknown')
                release = data.get('releases', [{}])[0] if data.get('releases') else {}
                return Metadata(
                    title=data.get('title') or 'Unknown',
                    artist=artist,
                    album=release.get('title'),
                    release_date=release.get('date'),
                    source='MusicBrainz',
                    mbid=mbid,
                    release_mbid=release.get('id') if release else None,
                    url=url
                )

        try:
            return _fetch(self.config)
        except Exception as e:
            self.ui.debug(f"MusicBrainz metadata fetch failed: {e}")
            return None

    def get_cover_url(self, metadata: Metadata) -> Optional[str]:
        """Get cover art URL from MusicBrainz."""
        if not metadata.release_mbid:
            return None

        cover_url = f"{self.COVER_ART_URL}/{metadata.release_mbid}/front"
        self.ui.debug(f"Checking cover art: {cover_url}")

        @with_retry(self.ui, "Cover art check")
        def _check(config: AppConfig):
            mb_rate_limiter.wait()
            req = urllib.request.Request(cover_url, headers={'User-Agent': MB_USER_AGENT}, method='HEAD')
            with urllib.request.urlopen(req, timeout=config.network_timeout) as response:
                if response.getcode() == 200:
                    return cover_url
            return None

        try:
            return _check(self.config)
        except Exception:
            return None


class iTunesSource(MetadataSource):
    """iTunes metadata source."""

    BASE_URL = "https://itunes.apple.com/search"

    @lru_cache(maxsize=100)
    def search(self, query: str) -> Tuple[Metadata, ...]:
        """Search iTunes - cached."""
        self.ui.progress(f"Searching iTunes: {query}")

        params = urllib.parse.urlencode({
            'term': query,
            'media': 'music',
            'entity': 'song',
            'limit': SEARCH_RESULT_LIMIT
        })
        url = f"{self.BASE_URL}?{params}"

        @with_retry(self.ui, "iTunes search")
        def _search(config: AppConfig):
            req = urllib.request.Request(url, headers={'User-Agent': MB_USER_AGENT})
            with urllib.request.urlopen(req, timeout=config.network_timeout) as response:
                data = json.loads(response.read())
                results = []
                for track in data.get('results', [])[:SEARCH_RESULT_LIMIT]:
                    cover_size = load_config().get('cover_size', DEFAULT_COVER_SIZE)
                    results.append(Metadata(
                        title=track.get('trackName') or 'Unknown',
                        artist=track.get('artistName') or 'Unknown',
                        album=track.get('collectionName'),
                        release_date=track.get('releaseDate', '')[:10],
                        thumbnail=track.get('artworkUrl100', '').replace('100x100', cover_size),
                        source='iTunes',
                        url=track.get('trackViewUrl')
                    ))
                return results

        try:
            results = _search(self.config)
            self.ui.verbose(f"Found {len(results)} results from iTunes")
            return tuple(results)
        except Exception as e:
            self.ui.debug(f"iTunes search failed: {e}")
            return tuple()

    def get_metadata(self, url: str) -> Optional[Metadata]:
        """Get metadata from iTunes URL."""
        match = re.search(r'id(\d+)', url)
        if not match:
            return None

        track_id = match.group(1)
        self.ui.progress(f"Fetching iTunes metadata: {track_id}")

        lookup_url = f"https://itunes.apple.com/lookup?id={track_id}&entity=song"

        @with_retry(self.ui, "iTunes metadata fetch")
        def _fetch(config: AppConfig):
            req = urllib.request.Request(lookup_url, headers={'User-Agent': MB_USER_AGENT})
            with urllib.request.urlopen(req, timeout=config.network_timeout) as response:
                data = json.loads(response.read())
                track = data.get('results', [{}])[0]
                if not track:
                    raise ValueError("No track found")

                cover_size = load_config().get('cover_size', DEFAULT_COVER_SIZE)
                return Metadata(
                    title=track.get('trackName') or 'Unknown',
                    artist=track.get('artistName') or 'Unknown',
                    album=track.get('collectionName'),
                    release_date=track.get('releaseDate', '')[:10],
                    thumbnail=track.get('artworkUrl100', '').replace('100x100', cover_size),
                    source='iTunes',
                    url=url
                )

        try:
            return _fetch(self.config)
        except Exception as e:
            self.ui.debug(f"iTunes metadata fetch failed: {e}")
            return None


# ============================================================================
# Download and File Operations
# ============================================================================

def download_audio(config: AppConfig, ui: UI, url: str, output_path: str, is_youtube_music: bool = False) -> bool:
    """Download audio from URL."""
    ui.progress(f"Downloading audio")

    fmt = config.format
    quality = config.quality
    timeout_sec = config.timeout

    # yt-dlp needs .%(ext)s for post-processing
    p = Path(output_path)
    out_tpl = str((p.parent / p.stem).as_posix()) + '.%(ext)s'

    cmd = get_ytdlp_cmd() + [
        '-x', '--audio-format', fmt,
        '-f', 'bestaudio/best', '--extract-audio', '--no-playlist',
        '--no-warnings', '--prefer-free-formats',
        '--extractor-args', f'youtube:player_client={"web_music,android" if is_youtube_music else "android,web"}',
        '-o', out_tpl, url
    ]

    if fmt == 'mp3':
        cmd.insert(4, str(quality))
        cmd.insert(4, '--audio-quality')

    if config.is_debug:
        cmd.insert(len(get_ytdlp_cmd()), '--verbose')

    @with_retry(ui, "Audio download")
    def _download(cfg: AppConfig):
        result = run_command(cmd, timeout=timeout_sec)
        if result.returncode != 0:
            raise Exception(f"Download failed: {result.stderr[:200]}")
        return True

    try:
        _download(config)
        ui.success("Audio downloaded")
        return True
    except Exception as e:
        ui.error(f"Download failed: {e}")
        return False


def download_cover(config: AppConfig, ui: UI, url: str, output_path: str) -> bool:
    """Download cover art."""
    ui.verbose(f"Downloading cover art")

    user_agents = [
        MB_USER_AGENT,
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15'
    ]

    for attempt, user_agent in enumerate(user_agents, 1):
        try:
            ui.debug(f"Cover download attempt {attempt}/{len(user_agents)}")
            req = urllib.request.Request(url, headers={'User-Agent': user_agent})
            with urllib.request.urlopen(req, timeout=config.network_timeout) as response:
                with open(output_path, 'wb') as f:
                    f.write(response.read())
            ui.verbose("Cover downloaded")
            return True
        except Exception as e:
            if attempt == len(user_agents):
                ui.debug(f"Cover download failed: {e}")
                return False
            time.sleep(0.5)

    return False


def apply_metadata(config: AppConfig, ui: UI, audio_file: str, metadata: Metadata, cover_path: Optional[str] = None) -> bool:
    """Apply metadata to audio file using ffmpeg."""
    ui.progress("Applying metadata")

    audio_path = Path(audio_file).absolute()
    if not audio_path.exists():
        ui.error(f"Audio file not found: {audio_file}")
        return False

    output_path = audio_path.parent / f"{audio_path.stem}.tagged{audio_path.suffix}"
    cmd = [find_ffmpeg(), '-i', str(audio_path), '-y', '-loglevel', 'error']

    # Filter out None values
    meta_dict = {k: v for k, v in metadata.to_dict().items() if v and isinstance(v, str)}

    # Handle cover art
    use_original = True
    cover_fixed = None

    if cover_path and Path(cover_path).exists():
        cover_path_obj = Path(cover_path)

        # Check if it's a YouTube thumbnail (needs cropping)
        if metadata.source in ['YouTube', 'YouTube Music'] and 'youtube' in (metadata.thumbnail or ''):
            cover_fixed = cover_path_obj.parent / f"{cover_path_obj.stem}_cropped{cover_path_obj.suffix}"
            crop_cmd = [
                find_ffmpeg(), '-i', str(cover_path_obj),
                '-vf', 'crop=min(iw\\,ih):min(iw\\,ih)',
                '-y', '-loglevel', 'error',
                str(cover_fixed)
            ]

            try:
                ui.debug("Cropping thumbnail to square")
                result = run_command(crop_cmd, timeout=30)
                if result.returncode == 0 and cover_fixed.exists():
                    use_original = False
                    ui.verbose("Thumbnail cropped to square")
                else:
                    ui.debug("Crop failed, using original")
            except Exception as e:
                ui.debug(f"Crop error: {e}")

        cover_to_use = cover_path_obj if use_original else cover_fixed
        cmd.extend(['-i', str(cover_to_use)])
        cmd.extend(['-map', '0:a', '-map', '1:v'])
        cmd.extend(['-c:a', 'copy', '-c:v', 'copy'])
        cmd.extend(['-disposition:v:0', 'attached_pic'])
    else:
        cmd.extend(['-c', 'copy'])

    # Add metadata tags
    for key, value in meta_dict.items():
        if key in ['title', 'artist', 'album', 'date']:
            tag_key = key if key != 'date' else 'date'
            if key == 'release_date':
                tag_key = 'date'
                value = value[:4] if len(value) >= 4 else value
            cmd.extend(['-metadata', f'{tag_key}={value}'])

    cmd.append(str(output_path))

    try:
        ui.debug(f"Running ffmpeg: {' '.join(cmd[:10])}...")
        result = run_command(cmd, timeout=60)

        # Check if output file was created successfully (more reliable than return code)
        if output_path.exists() and output_path.stat().st_size > 0:
            # Replace original with tagged version
            try:
                audio_path.unlink()
                output_path.rename(audio_path)
                ui.success("Metadata applied")

                # Cleanup
                if cover_path:
                    Path(cover_path).unlink(missing_ok=True)
                if cover_fixed:
                    cover_fixed.unlink(missing_ok=True)

                return True
            except Exception as e:
                ui.debug(f"File replacement error: {e}")
                # If rename fails, at least we have the tagged file
                if output_path.exists():
                    ui.verbose("Metadata applied (tagged file created)")
                    return True
        else:
            if result.stderr:
                ui.error(f"Metadata apply failed: {result.stderr[:200]}")
            else:
                ui.error("Metadata apply failed: Output file not created")
            return False

    except Exception as e:
        ui.error(f"Metadata apply error: {e}")
        if config.is_debug:
            traceback.print_exc(file=sys.stderr)
        return False


# ============================================================================
# Metadata Selection and User Interaction
# ============================================================================

def display_results(ui: UI, results: List[Metadata]):
    """Display metadata search results."""
    ui.info("Found the following matches:\n")

    for i, result in enumerate(results, 1):
        title = result.title or 'Unknown'
        artist = result.artist or 'Unknown'

        print(f"{Style.BRIGHT}{Fore.WHITE}{i}.{Style.RESET_ALL} "
              f"{Fore.GREEN}{title}{Style.RESET_ALL} - "
              f"{Fore.CYAN}{artist}{Style.RESET_ALL}")

        if result.album:
            print(f"   Album: {Style.DIM}{result.album}{Style.RESET_ALL}")
        if result.release_date:
            print(f"   Released: {Style.DIM}{result.release_date}{Style.RESET_ALL}")
        print(f"   Source: {Style.DIM}{result.source}{Style.RESET_ALL}")
        print()


def get_user_choice(ui: UI, max_choice: int, is_youtube_music: bool = False) -> int:
    """Get user choice with timeout."""
    fallback_source = 'YouTube Music' if is_youtube_music else 'YouTube'
    prompt = f"Select (1-{max_choice}, 0=custom, 00={fallback_source}): "
    timeout = COUNTDOWN_SECONDS

    print(f"{Fore.CYAN}{prompt}{Style.RESET_ALL}", end='', flush=True)

    if platform.system() == 'Windows':
        import msvcrt
        start_time = time.time()
        choice_str = ''

        while time.time() - start_time < timeout:
            if msvcrt.kbhit():
                byte_arr = msvcrt.getch()
                if byte_arr == b'\r':  # Enter
                    break
                elif byte_arr >= b'0' and byte_arr <= b'9':
                    char = byte_arr.decode('utf-8')
                    choice_str += char
                    print(char, end='', flush=True)

            remaining = int(timeout - (time.time() - start_time))
            if remaining >= 0:
                print(f"\r{Fore.CYAN}{prompt}{Style.RESET_ALL}{choice_str} ({remaining}s) ", end='', flush=True)
            time.sleep(0.1)

        print()  # Newline

        if choice_str:
            return int(choice_str) if choice_str.isdigit() else -1
        else:
            ui.info(f"Timeout - using {fallback_source} metadata")
            return -1
    else:
        # Unix-like systems
        import select
        rlist, _, _ = select.select([sys.stdin], [], [], timeout)

        if rlist:
            choice_str = sys.stdin.readline().strip()
            return int(choice_str) if choice_str.isdigit() else -1
        else:
            print()
            ui.info(f"Timeout - using {fallback_source} metadata")
            return -1


def extract_search_query(entry: Dict) -> str:
    """Extract search query from video entry."""
    title = entry.get('title', '')
    uploader = entry.get('uploader', '')

    # Clean title
    title = re.sub(r'[^\w\s\-/&]', '', title).strip()

    # Clean uploader
    uploader = re.sub(r'\s*-\s*Topic', '', uploader, flags=re.IGNORECASE)
    uploader = re.sub(r'\s*VEVO', '', uploader, flags=re.IGNORECASE)
    uploader = re.sub(r'Official', '', uploader, flags=re.IGNORECASE)

    # Try to extract artist - title from title
    if ' - ' in title:
        parts = title.split(' - ', 1)
        if len(parts) == 2:
            artist_part, title_part = parts
            return f"{artist_part} {title_part}"

    return f"{uploader} {title}".strip()


def get_youtube_fallback_metadata(config: AppConfig, ui: UI, entry: Dict, url: str, is_youtube_music: bool = False) -> Metadata:
    """Get fallback metadata from YouTube/YouTube Music."""
    source_name = "YouTube Music" if is_youtube_music else "YouTube"
    ui.progress(f"Fetching {source_name} fallback metadata")

    cmd = get_ytdlp_cmd() + [
        '--dump-json',
        '--skip-download',
        '--no-warnings',
        '--extractor-args', f'youtube:player_client={"web_music,android" if is_youtube_music else "android,web"}',
        '--no-playlist',
        url
    ]

    @with_retry(ui, f"{source_name} fallback metadata")
    def _fetch(cfg: AppConfig):
        result = run_command(cmd, timeout=cfg.network_timeout)
        if not result.stdout.strip():
            raise ValueError("Empty response")

        data = json.loads(result.stdout)
        artist = data.get('uploader') or 'Unknown'

        # Clean artist name (ensure it's a string)
        if artist and isinstance(artist, str):
            artist = re.sub(r'\s*-\s*Topic', '', artist, flags=re.IGNORECASE)
            artist = re.sub(r'\s*VEVO', '', artist, flags=re.IGNORECASE)
            artist = re.sub(r'Official', '', artist, flags=re.IGNORECASE)
        else:
            artist = 'Unknown'

        return Metadata(
            title=data.get('title') or 'Unknown',
            artist=artist,
            release_date=data.get('upload_date'),
            thumbnail=data.get('thumbnail'),
            description=data.get('description'),
            duration=data.get('duration'),
            url=url,
            source=source_name
        )

    try:
        return _fetch(config)
    except Exception:
        # Last resort: use entry data
        ui.debug("Using entry data as last resort")
        artist = entry.get('uploader') or 'Unknown'

        # Clean artist name (ensure it's a string)
        if artist and isinstance(artist, str):
            artist = re.sub(r'\s*-\s*Topic', '', artist, flags=re.IGNORECASE)
            artist = re.sub(r'\s*VEVO', '', artist, flags=re.IGNORECASE)
        else:
            artist = 'Unknown'

        return Metadata(
            title=entry.get('title') or 'Unknown',
            artist=artist,
            thumbnail=entry.get('thumbnail'),
            url=url,
            source=source_name
        )


def select_metadata(
    config: AppConfig,
    ui: UI,
    sources: Dict[str, MetadataSource],
    entry: Dict,
    download_url: str,
    metadata_url: Optional[str],
    meta_source: Optional[str],
    no_interactive: bool
) -> Optional[Metadata]:
    """Select metadata from various sources - NO AUTOMATIC SEARCHING!"""

    is_youtube_music = is_youtube_music_url(download_url)

    # 1. If metadata URL provided, use it directly
    if metadata_url:
        ui.info(f"Using provided metadata URL")

        # Determine source from URL
        if 'music.youtube.com' in metadata_url:
            source = sources.get('ytm')
        elif 'musicbrainz.org' in metadata_url:
            source = sources.get('mb')
        elif 'itunes.apple.com' in metadata_url:
            source = sources.get('it')
        else:
            source = sources.get('ytm')  # Default

        if source:
            metadata = source.get_metadata(metadata_url)
            if metadata and metadata.is_complete():
                return metadata

    # 2. Try YouTube Music URL directly (ALWAYS, unless forced source)
    if not meta_source or meta_source == 'ytm':
        if is_youtube_music and 'ytm' in sources:
            ui.verbose("Trying YouTube Music URL metadata (direct)")
            metadata = sources['ytm'].get_metadata(download_url)
            if metadata and metadata.is_complete():
                ui.success("Using YouTube Music URL metadata")
                return metadata

        # For regular YouTube URLs, try converting to YouTube Music URL
        if not is_youtube_music and 'ytm' in sources:
            ytm_url = get_ytm_url_from_yt(download_url)
            ui.verbose(f"Trying YouTube Music URL: {ytm_url}")
            metadata = sources['ytm'].get_metadata(ytm_url)
            if metadata and metadata.is_complete():
                ui.success("Using YouTube Music metadata (converted URL)")
                return metadata

    # 3. If forced source (and not ytm), try that source's direct URL method
    if meta_source and meta_source != 'ytm':
        ui.info(f"Using forced metadata source: {meta_source}")
        source = sources.get(meta_source)
        if source:
            # Try direct URL fetch if the source supports it
            metadata = source.get_metadata(download_url)
            if metadata and metadata.is_complete():
                return metadata

    # 4. Fallback to YouTube/YouTube Music basic metadata (NO SEARCH!)
    ui.info("Using YouTube fallback metadata (no search)")
    return get_youtube_fallback_metadata(config, ui, entry, download_url, is_youtube_music)


# ============================================================================
# Playlist Processing
# ============================================================================

def fetch_playlist_info(config: AppConfig, ui: UI, url: str) -> Optional[PlaylistInfo]:
    """Fetch playlist metadata."""
    ui.progress("Fetching playlist information")

    cmd = get_ytdlp_cmd() + [
        '--dump-json',
        '--flat-playlist',
        '--playlist-end', '1',  # Only get first entry to extract playlist info
        '--no-warnings',
        '--extractor-args', 'youtube:player_client=android,web',
        url
    ]

    try:
        result = run_command(cmd, timeout=config.network_timeout)
        if result.stdout.strip():
            lines = result.stdout.strip().split('\n')
            # Try to get playlist info from any line
            for line in lines:
                try:
                    data = json.loads(line)
                    # Check if this has playlist info
                    if 'playlist_title' in data or 'playlist' in data:
                        title = data.get('playlist_title') or data.get('playlist', 'Playlist')
                        uploader = data.get('playlist_uploader') or data.get('uploader') or data.get('channel', 'Unknown')

                        # Sanitize
                        title = sanitize_filename(title, 50)
                        uploader = sanitize_filename(uploader, 30)

                        info = PlaylistInfo(
                            title=title,
                            uploader=uploader,
                            url=url
                        )

                        ui.success(f"Playlist: {title} by {uploader}")
                        return info
                except json.JSONDecodeError:
                    continue

            # Fallback: use first entry data
            try:
                data = json.loads(lines[0])
                title = data.get('title', 'Playlist')
                uploader = data.get('uploader') or data.get('channel', 'Unknown')

                title = sanitize_filename(title, 50)
                uploader = sanitize_filename(uploader, 30)

                info = PlaylistInfo(
                    title=title,
                    uploader=uploader,
                    url=url
                )

                ui.verbose(f"Using fallback playlist info: {title} by {uploader}")
                return info
            except:
                pass

    except Exception as e:
        ui.debug(f"Could not fetch playlist info: {e}")

    return None


def fetch_playlist_entries(config: AppConfig, ui: UI, url: str) -> List[Dict]:
    """Fetch playlist entries."""
    ui.progress("Fetching playlist entries")

    cmd = get_ytdlp_cmd() + [
        '--flat-playlist',
        '--dump-json',
        '--no-warnings',
        '--extractor-args', 'youtube:player_client=android,web',
        url
    ]

    if config.is_debug:
        cmd.insert(len(get_ytdlp_cmd()), '--verbose')

    player_clients = ['android', 'web', 'ios']

    for attempt, client in enumerate(player_clients, 1):
        try:
            ui.verbose(f"Attempt {attempt}/{len(player_clients)} with player_client={client}")
            cmd[cmd.index('--extractor-args') + 1] = f'youtube:player_client={client}'

            result = run_command(cmd, timeout=config.fetch_timeout)

            if result.returncode != 0:
                ui.debug(f"Error: {result.stderr[:200]}")
                if attempt < len(player_clients):
                    time.sleep(1)
                continue

            entries = []
            for line in result.stdout.strip().split('\n'):
                if line:
                    try:
                        entry = json.loads(line)
                        entries.append(entry)
                    except json.JSONDecodeError:
                        continue

            if entries:
                ui.success(f"Found {len(entries)} videos in playlist")
                return entries
            else:
                ui.debug("No entries found")
                if attempt < len(player_clients):
                    time.sleep(1)

        except subprocess.TimeoutExpired:
            ui.warning(f"Timeout (attempt {attempt}/{len(player_clients)})")
            if attempt < len(player_clients):
                time.sleep(1)
        except Exception as e:
            ui.debug(f"Error: {e}")
            if attempt < len(player_clients):
                time.sleep(1)

    ui.error("Failed to fetch playlist entries")
    return []


# ============================================================================
# Task Processing
# ============================================================================

@dataclass
class Task:
    """Download task."""
    download_url: str
    metadata_url: Optional[str] = None
    meta_source: Optional[str] = None
    playlist_folder: Optional[str] = None
    entry: Optional[Dict] = None


def process_task(
    config: AppConfig,
    ui: UI,
    sources: Dict[str, MetadataSource],
    task: Task,
    task_idx: int,
    total: int,
    base_output_dir: Path,
    no_interactive: bool,
    report: Optional[ParallelReport] = None
) -> bool:
    """Process a single download task. Returns True on success, False on failure."""

    ui.task_header(task_idx, total, task.download_url)

    # Determine output directory
    if task.playlist_folder:
        output_dir = Path(task.playlist_folder)
        ui.debug(f"Using playlist folder: {output_dir}")
    else:
        output_dir = base_output_dir
        ui.debug(f"Using base output dir: {output_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)

    # Validate URL
    if not task.download_url.startswith(('http://', 'https://')):
        ui.error(f"Invalid URL: {task.download_url}")
        return False

    is_youtube_music = is_youtube_music_url(task.download_url)
    ui.info(f"Source: {'YouTube Music' if is_youtube_music else 'YouTube'}")

    # Fetch video info if not provided
    if not task.entry:
        ui.progress("Fetching video info")
        cmd = get_ytdlp_cmd() + [
            '--dump-json',
            '--skip-download',
            '--no-warnings',
            '--extractor-args', 'youtube:player_client=android,web',
            '--no-playlist',
            task.download_url
        ]

        try:
            result = run_command(cmd, timeout=config.network_timeout)
            if result.stdout.strip():
                task.entry = json.loads(result.stdout)
            else:
                ui.error("Could not fetch video info")
                return False
        except Exception as e:
            ui.error(f"Error fetching video info: {e}")
            return False

    # Select metadata
    metadata = select_metadata(
        config, ui, sources,
        task.entry,
        task.download_url,
        task.metadata_url,
        task.meta_source,
        no_interactive
    )

    if not metadata or not metadata.is_complete():
        ui.error("Could not get valid metadata")
        if report:
            report.add(f"{task.entry.get('title', 'Unknown')}", 'metadata_failed')
        return False

    ui.success(f"Metadata: {metadata.title} - {metadata.artist}")
    if report:
        report.add(f"{metadata.artist} - {metadata.title}", 'metadata_ok')

    # Generate output filename
    template = config.output_template
    filename = template.format(
        artist=sanitize_filename(metadata.artist, 50),
        title=sanitize_filename(metadata.title, 50),
        album=sanitize_filename(metadata.album or 'Unknown', 50),
        date=metadata.release_date[:4] if metadata.release_date and len(metadata.release_date) >= 4 else 'Unknown'
    )

    filename = sanitize_filename(filename, config.max_filename_length)
    output_file = output_dir / f"{filename}.{config.format}"

    # Check if exists
    if config.skip_existing and output_file.exists():
        ui.info(f"Skipping (already exists): {output_file.name}")
        if report:
            report.add(f"{metadata.artist} - {metadata.title}", 'skipped')
        return True  # Not a failure, intentionally skipped

    # Dry run
    if config.dry_run:
        ui.info(f"[DRY RUN] Would download to: {output_file}")
        if report:
            report.add(f"{metadata.artist} - {metadata.title}", 'skipped')
        return True

    # Download audio
    if not download_audio(config, ui, task.download_url, str(output_file), is_youtube_music):
        ui.error("Audio download failed")
        if report:
            report.add(f"{metadata.artist} - {metadata.title}", 'download_failed')
        return False

    if report:
        report.add(f"{metadata.artist} - {metadata.title}", 'download_ok')

    # Download cover
    cover_path = None
    if not config.no_cover and metadata.thumbnail:
        cover_path = output_dir / f"{filename}_cover.jpg"
        if not download_cover(config, ui, metadata.thumbnail, str(cover_path)):
            cover_path = None

    # Apply metadata
    if not apply_metadata(config, ui, str(output_file), metadata, str(cover_path) if cover_path else None):
        ui.warning("Metadata application failed")
        if report:
            report.add(f"{metadata.artist} - {metadata.title}", 'metadata_apply_failed')

    ui.success(f"Completed: {output_file.name}")
    return True


# ============================================================================
# Main Application
# ============================================================================

def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description=f'YouTube Music Metadata Scraping Downloader v{VERSION}',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ytmsd_v2.py https://youtube.com/watch?v=...
  ytmsd_v2.py https://music.youtube.com/watch?v=...
  ytmsd_v2.py tracks.csv
  ytmsd_v2.py https://youtube.com/playlist?list=...
  ytmsd_v2.py https://youtube.com/watch?v=... --meta ytm
  ytmsd_v2.py tracks.csv --mode parallel --output ./downloads
  ytmsd_v2.py URL1 URL2 URL3 --mode parallel

CSV format: download_url,metadata_url,meta_source
  (metadata_url and meta_source are optional)
        """
    )

    parser.add_argument('input', nargs='*', help='Download URL(s), playlist URL(s), or CSV file path')
    parser.add_argument('--meta', '-m', choices=['yt', 'ytm', 'it', 'mb'], help='Force metadata source')
    parser.add_argument('--meta_link', '-l', metavar='URL', help='Metadata URL to fetch metadata directly')
    parser.add_argument('--output', '-o', metavar='DIR', help='Output directory')
    parser.add_argument('--mode', '-M', choices=['sequential', 'parallel'], default='sequential',
                        help='Process mode (default: sequential)')
    parser.add_argument('--jobs', '-j', type=int, default=8, metavar='N',
                        help='Max concurrent downloads in parallel mode (default: 8)')
    parser.add_argument('--format', '-f', choices=['mp3', 'opus', 'm4a', 'flac'], default='mp3',
                        help='Audio format (default: mp3)')
    parser.add_argument('--quality', '-Q', type=int, choices=range(10), default=0, metavar='0-9',
                        help='Audio quality for mp3 (0=best, 9=worst, default: 0)')
    parser.add_argument('--output-template', '-t', metavar='TPL', default='{artist}_{title}',
                        help='Output filename template (default: {artist}_{title})')
    parser.add_argument('--no-cover', action='store_true', help='Skip cover art download')
    parser.add_argument('--dry-run', action='store_true', help='List what would be downloaded')
    parser.add_argument('--limit', type=int, metavar='N', help='For playlists: only process first N tracks')
    parser.add_argument('--skip-existing', action='store_true', help='Skip tracks whose output file exists')
    parser.add_argument('--timeout', type=int, default=60, metavar='SEC', help='Download timeout (default: 60)')
    parser.add_argument('--max-filename-length', type=int, default=200, metavar='N',
                        help='Max filename length (default: 200)')
    parser.add_argument('--debug', '-d', action='count', default=0,
                        help='Debug level: -d (verbose), -dd (debug)')
    parser.add_argument('--quiet', '-q', action='store_true', help='Quiet mode (errors only)')
    parser.add_argument('--no-search-retry', '-n', action='store_true',
                        help='Reduce retries from 3 to 1')
    parser.add_argument('--re-attempt-failed', '-rf', action='store_true',
                        help='Re-attempt failed downloads at the end')
    parser.add_argument('--settings', '-s', action='store_true', help='Open settings menu')
    parser.add_argument('--version', '-v', action='version', version=f'ytmsd v{VERSION}')

    args = parser.parse_args()

    # Determine debug level
    if args.quiet:
        debug_level = DebugLevel.QUIET
    elif args.debug >= 2:
        debug_level = DebugLevel.DEBUG
    elif args.debug == 1:
        debug_level = DebugLevel.VERBOSE
    else:
        debug_level = DebugLevel.NORMAL

    # Create config
    saved_config = load_config()
    config = AppConfig(
        debug_level=debug_level,
        no_search_retry=args.no_search_retry,
        jobs=args.jobs,
        format=args.format if args.format else saved_config.get('format', 'mp3'),
        quality=args.quality if args.quality != 0 else saved_config.get('quality', 0),
        timeout=args.timeout,
        no_cover=args.no_cover,
        dry_run=args.dry_run,
        skip_existing=args.skip_existing,
        max_filename_length=args.max_filename_length,
        output_template=args.output_template,
        fetch_timeout=saved_config.get('fetch_timeout', DEFAULT_FETCH_TIMEOUT),
        network_timeout=saved_config.get('timeout', DEFAULT_TIMEOUT)
    )

    # Create UI
    ui = UI(config)

    # Settings menu
    if args.settings:
        settings_menu(ui)
        return

    # Check for input
    if not args.input:
        ui.error("No input provided. Use --help for usage information.")
        sys.exit(1)

    # Show header
    ui.header(f"YouTube Music Metadata Scraping Downloader v{VERSION}")

    # Initialize metadata sources
    sources = {}
    enabled_sources = saved_config.get('sources', DEFAULT_CONFIG['sources'])

    if enabled_sources.get('youtube_music', True):
        sources['ytm'] = YouTubeMusicSource(config, ui)
    if enabled_sources.get('musicbrainz', True):
        sources['mb'] = MusicBrainzSource(config, ui)
    if enabled_sources.get('itunes', True):
        sources['it'] = iTunesSource(config, ui)

    ui.verbose(f"Enabled sources: {', '.join(sources.keys())}")

    # Process inputs and build task list
    tasks: List[Task] = []
    base_output_dir = Path(args.output) if args.output else Path.cwd()

    for input_arg in args.input:
        input_path = Path(input_arg)

        # CSV file
        if input_path.suffix.lower() == '.csv' and input_path.exists():
            ui.info(f"Processing CSV file: {input_arg}")
            try:
                with open(input_path, 'r', encoding='utf-8') as f:
                    reader = csv.reader(f)
                    for row in reader:
                        if not row or row[0].startswith('#'):
                            continue
                        download_url = clean_video_url(row[0].strip())
                        metadata_url = row[1].strip() if len(row) > 1 else args.meta_link
                        meta_source = row[2].strip() if len(row) > 2 else args.meta

                        tasks.append(Task(
                            download_url=download_url,
                            metadata_url=metadata_url,
                            meta_source=meta_source
                        ))
                ui.success(f"Loaded {len(tasks)} tasks from CSV")
            except Exception as e:
                ui.error(f"Error reading CSV: {e}")
                continue

        # Playlist URL
        elif is_playlist_url(input_arg):
            ui.info(f"Detected playlist: {input_arg}")

            # Fetch playlist info and create folder IMMEDIATELY
            playlist_info = fetch_playlist_info(config, ui, input_arg)

            # Create playlist folder right away
            playlist_folder = None
            if playlist_info:
                folder_name = playlist_info.get_folder_name()
                playlist_folder = str(base_output_dir / folder_name)
                ui.debug(f"Creating playlist folder: {playlist_folder}")
                Path(playlist_folder).mkdir(parents=True, exist_ok=True)
                ui.success(f"Created playlist folder: {folder_name}")
            else:
                ui.warning("No playlist info available, files will go to base directory")

            # Now fetch entries
            entries = fetch_playlist_entries(config, ui, input_arg)

            if not entries:
                ui.error("No entries found in playlist")
                continue

            # Apply limit to THIS playlist only
            if args.limit and args.limit > 0:
                entries = entries[:args.limit]
                ui.info(f"Limited to first {args.limit} tracks")

            # Add tasks for this playlist
            for entry in entries:
                clean_url = clean_video_url(entry.get('url', ''))
                tasks.append(Task(
                    download_url=clean_url,
                    metadata_url=args.meta_link,
                    meta_source=args.meta,
                    playlist_folder=playlist_folder,
                    entry=entry
                ))

        # Single URL
        else:
            if not input_arg.startswith(('http://', 'https://')):
                ui.error(f"Invalid URL: {input_arg}")
                continue

            tasks.append(Task(
                download_url=clean_video_url(input_arg),
                metadata_url=args.meta_link,
                meta_source=args.meta
            ))

    if not tasks:
        ui.error("No valid tasks to process")
        sys.exit(1)

    ui.info(f"Total tasks: {len(tasks)}")
    ui.separator()

    # Process tasks
    no_interactive = (args.mode == 'parallel')
    failed_tasks: List[Task] = []
    failed_lock = threading.Lock()

    def run_task(task: Task, idx: int, total: int, report: Optional[ParallelReport]) -> bool:
        """Wrapper that captures failed tasks."""
        try:
            success = process_task(
                config, ui, sources, task, idx, total,
                base_output_dir, no_interactive, report
            )
            if not success:
                with failed_lock:
                    failed_tasks.append(task)
            return success
        except Exception as e:
            ui.error(f"Task error: {e}")
            if config.is_debug:
                traceback.print_exc(file=sys.stderr)
            with failed_lock:
                failed_tasks.append(task)
            return False

    if args.mode == 'parallel':
        ui.info(f"Processing in parallel mode (max {config.jobs} concurrent)")
        max_workers = min(len(tasks), max(1, config.jobs))
        report = ParallelReport(ui)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_task, task, i, len(tasks), report): task
                for i, task in enumerate(tasks, 1)
            }
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as e:
                    ui.error(f"Task error: {e}")
                    if config.is_debug:
                        traceback.print_exc(file=sys.stderr)

        report.print_report()
    else:
        for i, task in enumerate(tasks, 1):
            run_task(task, i, len(tasks), None)

    # Re-attempt failed tasks if requested
    if args.re_attempt_failed and failed_tasks:
        ui.separator()
        ui.header(f"Re-attempting {len(failed_tasks)} Failed Task(s)")

        retry_failed: List[Task] = []
        retry_report = ParallelReport(ui) if args.mode == 'parallel' else None

        if args.mode == 'parallel':
            max_workers = min(len(failed_tasks), max(1, config.jobs))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        process_task,
                        config, ui, sources, task, i, len(failed_tasks),
                        base_output_dir, no_interactive, retry_report
                    ): task
                    for i, task in enumerate(failed_tasks, 1)
                }
                for future in as_completed(futures):
                    try:
                        success = future.result()
                        if not success:
                            retry_failed.append(futures[future])
                    except Exception as e:
                        ui.error(f"Retry task error: {e}")
                        retry_failed.append(futures[future])
            if retry_report:
                retry_report.print_report()
        else:
            for i, task in enumerate(failed_tasks, 1):
                try:
                    success = process_task(
                        config, ui, sources, task, i, len(failed_tasks),
                        base_output_dir, no_interactive, None
                    )
                    if not success:
                        retry_failed.append(task)
                except Exception as e:
                    ui.error(f"Retry task error: {e}")
                    retry_failed.append(task)

        # Retry summary
        ui.separator()
        retried = len(failed_tasks)
        recovered = retried - len(retry_failed)
        ui.success(f"Re-attempt summary: {recovered}/{retried} recovered")
        if retry_failed:
            ui.warning(f"Still failed after retry: {len(retry_failed)}")
            for t in retry_failed:
                ui.error(f"  → {t.download_url}")
    elif args.re_attempt_failed and not failed_tasks:
        ui.info("No failed tasks to re-attempt — all succeeded!")

    # Final summary
    total_failed = len(failed_tasks)
    total_ok = len(tasks) - total_failed
    ui.separator()
    if total_failed == 0:
        ui.success(f"All {len(tasks)} task(s) completed successfully")
    else:
        ui.success(f"Completed: {total_ok}/{len(tasks)}")
        if not args.re_attempt_failed:
            ui.info("Tip: Use --re-attempt-failed (-rf) to retry failed tasks automatically")

    ui.header("Done!")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{Fore.YELLOW}Interrupted by user{Style.RESET_ALL}")
        sys.exit(1)
    except Exception as e:
        print(f"{Fore.RED}Fatal error: {e}{Style.RESET_ALL}")
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)
