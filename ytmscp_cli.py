#!/usr/bin/env python3
"""
YouTube Music Scraper and Downloader (ytmscp)
Downloads audio from YouTube or YouTube Music and scrapes metadata from various sources.
"""

import sys
import subprocess
import json
import re
import traceback
from pathlib import Path
from typing import Optional, Dict, List, Any
import urllib.request
import urllib.parse
from datetime import datetime
import time
from difflib import SequenceMatcher
import csv
import threading
import queue

# Configuration file path
CONFIG_FILE = Path.home() / '.ytmscp_config.json'

# Default configuration for metadata sources and settings
DEFAULT_CONFIG = {
    'sources': {
        'youtube_music': True,
        'musicbrainz': True,
        'itunes': False
    },
    'timeout': 15,
    'cover_size': '600x600'
}

def load_config() -> Dict:
    """Load configuration from ~/.ytmscp_config.json or return default config."""
    print("Loading configuration...")
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}", file=sys.stderr)
    print("Using default configuration")
    return DEFAULT_CONFIG.copy()

def save_config(config: Dict):
    """Save configuration to ~/.ytmscp_config.json."""
    print("Saving configuration...")
    try:
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
        print("Configuration saved")
    except Exception as e:
        print(f"Error saving config: {e}", file=sys.stderr)

def settings_menu():
    """Display an interactive menu to toggle metadata sources."""
    config = load_config()
    
    while True:
        print("\nytmscp Settings")
        print("=" * 40)
        print("\nMetadata Sources:")
        sources = config['sources']
        source_list = list(sources.keys())
        
        for i, (name, enabled) in enumerate(sources.items(), 1):
            status = "Enabled" if enabled else "Disabled"
            display_name = name.replace('_', ' ').title()
            print(f"  {i}. [{status}] {display_name}")
        
        print(f"\n  {len(source_list) + 1}. Save and Exit")
        print(f"  {len(source_list) + 2}. Exit without saving")
        
        try:
            choice = input("\nSelect option to toggle: ").strip()
            choice = int(choice)
            
            if choice == len(source_list) + 1:
                save_config(config)
                print("\nSettings saved")
                break
            elif choice == len(source_list) + 2:
                print("\nChanges discarded")
                break
            elif 1 <= choice <= len(source_list):
                source_name = source_list[choice - 1]
                config['sources'][source_name] = not config['sources'][source_name]
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
        """Return cover art URL from metadata."""
        return metadata.get('thumbnail')

class YouTubeMusicSource(MetadataSource):
    """Handles metadata scraping and audio downloading from YouTube Music."""
    def search(self, query: str) -> List[Dict[str, Any]]:
        """Search YouTube Music for metadata using yt-dlp."""
        print(f"Searching YouTube Music for: {query}")
        cmd = [
            'yt-dlp',
            '--dump-json',
            '--default-search', 'ytsearch3',
            '--skip-download',
            '--no-warnings',
            '--extractor-args', 'youtube:player_client=web_music,android',
            query
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['timeout'])
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
            return results[:3]
        except subprocess.TimeoutExpired:
            print("YouTube Music search timed out, skipping...", file=sys.stderr)
            return []
        except Exception as e:
            print(f"Error searching YouTube Music: {e}", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            return []
    
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from a specific YouTube Music URL."""
        print(f"Fetching YouTube Music metadata from: {url}")
        cmd = [
            'yt-dlp',
            '--dump-json',
            '--skip-download',
            '--no-warnings',
            '--extractor-args', 'youtube:player_client=web_music,android',
            url
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['timeout'])
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
            # Check if metadata is sufficient (title and artist are required)
            if metadata['title'] and metadata['artist']:
                print("Metadata fetched from YouTube Music")
                return metadata
            else:
                print("Insufficient metadata from YouTube Music, falling back to YouTube")
                return None
        except subprocess.TimeoutExpired:
            print("YouTube Music metadata fetch timed out", file=sys.stderr)
            return None
        except Exception as e:
            print(f"Error fetching YouTube Music metadata: {e}", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            return None
    
    def _select_thumbnail(self, data: Dict[str, Any]) -> Optional[str]:
        """Select the best thumbnail, preferring lh3.googleusercontent.com with width/height parameters."""
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
    """Handles metadata scraping from MusicBrainz."""
    BASE_URL = "https://musicbrainz.org/ws/2"
    COVER_ART_URL = "https://coverartarchive.org/release"
    
    def search(self, query: str) -> List[Dict[str, Any]]:
        """Search MusicBrainz for metadata."""
        print(f"Searching MusicBrainz for: {query}")
        url = f"{self.BASE_URL}/recording/?query={urllib.parse.quote(query)}&fmt=json&limit=3"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'ytmscp/1.0'})
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
                return results
        except Exception as e:
            print(f"Error searching MusicBrainz: {e}", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            return []
    
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from a specific MusicBrainz recording URL."""
        print(f"Fetching MusicBrainz metadata from: {url}")
        match = re.search(r'/recording/([a-f0-9-]+)', url)
        if not match:
            return None
        
        mbid = match.group(1)
        api_url = f"{self.BASE_URL}/recording/{mbid}?inc=artists+releases&fmt=json"
        try:
            req = urllib.request.Request(api_url, headers={'User-Agent': 'ytmscp/1.0'})
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
            print(f"Error fetching MusicBrainz metadata: {e}", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            return None
    
    def get_cover_url(self, metadata: Dict[str, Any]) -> Optional[str]:
        """Fetch cover art URL from Cover Art Archive using release MBID."""
        release_mbid = metadata.get('release_mbid')
        if not release_mbid:
            return None
        cover_url = f"{self.COVER_ART_URL}/{release_mbid}/front"
        try:
            req = urllib.request.Request(cover_url, headers={'User-Agent': 'ytmscp/1.0'}, method='HEAD')
            with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
                if response.getcode() == 200:
                    return cover_url
        except Exception:
            print("No cover art found in Cover Art Archive", file=sys.stderr)
            return None

class iTunesSource(MetadataSource):
    """Handles metadata scraping from iTunes/Apple Music."""
    BASE_URL = "https://itunes.apple.com/search"
    
    def search(self, query: str) -> List[Dict[str, Any]]:
        """Search iTunes for metadata."""
        print(f"Searching iTunes for: {query}")
        params = urllib.parse.urlencode({
            'term': query,
            'media': 'music',
            'entity': 'song',
            'limit': 3
        })
        url = f"{self.BASE_URL}?{params}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'ytmscp/1.0'})
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
                return results
        except Exception as e:
            print(f"Error searching iTunes: {e}", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            return []
    
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        """Fetch metadata from a specific iTunes URL."""
        print(f"Fetching iTunes metadata from: {url}")
        match = re.search(r'id(\d+)', url)
        if not match:
            return None
        track_id = match.group(1)
        lookup_url = f"https://itunes.apple.com/lookup?id={track_id}&entity=song"
        try:
            req = urllib.request.Request(lookup_url, headers={'User-Agent': 'ytmscp/1.0'})
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
            print(f"Error fetching iTunes metadata: {e}", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            return None

def download_audio(url: str, output_path: str, is_youtube_music: bool = False) -> bool:
    """Download audio from a YouTube or YouTube Music URL using yt-dlp with retries."""
    print(f"Preparing to download audio from: {url}")
    cmd = [
        'yt-dlp',
        '-x',
        '--audio-format', 'mp3',
        '--audio-quality', '0',
        '-f', 'bestaudio/best',
        '--extract-audio',
        '--no-playlist',
        '--no-warnings',
        '--prefer-free-formats',
        '--extractor-args', f'youtube:player_client={"web_music,android" if is_youtube_music else "android"}',
        '-o', output_path,
        url
    ]
    max_retries = 2
    for attempt in range(max_retries):
        try:
            print(f"Downloading (Attempt {attempt + 1}/{max_retries})...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['timeout'] * 2)
            if result.returncode == 0:
                print("Audio download complete")
                return True
            else:
                print(f"Download failed: {result.stderr}", file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying with alternative method...")
                    cmd[-3] = '--extractor-args youtube:player_client=ios'
        except subprocess.TimeoutExpired:
            print("Download timed out", file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(2)
        except Exception as e:
            print(f"Error downloading audio: {e}", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(2)
    print("All audio download attempts failed", file=sys.stderr)
    return False

def download_cover(url: str, output_path: str) -> bool:
    """Download cover art with retries and multiple User-Agents."""
    print(f"Attempting to download cover from: {url}")
    user_agents = [
        'ytmscp/1.0',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15'
    ]
    max_retries = len(user_agents)
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
            print(f"Error downloading cover (attempt {attempt}/{max_retries}): {e}", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            if attempt < max_retries:
                print("Retrying with different User-Agent...")
                time.sleep(1)
    print("All cover download attempts failed", file=sys.stderr)
    return False

def apply_metadata(audio_file: str, metadata: Dict[str, Any], cover_path: Optional[str] = None) -> bool:
    """Apply metadata and optional cover art to an audio file using FFmpeg."""
    print(f"Preparing to apply metadata to: {audio_file}")
    if not Path(audio_file).exists():
        print(f"Audio file not found: {audio_file}", file=sys.stderr)
        return False
    
    output_file = audio_file.replace('.mp3', '.tagged.mp3')
    cmd = ['ffmpeg', '-i', audio_file, '-y', '-loglevel', 'error']
    
    # Filter valid metadata
    metadata = {k: v for k, v in metadata.items() if v and isinstance(v, str)}
    
    # Process cover art if available
    if cover_path and Path(cover_path).exists():
        print(f"Processing cover art: {cover_path}")
        cover_url = metadata.get('thumbnail', '')
        use_original = True
        cover_fixed = cover_path.replace('.jpg', '.fixed.jpg')
        
        # Crop YouTube thumbnails (ytimg.com) but not YouTube Music thumbnails (lh3.googleusercontent.com)
        if 'ytimg.com' in cover_url and not ('lh3.googleusercontent.com' in cover_url and 'w' in cover_url and 'h' in cover_url):
            print("Detected YouTube thumbnail, applying crop and scale...")
            try:
                ffmpeg_cmd = [
                    'ffmpeg', '-i', str(cover_path), '-y', '-loglevel', 'error',
                    "-filter_complex", "crop='min(iw\\,ih):min(iw\\,ih):(iw-min(iw\\,ih))/2:(ih-min(iw\\,ih))/2',scale=600:600",
                    str(cover_fixed)
                ]
                print(f"Running FFmpeg command: {' '.join(ffmpeg_cmd)}")
                subprocess.run(ffmpeg_cmd, check=True, shell=False)
                print("Cover art processed")
                use_original = False
            except Exception as e:
                print(f"Could not process cover art: {e}. Falling back to original cover.", file=sys.stderr)
                if '--debug' in sys.argv:
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
    
    # Add metadata fields
    for key in ['title', 'artist', 'album']:
        if metadata.get(key):
            cmd.extend(['-metadata', f'{key}={metadata[key]}'])
    if metadata.get('release_date'):
        try:
            year = metadata['release_date'][:4] if len(metadata['release_date']) >= 4 else metadata['release_date']
            datetime.strptime(year, '%Y')  # Validate year
            cmd.extend(['-metadata', f'date={year}'])
        except ValueError:
            print(f"Invalid release date format: {metadata['release_date']}", file=sys.stderr)
    
    cmd.append(output_file)
    
    try:
        print("Applying metadata...")
        print(f"Running FFmpeg command: {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            Path(audio_file).unlink(missing_ok=True)
            Path(output_file).rename(audio_file)
            
            # Clean up temporary cover file
            if cover_path and Path(cover_path).exists():
                cover_fixed = cover_path.replace('.jpg', '.fixed.jpg')
                if Path(cover_fixed).exists():
                    Path(cover_fixed).unlink(missing_ok=True)
                if not use_original:
                    Path(cover_path).unlink(missing_ok=True)
            
            print("Metadata applied successfully")
            return True
        else:
            print(f"Failed to apply metadata: {result.stderr}", file=sys.stderr)
            return False
    except Exception as e:
        print(f"Error applying metadata: {e}", file=sys.stderr)
        if '--debug' in sys.argv:
            traceback.print_exc(file=sys.stderr)
        return False

def display_results(results: List[Dict[str, Any]]):
    """Display metadata search results for user selection."""
    print("\nFound the following matches:\n")
    for i, result in enumerate(results, 1):
        print(f"{i}. {result.get('title', 'Unknown')} - {result.get('artist', 'Unknown')}")
        if result.get('album'):
            print(f"   Album: {result['album']}")
        if result.get('release_date'):
            print(f"   Released: {result['release_date']}")
        print(f"   Source: {result['source']}")
        print()

def get_user_choice(max_choice: int, first_time: bool = False, no_results: bool = False) -> int:
    """Prompt user to select a metadata option with a 10-second countdown."""
    # Set prompt based on whether results are available
    prompt = f"Select option (1-{max_choice}, 0 for link/query, 00 for YouTube metadata): " if not no_results else "Enter 0 to provide link/name (timeout will use YouTube metadata): "
    timeout = 10
    result_queue = queue.Queue()
    
    def input_thread():
        try:
            choice_str = input(prompt).strip()
            result_queue.put(choice_str)
        except Exception:
            result_queue.put(None)
    
    thread = threading.Thread(target=input_thread)
    thread.daemon = True
    thread.start()
    
    print("Countdown: ", end='', flush=True)
    for i in range(timeout, 0, -1):
        print(f"{i}... ", end='', flush=True)
        time.sleep(1)
        if not result_queue.empty():
            break
    
    try:
        choice_str = result_queue.get_nowait()
    except queue.Empty:
        print("\nTimeout, selecting first option (1)" if max_choice > 0 else "\nTimeout, using YouTube metadata")
        return 1 if max_choice > 0 else 0
    
    if choice_str is None:
        print("\nInput error, selecting first option (1)" if max_choice > 0 else "\nInput error, using YouTube metadata")
        return 1 if max_choice > 0 else 0
    
    while True:
        try:
            if choice_str == "00":
                return -1  # Special value for YouTube metadata override
            choice = int(choice_str)
            if 0 <= choice <= max_choice:
                return choice
            else:
                print(f"Invalid, choose 0-{max_choice} or 00")
        except ValueError:
            print(f"Invalid, enter number 0-{max_choice} or 00")
        try:
            choice_str = input(prompt).strip()
        except KeyboardInterrupt:
            print("\nInput interrupted, selecting first option (1)" if max_choice > 0 else "\nInput interrupted, using YouTube metadata")
            return 1 if max_choice > 0 else 0

def manual_input() -> Dict[str, Any]:
    """Prompt user for manual metadata input with validation."""
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
    """Extract a clean search query from yt-dlp entry."""
    title = entry.get('title', '')
    uploader = entry.get('uploader', '')
    
    # Clean title by removing common suffixes and special characters
    title = re.sub(r'\s*\(Official.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Official.*?\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?Audio\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?Video\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?MV\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*MV\s*', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*[\(\[]?\s*f(ea)?t\.?\s+.*?[\)\]]?', '', title, flags=re.IGNORECASE)
    title = re.sub(r'[^\w\s\-/&]', '', title)
    
    # Clean uploader
    uploader = re.sub(r'\s*-\s*Topic', '', uploader, flags=re.IGNORECASE)
    uploader = re.sub(r'\s*VEVO', '', uploader, flags=re.IGNORECASE)
    uploader = re.sub(r'Official', '', uploader, flags=re.IGNORECASE)
    
    # Handle artist-title format in title
    if ' - ' in title:
        parts = title.split(' - ', 1)
        if len(parts) == 2:
            artist_part = parts[0].strip()
            title_part = parts[1].strip()
            return f"{artist_part} {title_part}"
    
    return f"{uploader} {title}".strip()

def get_youtube_fallback_metadata(entry: Dict, url: str) -> Dict[str, Any]:
    """Construct metadata from YouTube video entry, using YouTube URL."""
    print(f"Fetching YouTube metadata for: {url}")
    cmd = [
        'yt-dlp',
        '--dump-json',
        '--skip-download',
        '--no-warnings',
        '--extractor-args', 'youtube:player_client=android',
        url
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['timeout'])
        data = json.loads(result.stdout)
        artist = data.get('uploader', 'Unknown')
        # Clean artist
        artist = re.sub(r'\s*-\s*Topic', '', artist, flags=re.IGNORECASE)
        artist = re.sub(r'\s*VEVO', '', artist, flags=re.IGNORECASE)
        artist = re.sub(r'Official', '', artist, flags=re.IGNORECASE)
        print("Metadata fetched from YouTube")
        return {
            'title': data.get('title', 'Unknown'),
            'artist': artist,
            'album': None,
            'release_date': data.get('upload_date'),
            'thumbnail': data.get('thumbnail'),
            'source': 'YouTube Fallback'
        }
    except Exception as e:
        print(f"Error fetching YouTube metadata: {e}, using entry data", file=sys.stderr)
        if '--debug' in sys.argv:
            traceback.print_exc(file=sys.stderr)
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
            'source': 'YouTube Fallback'
        }

def get_enabled_sources(config: Dict) -> List[MetadataSource]:
    """Return a list of enabled metadata sources based on config."""
    print("Loading enabled metadata sources...")
    sources = []
    source_map = {
        'youtube_music': YouTubeMusicSource,
        'musicbrainz': MusicBrainzSource,
        'itunes': iTunesSource
    }
    
    for name, source_class in source_map.items():
        if config['sources'].get(name, False):
            sources.append(source_class())
            print(f"Enabled: {name.replace('_', ' ').title()}")
    
    return sources

def check_thumbnail_url(url: str) -> bool:
    """Check if a thumbnail URL is accessible."""
    print(f"Checking thumbnail availability: {url}")
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'ytmscp/1.0'}, method='HEAD')
        with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
            print(f"Thumbnail accessible: {url}")
            return response.getcode() == 200
    except Exception as e:
        print(f"Thumbnail not accessible: {e}", file=sys.stderr)
        return False

def get_metadata_from_source(source_name: str, sources: List[MetadataSource], query: str, entry: Dict, youtube_url: str) -> Optional[Dict[str, Any]]:
    """Fetch metadata from a specific source using a query."""
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
        results.sort(key=lambda r: SequenceMatcher(None, query.lower(), (r.get('artist', '') + ' ' + r.get('title', '')).lower()).ratio(), reverse=True)
        print(f"Metadata found from {source_name}")
        return results[0]
    
    print(f"No metadata found from {source_name}, falling back to YouTube metadata")
    return get_youtube_fallback_metadata(entry, youtube_url)

def is_youtube_music_url(url: str) -> bool:
    """Check if the URL is a YouTube Music URL."""
    return 'music.youtube.com' in url.lower()

def get_youtube_url_from_ytm(url: str) -> str:
    """Convert a YouTube Music URL to a YouTube URL."""
    return url.replace('music.youtube.com', 'youtube.com')

def process_track(entry: Dict, sources: List[MetadataSource], metadata_url: Optional[str], output_dir: Path, meta_source: Optional[str] = None, is_youtube_music: bool = False):
    """Process a single track: fetch metadata, download audio and cover, apply metadata."""
    video_url = entry.get('webpage_url') or entry.get('url')
    youtube_url = get_youtube_url_from_ytm(video_url) if is_youtube_music else video_url
    print(f"\nProcessing track: {entry.get('title', 'Unknown')}")
    print(f"URL: {video_url}")
    
    # Extract search query
    query = entry.get('track') or entry.get('title', '')
    artist = entry.get('artist') or entry.get('uploader', '')
    if artist:
        query = f"{artist} {query}"
    else:
        query = extract_search_query(entry)
    
    print(f"Using query: {query}")
    
    all_results = []
    youtube_metadata = entry
    
    metadata = None
    # Try YouTube Music metadata first if the source is YouTube Music and no --meta is specified
    if is_youtube_music and not meta_source:
        print(f"Attempting YouTube Music metadata fetch from: {video_url}")
        ytm_source = YouTubeMusicSource()
        metadata = ytm_source.get_metadata(video_url)
        if not metadata:
            print("Falling back to YouTube metadata for YouTube Music URL")
            metadata = get_youtube_fallback_metadata(entry, youtube_url)
    
    # Use --meta or CSV meta_source if specified
    if not metadata and meta_source:
        valid_sources = {'yt', 'ytm', 'mb', 'it'}
        if meta_source.lower() in valid_sources:
            print(f"Using specified metadata source: {meta_source}")
            metadata = get_metadata_from_source(meta_source.lower(), sources, query, entry, youtube_url)
        else:
            print(f"Invalid meta_source '{meta_source}' in CSV, using default behavior", file=sys.stderr)
    
    # Use --meta_link or CSV metadata_url if provided
    if not metadata and metadata_url:
        print(f"Attempting direct metadata fetch from: {metadata_url}")
        for source in sources:
            direct_meta = source.get_metadata(metadata_url)
            if direct_meta:
                metadata = direct_meta
                print("Direct metadata fetched successfully")
                break
        if not metadata:
            print("Direct metadata fetch failed, falling back to search")
    
    # Perform search if no metadata has been found
    if not metadata:
        print("Performing metadata search...")
        for source in sources:
            results = source.search(query)
            all_results.extend(results)
        
        if all_results:
            # Sort results by similarity
            def similarity(a, b):
                return SequenceMatcher(None, a.lower(), b.lower()).ratio()
            
            all_results.sort(key=lambda r: similarity(query, (r.get('artist', '') + ' ' + r.get('title', ''))), reverse=True)
            
            display_results(all_results)
            choice = get_user_choice(len(all_results), first_time=True)
            
            if choice == -1:
                print("User selected YouTube metadata")
                metadata = get_youtube_fallback_metadata(entry, youtube_url)
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
                            metadata = get_youtube_fallback_metadata(entry, youtube_url)
                    else:
                        print(f"Searching for user query: {user_input}")
                        new_results = []
                        for source in sources:
                            results = source.search(user_input)
                            new_results.extend(results)
                        if new_results:
                            new_results.sort(key=lambda r: similarity(user_input, (r.get('artist', '') + ' ' + r.get('title', ''))).lower(), reverse=True)
                            display_results(new_results)
                            choice = get_user_choice(len(new_results), first_time=False)
                            if choice == -1:
                                print("User selected YouTube metadata")
                                metadata = get_youtube_fallback_metadata(entry, youtube_url)
                            elif choice == 0:
                                print("No selection made, using YouTube metadata")
                                metadata = get_youtube_fallback_metadata(entry, youtube_url)
                            else:
                                metadata = new_results[choice - 1]
                        else:
                            print("No results from user query, using YouTube metadata")
                            metadata = get_youtube_fallback_metadata(entry, youtube_url)
                else:
                    print("No input provided, using YouTube metadata")
                    metadata = get_youtube_fallback_metadata(entry, youtube_url)
            else:
                metadata = all_results[choice - 1]
        else:
            print("No metadata found, waiting for user input or defaulting to YouTube metadata...")
            choice = get_user_choice(0, first_time=True, no_results=True)
            if choice == -1:
                print("User selected YouTube metadata")
                metadata = get_youtube_fallback_metadata(entry, youtube_url)
            elif choice == 0:
                print("No input provided, using YouTube metadata")
                metadata = get_youtube_fallback_metadata(entry, youtube_url)
            else:
                print("Using YouTube metadata")
                metadata = get_youtube_fallback_metadata(entry, youtube_url)
    
    print("\nSelected metadata:")
    for key, value in metadata.items():
        if value and key != 'source':
            print(f"  {key}: {value}")
    
    # Generate output filename
    safe_title = re.sub(r'[^\w\s-]', '', metadata.get('title', 'audio')).strip().replace(' ', '_')
    safe_artist = re.sub(r'[^\w\s-]', '', metadata.get('artist', 'unknown')).strip().replace(' ', '_')
    output_file = output_dir / f"{safe_artist}-{safe_title}.mp3"
    
    # Download audio, trying YouTube Music first if specified
    audio_downloaded = download_audio(video_url, str(output_file), is_youtube_music)
    if not audio_downloaded and is_youtube_music:
        print(f"Audio download failed from YouTube Music, trying YouTube: {youtube_url}")
        audio_downloaded = download_audio(youtube_url, str(output_file), False)
    
    if not audio_downloaded:
        print(f"Skipping track due to download failure: {entry.get('title')}")
        return
    
    # Download cover art
    cover_path = None
    cover_url = None
    source_instance = next((s for s in sources if s.__class__.__name__.lower().startswith(metadata['source'].lower().replace(' ', ''))), None)
    
    if source_instance and metadata['source'] != 'YouTube Fallback':
        cover_url = source_instance.get_cover_url(metadata)
        if cover_url:
            cover_path = output_dir / f"{safe_artist}-{safe_title}.jpg"
            print(f"\nDownloading cover art from {metadata['source']}...")
            if download_cover(cover_url, str(cover_path)):
                print("Cover art downloaded")
            else:
                cover_path = None
    
    # Fallback to YouTube thumbnail
    if not cover_path and entry.get('thumbnail'):
        thumbnail_urls = []
        base_url = entry['thumbnail']
        if 'ytimg.com' in base_url:
            thumbnail_urls = [
                base_url.replace('default.jpg', 'maxresdefault.jpg').replace('.webp', '.jpg'),
                base_url.replace('default.jpg', 'hqdefault.jpg').replace('.webp', '.jpg'),
                base_url.replace('.webp', '.jpg')
            ]
        else:
            thumbnail_urls = [base_url]
        
        for url in thumbnail_urls:
            print(f"Checking YouTube thumbnail: {url}")
            if check_thumbnail_url(url):
                cover_url = url
                cover_path = output_dir / f"{safe_artist}-{safe_title}.jpg"
                print(f"\nFalling back to YouTube thumbnail: {cover_url}")
                if download_cover(cover_url, str(cover_path)):
                    print("YouTube thumbnail downloaded")
                    break
                else:
                    cover_path = None
            else:
                print(f"Thumbnail not available: {url}")
    
    # Apply metadata
    apply_metadata(str(output_file), metadata, str(cover_path) if cover_path else None)

def main():
    """Main function to handle command-line arguments and process tracks."""
    print("Starting ytmscp - YouTube Music Scraper")
    print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        if len(sys.argv) < 2 or '--help' in sys.argv:
            print("Usage: ytmscp [download_url_or_csv] [--meta yt|ytm|it|mb] [--meta_link link] [--debug]")
            print("       ytmscp --settings")
            print("\nExamples:")
            print("  ytmscp https://youtube.com/watch?v=...")
            print("  ytmscp https://music.youtube.com/watch?v=...")
            print("  ytmscp tracks.csv")
            print("  ytmscp https://youtube.com/playlist?list=...")
            print("  ytmscp https://music.youtube.com/watch?v=... --meta ytm")
            print("  ytmscp https://youtube.com/watch?v=... --meta_link https://music.youtube.com/watch?v=...")
            print("  CSV format: download_url,metadata_url,meta_source (metadata_url and meta_source optional)")
            sys.exit(1)
        
        if sys.argv[1] == '--settings':
            settings_menu()
            sys.exit(0)
        
        input_arg = sys.argv[1]
        meta_source = None
        metadata_url = None
        
        # Parse command-line arguments
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == '--meta' and i + 1 < len(sys.argv):
                meta_source = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == '--meta_link' and i + 1 < len(sys.argv):
                metadata_url = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] not in ('--debug',):
                print(f"Invalid argument: {sys.argv[i]}", file=sys.stderr)
                sys.exit(1)
            else:
                i += 1
        
        # Check dependencies
        print("Checking dependencies...")
        try:
            result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True)
            print(f"yt-dlp found: {result.stdout.strip()}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Error: yt-dlp not found in PATH: {e}", file=sys.stderr)
            print("Install it with: pip install yt-dlp", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            sys.exit(1)
        
        try:
            result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
            print(f"ffmpeg found: {result.stdout.splitlines()[0]}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Warning: ffmpeg not found in PATH: {e}. Metadata tagging will be limited.", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
        
        # Load config and get enabled sources
        config = load_config()
        sources = get_enabled_sources(config)
        
        if not sources:
            print("No metadata sources enabled. Using YouTube metadata as fallback.")
        
        # Determine if input is a CSV file
        tasks = []
        if input_arg.endswith('.csv'):
            print(f"Reading CSV file: {input_arg}")
            try:
                with open(input_arg, newline='') as csvfile:
                    reader = csv.reader(csvfile)
                    for row in reader:
                        if not row:
                            print("Skipping empty CSV row", file=sys.stderr)
                            continue
                        download_url = row[0].strip()
                        if not download_url:
                            print("Skipping CSV row with empty download_url", file=sys.stderr)
                            continue
                        metadata_url_csv = row[1].strip() if len(row) > 1 else None
                        meta_source_csv = row[2].strip() if len(row) > 2 else None
                        tasks.append((download_url, metadata_url_csv or metadata_url, meta_source_csv or meta_source))
                print(f"Found {len(tasks)} valid tasks in CSV")
            except Exception as e:
                print(f"Error reading CSV file: {e}", file=sys.stderr)
                if '--debug' in sys.argv:
                    traceback.print_exc(file=sys.stderr)
                sys.exit(1)
        else:
            if not input_arg.startswith(('http://', 'https://')):
                print(f"Invalid download URL: {input_arg}. Must start with http:// or https://", file=sys.stderr)
                sys.exit(1)
            tasks = [(input_arg, metadata_url, meta_source)]
        
        # Process each task
        for task_idx, (download_url, metadata_url, meta_source) in enumerate(tasks, 1):
            print(f"\nProcessing task {task_idx}/{len(tasks)}: {download_url}")
            
            # Validate URL
            if not download_url.startswith(('http://', 'https://')):
                print(f"Invalid download URL: {download_url}. Skipping task.", file=sys.stderr)
                continue
            
            # Detect if the URL is YouTube Music
            is_youtube_music = is_youtube_music_url(download_url)
            print(f"Source: {'YouTube Music' if is_youtube_music else 'YouTube'}")
            
            # Fetch video/playlist data
            print("Fetching info from URL...")
            cmd = [
                'yt-dlp',
                '--dump-json',
                '--skip-download',
                '--no-warnings',
                '--extractor-args', f'youtube:player_client={"web_music,android" if is_youtube_music else "android"}',
                download_url
            ]
            try:
                print(f"Running yt-dlp command: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['timeout'] * 2)
                if result.returncode != 0:
                    print(f"Error fetching info: {result.stderr}", file=sys.stderr)
                    if '--debug' in sys.argv:
                        print(f"Debug: yt-dlp command: {' '.join(cmd)}", file=sys.stderr)
                    continue
                
                lines = result.stdout.strip().split('\n')
                entries = []
                for line in lines:
                    if line:
                        try:
                            entries.append(json.loads(line))
                        except json.JSONDecodeError as e:
                            print(f"Error parsing yt-dlp output: {e}", file=sys.stderr)
                            continue
                
                if not entries:
                    print("No entries found for URL", file=sys.stderr)
                    continue
                
                is_playlist = len(entries) > 1 or 'entries' in entries[0]
                playlist_title = entries[0].get('playlist_title') if is_playlist else None
                output_dir = Path.cwd()
                
                if is_playlist:
                    print("Detected playlist/album")
                    if playlist_title:
                        print(f"Playlist title: {playlist_title}")
                        safe_playlist = re.sub(r'[^\w\s-]', '', playlist_title).strip().replace(' ', '_')
                        output_dir = Path(safe_playlist)
                        output_dir.mkdir(exist_ok=True)
                    else:
                        print("Playlist title not found, using current directory")
                
                for entry in entries:
                    process_track(entry, sources, metadata_url, output_dir, meta_source, is_youtube_music)
            
            except Exception as e:
                print(f"Error processing URL {download_url}: {e}", file=sys.stderr)
                if '--debug' in sys.argv:
                    traceback.print_exc(file=sys.stderr)
                continue
        
        print("\nAll tasks processed")
    
    except Exception as e:
        print(f"Fatal error in main: {e}", file=sys.stderr)
        if '--debug' in sys.argv:
            traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()