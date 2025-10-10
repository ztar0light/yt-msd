#!/usr/bin/env python3
"""
YouTube Music Metadata Scraping Downloader (ytmsd)
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
import platform
import tempfile
import os

# Configuration file path
CONFIG_FILE = Path.home() / '.ytmsd_config.json'

# Default configuration for metadata sources and settings
DEFAULT_CONFIG = {
    'sources': {
        'youtube_music': True,
        'musicbrainz': True,
        'itunes': False
    },
    'timeout': 15,
    'fetch_timeout': 120,
    'cover_size': '600x600'
}

def load_config() -> Dict:
    """Load configuration from ~/.ytmsd_config.json or return default config."""
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
    """Save configuration to ~/.ytmsd_config.json."""
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
        print("\nytmsd Settings")
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
        return metadata.get('thumbnail')

class YouTubeMusicSource(MetadataSource):
    """Handles metadata scraping and audio downloading from YouTube Music."""
    def search(self, query: str) -> List[Dict[str, Any]]:
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
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"Search attempt {attempt + 1}/{max_retries}")
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
                print(f"YouTube Music search timed out (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(2)
            except Exception as e:
                print(f"Error searching YouTube Music: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if '--debug' in sys.argv:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(2)
        print("All YouTube Music search attempts failed", file=sys.stderr)
        return []
    
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        print(f"Fetching YouTube Music metadata from: {url}")
        cmd = [
            'yt-dlp',
            '--dump-json',
            '--skip-download',
            '--no-warnings',
            '--extractor-args', 'youtube:player_client=web_music,android',
            url
        ]
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"Metadata fetch attempt {attempt + 1}/{max_retries}")
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
                if metadata['title'] and metadata['artist']:
                    print("Metadata fetched from YouTube Music")
                    return metadata
                else:
                    print("Insufficient metadata from YouTube Music, falling back to YouTube")
                    return None
            except subprocess.TimeoutExpired:
                print(f"YouTube Music metadata fetch timed out (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(2)
            except Exception as e:
                print(f"Error fetching YouTube Music metadata: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if '--debug' in sys.argv:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(2)
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
    
    def search(self, query: str) -> List[Dict[str, Any]]:
        print(f"Searching MusicBrainz for: {query}")
        url = f"{self.BASE_URL}/recording/?query={urllib.parse.quote(query)}&fmt=json&limit=3"
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"Search attempt {attempt + 1}/{max_retries}")
                req = urllib.request.Request(url, headers={'User-Agent': 'ytmsd/1.0'})
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
                print(f"Error searching MusicBrainz: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if '--debug' in sys.argv:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(2)
        print("All MusicBrainz search attempts failed", file=sys.stderr)
        return []
    
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        print(f"Fetching MusicBrainz metadata from: {url}")
        match = re.search(r'/recording/([a-f0-9-]+)', url)
        if not match:
            return None
        
        mbid = match.group(1)
        api_url = f"{self.BASE_URL}/recording/{mbid}?inc=artists+releases&fmt=json"
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"Metadata fetch attempt {attempt + 1}/{max_retries}")
                req = urllib.request.Request(api_url, headers={'User-Agent': 'ytmsd/1.0'})
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
                if '--debug' in sys.argv:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(2)
        print("All MusicBrainz metadata fetch attempts failed", file=sys.stderr)
        return None
    
    def get_cover_url(self, metadata: Dict[str, Any]) -> Optional[str]:
        release_mbid = metadata.get('release_mbid')
        if not release_mbid:
            return None
        cover_url = f"{self.COVER_ART_URL}/{release_mbid}/front"
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"Cover art check attempt {attempt + 1}/{max_retries}")
                req = urllib.request.Request(cover_url, headers={'User-Agent': 'ytmsd/1.0'}, method='HEAD')
                with urllib.request.urlopen(req, timeout=DEFAULT_CONFIG['timeout']) as response:
                    if response.getcode() == 200:
                        return cover_url
            except Exception as e:
                print(f"Error checking cover art: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(2)
        print("No cover art found in Cover Art Archive", file=sys.stderr)
        return None

class iTunesSource(MetadataSource):
    BASE_URL = "https://itunes.apple.com/search"
    
    def search(self, query: str) -> List[Dict[str, Any]]:
        print(f"Searching iTunes for: {query}")
        params = urllib.parse.urlencode({
            'term': query,
            'media': 'music',
            'entity': 'song',
            'limit': 3
        })
        url = f"{self.BASE_URL}?{params}"
        max_retries = 3
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
                    return results
            except Exception as e:
                print(f"Error searching iTunes: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
                if '--debug' in sys.argv:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(2)
        print("All iTunes search attempts failed", file=sys.stderr)
        return []
    
    def get_metadata(self, url: str) -> Optional[Dict[str, Any]]:
        print(f"Fetching iTunes metadata from: {url}")
        match = re.search(r'id(\d+)', url)
        if not match:
            return None
        track_id = match.group(1)
        lookup_url = f"https://itunes.apple.com/lookup?id={track_id}&entity=song"
        max_retries = 3
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
                if '--debug' in sys.argv:
                    traceback.print_exc(file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying...")
                    time.sleep(2)
        print("All iTunes metadata fetch attempts failed", file=sys.stderr)
        return None

def download_audio(url: str, output_path: str, is_youtube_music: bool = False) -> bool:
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
    if '--debug' in sys.argv:
        cmd.insert(-2, '--verbose')
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"Downloading (Attempt {attempt + 1}/{max_retries})...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['fetch_timeout'])
            if result.returncode == 0:
                print("Audio download complete")
                return True
            else:
                print(f"Download failed: {result.stderr}", file=sys.stderr)
                if attempt < max_retries - 1:
                    print("Retrying with alternative method...")
                    cmd[cmd.index('--extractor-args') + 1] = 'youtube:player_client=ios'
        except subprocess.TimeoutExpired:
            print(f"Download timed out after {DEFAULT_CONFIG['fetch_timeout']} seconds (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(2)
        except Exception as e:
            print(f"Error downloading audio: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(2)
    print("All audio download attempts failed", file=sys.stderr)
    return False

def download_cover(url: str, output_path: str) -> bool:
    print(f"Attempting to download cover from: {url}")
    user_agents = [
        'ytmsd/1.0',
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
            print(f"Error downloading cover: {e} (attempt {attempt}/{max_retries})", file=sys.stderr)
            if '--debug' in sys.argv:
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
    
    output_path = audio_path.parent / f"{audio_path.stem}.tagged.mp3"
    cmd = ['ffmpeg', '-i', str(audio_path), '-y', '-loglevel', 'error']
    
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
                    'ffmpeg', '-i', str(cover_path), '-y', '-loglevel', 'error',
                    '-filter_complex', "crop='min(iw,ih):min(iw,ih):(iw-min(iw,ih))/2:(ih-min(iw,ih))/2',scale=600:600",
                    str(cover_fixed)
                ]
                print(f"Running FFmpeg command: {' '.join(ffmpeg_cmd)}")
                subprocess.run(ffmpeg_cmd, check=True, capture_output=True, encoding='utf-8', errors='replace')
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
        result = subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')
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
        if '--debug' in sys.argv:
            traceback.print_exc(file=sys.stderr)
        return False

def display_results(results: List[Dict[str, Any]]):
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
                return -1
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
    
    title = re.sub(r'\s*\(Official.*?\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\[Official.*?\]', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?Audio\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?Video\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*\(.*?MV\)', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*MV\s*', '', title, flags=re.IGNORECASE)
    title = re.sub(r'\s*[\(\[]?\s*f(ea)?t\.?\s+.*?[\)\]]?', '', title, flags=re.IGNORECASE)
    title = re.sub(r'[^\w\s\-/&]', '', title)
    
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

def get_youtube_fallback_metadata(entry: Dict, url: str) -> Dict[str, Any]:
    print(f"Fetching YouTube metadata for: {url}")
    cmd = [
        'yt-dlp',
        '--dump-json',
        '--skip-download',
        '--no-warnings',
        '--extractor-args', 'youtube:player_client=android',
        url
    ]
    if '--debug' in sys.argv:
        cmd.insert(-2, '--verbose')
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"Metadata fetch attempt {attempt + 1}/{max_retries}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['timeout'])
            data = json.loads(result.stdout)
            artist = data.get('uploader', 'Unknown')
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
        except subprocess.TimeoutExpired:
            print(f"YouTube metadata fetch timed out (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(2)
        except Exception as e:
            print(f"Error fetching YouTube metadata: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
            if '--debug' in sys.argv:
                traceback.print_exc(file=sys.stderr)
            if attempt < max_retries - 1:
                print("Retrying...")
                time.sleep(2)
    print("All YouTube metadata fetch attempts failed, using entry data", file=sys.stderr)
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
            display_name = 'iTunes' if name == 'itunes' else name.replace('_', ' ').title()
            print(f"Enabled: {display_name}")
    
    return sources

def check_thumbnail_url(url: str) -> bool:
    print(f"Checking thumbnail availability: {url}")
    max_retries = 3
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
                time.sleep(2)
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
        results.sort(key=lambda r: SequenceMatcher(None, query.lower(), (r.get('artist', '') + ' ' + r.get('title', '')).lower()).ratio(), reverse=True)
        print(f"Metadata found from {source_name}")
        return results[0]
    
    print(f"No metadata found from {source_name}, falling back to YouTube metadata")
    return get_youtube_fallback_metadata(entry, youtube_url)

def is_youtube_music_url(url: str) -> bool:
    return 'music.youtube.com' in url.lower()

def get_youtube_url_from_ytm(url: str) -> str:
    return url.replace('music.youtube.com', 'youtube.com')

def is_playlist_url(url: str) -> bool:
    return 'list=' in url or '/playlist?' in url

def clean_video_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    query = urllib.parse.parse_qs(parsed.query)
    query.pop('list', None)
    query.pop('pp', None)
    clean_query = urllib.parse.urlencode(query, doseq=True)
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, clean_query, parsed.fragment))

def process_track(entry: Dict, sources: List[MetadataSource], metadata_url: Optional[str], output_dir: Path, meta_source: Optional[str] = None, is_youtube_music: bool = False):
    video_url = entry.get('webpage_url') or entry.get('url')
    youtube_url = get_youtube_url_from_ytm(video_url) if is_youtube_music else video_url
    print(f"\nProcessing track: {entry.get('title', 'Unknown')}")
    print(f"URL: {video_url}")
    
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
    if is_youtube_music and not meta_source:
        print(f"Attempting YouTube Music metadata fetch from: {video_url}")
        ytm_source = YouTubeMusicSource()
        metadata = ytm_source.get_metadata(video_url)
        if not metadata:
            print("Falling back to YouTube metadata for YouTube Music URL")
            metadata = get_youtube_fallback_metadata(entry, youtube_url)
    
    if not metadata and meta_source:
        valid_sources = {'yt', 'ytm', 'mb', 'it'}
        if meta_source.lower() in valid_sources:
            print(f"Using specified metadata source: {meta_source}")
            metadata = get_metadata_from_source(meta_source.lower(), sources, query, entry, youtube_url)
        else:
            print(f"Invalid meta_source '{meta_source}' in CSV, using default behavior", file=sys.stderr)
    
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
    
    if not metadata:
        print("Performing metadata search...")
        for source in sources:
            results = source.search(query)
            all_results.extend(results)
        
        if all_results:
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
                            print("No results for user query, using YouTube metadata")
                            metadata = get_youtube_fallback_metadata(entry, youtube_url)
                else:
                    print("No input provided, using YouTube metadata")
                    metadata = get_youtube_fallback_metadata(entry, youtube_url)
            else:
                metadata = all_results[choice - 1]
        else:
            print("No metadata found from any source")
            choice = get_user_choice(0, first_time=True, no_results=True)
            if choice == 0:
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
                            print("No results for user query, using YouTube metadata")
                            metadata = get_youtube_fallback_metadata(entry, youtube_url)
                else:
                    print("No input provided, using YouTube metadata")
                    metadata = get_youtube_fallback_metadata(entry, youtube_url)
            else:
                print("Using YouTube metadata")
                metadata = get_youtube_fallback_metadata(entry, youtube_url)
    
    title = metadata.get('title', 'Unknown')
    artist = metadata.get('artist', 'Unknown')
    safe_title = re.sub(r'[^\w\s-]', '', title).strip().replace(' ', '_')
    safe_artist = re.sub(r'[^\w\s-]', '', artist).strip().replace(' ', '_')
    output_file = output_dir / f"{safe_artist}_{safe_title}.mp3"
    
    print(f"Downloading to: {output_file}")
    
    if download_audio(youtube_url, str(output_file), is_youtube_music):
        cover_path = None
        if metadata.get('thumbnail'):
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
            print(f"Track processed successfully: {title} by {artist}")
        else:
            print(f"Failed to apply metadata for {title} by {artist}", file=sys.stderr)
        
        if cover_path and Path(cover_path).exists():
            Path(cover_path).unlink(missing_ok=True)
    else:
        print(f"Failed to download audio for {title} by {artist}", file=sys.stderr)

def main():
    print("Starting ytmsd - YouTube Music Metadata Scraping Downloader")
    print(f"Current time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    try:
        if len(sys.argv) < 2 or '--help' in sys.argv:
            print("Usage: ytmsd [download_url_or_csv] [--meta yt|ytm|it|mb] [--meta_link link] [--debug]")
            print("       ytmsd --settings")
            print("\nExamples:")
            print("  ytmsd https://youtube.com/watch?v=...")
            print("  ytmsd https://music.youtube.com/watch?v=...")
            print("  ytmsd tracks.csv")
            print("  ytmsd https://youtube.com/playlist?list=...")
            print("  ytmsd https://music.youtube.com/watch?v=... --meta ytm")
            print("  ytmsd https://youtube.com/watch?v=... --meta_link https://music.youtube.com/watch?v=...")
            print("  CSV format: download_url,metadata_url,meta_source (metadata_url and meta_source optional)")
            sys.exit(1)
        
        if sys.argv[1] == '--settings':
            settings_menu()
            sys.exit(0)
        
        input_arg = sys.argv[1]
        meta_source = None
        metadata_url = None
        
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
        
        print("Checking dependencies...")
        try:
            result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True)
            print(f"yt-dlp found: {result.stdout.strip()}")
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            print(f"Error: yt-dlp not found in PATH: {e}", file=sys.stderr)
            print("Install it with: pip install -U yt-dlp", file=sys.stderr)
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
        
        config = load_config()
        sources = get_enabled_sources(config)
        
        if not sources:
            print("No metadata sources enabled. Using YouTube metadata as fallback.")
        
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
        elif is_playlist_url(input_arg):
            print(f"Detected playlist URL: {input_arg}")
            with tempfile.NamedTemporaryFile(mode='w+', suffix='.csv', delete=False) as temp_csv:
                csv_writer = csv.writer(temp_csv)
                cmd = [
                    'yt-dlp',
                    '--flat-playlist',
                    '--get-id',
                    '--no-warnings',
                    '--extractor-args', 'youtube:player_client=android',
                    input_arg
                ]
                if '--debug' in sys.argv:
                    cmd.insert(-1, '--verbose')
                max_retries = 3
                player_clients = ['android', 'ios', 'web']
                video_ids = []
                for attempt, client in enumerate(player_clients, 1):
                    try:
                        print(f"Fetching playlist video IDs (attempt {attempt}/{max_retries}) with player_client={client}...")
                        cmd[cmd.index('--extractor-args') + 1] = f'youtube:player_client={client}'
                        print(f"Executing command: {' '.join(cmd)}")
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['fetch_timeout'])
                        if result.returncode != 0:
                            print(f"Error fetching playlist IDs: {result.stderr}", file=sys.stderr)
                            if attempt < max_retries:
                                print(f"Retrying with player_client={player_clients[attempt]}...")
                                time.sleep(2)
                            continue
                        video_ids = list(filter(None, [vid.strip() for vid in result.stdout.splitlines()]))
                        if not video_ids:
                            print("No video IDs found in playlist", file=sys.stderr)
                            if attempt < max_retries:
                                print(f"Retrying with player_client={player_clients[attempt]}...")
                                time.sleep(2)
                            continue
                        for vid in video_ids:
                            clean_url = f"https://www.youtube.com/watch?v={vid}"
                            csv_writer.writerow([clean_url, metadata_url, meta_source])
                        temp_csv.flush()
                        break
                    except subprocess.TimeoutExpired:
                        print(f"Playlist fetch timed out after {DEFAULT_CONFIG['fetch_timeout']} seconds (attempt {attempt}/{max_retries})", file=sys.stderr)
                        if attempt < max_retries:
                            print(f"Retrying with player_client={player_clients[attempt]}...")
                            time.sleep(2)
                    except Exception as e:
                        print(f"Error fetching playlist: {e} (attempt {attempt}/{max_retries})", file=sys.stderr)
                        if '--debug' in sys.argv:
                            traceback.print_exc(file=sys.stderr)
                        if attempt < max_retries:
                            print(f"Retrying with player_client={player_clients[attempt]}...")
                            time.sleep(2)
                
                if not video_ids:
                    print(f"Failed to fetch playlist entries for {input_arg} after {max_retries} attempts", file=sys.stderr)
                    temp_csv.close()
                    os.unlink(temp_csv.name)
                    sys.exit(1)
                
                temp_csv.seek(0)
                reader = csv.reader(temp_csv)
                for row in reader:
                    if not row:
                        print("Skipping empty row in temporary CSV", file=sys.stderr)
                        continue
                    download_url = row[0].strip()
                    if not download_url:
                        print("Skipping empty download_url in temporary CSV", file=sys.stderr)
                        continue
                    metadata_url_csv = row[1].strip() if len(row) > 1 else None
                    meta_source_csv = row[2].strip() if len(row) > 2 else None
                    tasks.append((download_url, metadata_url_csv or metadata_url, meta_source_csv or meta_source))
                print(f"Found {len(tasks)} videos in playlist")
                temp_csv.close()
            
            try:
                os.unlink(temp_csv.name)
                print(f"Temporary CSV file {temp_csv.name} deleted")
            except Exception as e:
                print(f"Error deleting temporary CSV file: {e}", file=sys.stderr)
        else:
            if not input_arg.startswith(('http://', 'https://')):
                print(f"Invalid download URL: {input_arg}. Must start with http:// or https://", file=sys.stderr)
                sys.exit(1)
            tasks = [(clean_video_url(input_arg), metadata_url, meta_source)]
        
        for task_idx, (download_url, metadata_url, meta_source) in enumerate(tasks, 1):
            print(f"\nProcessing task {task_idx}/{len(tasks)}: {download_url}")
            
            if not download_url.startswith(('http://', 'https://')):
                print(f"Invalid download URL: {download_url}. Skipping task.", file=sys.stderr)
                continue
            
            is_youtube_music = is_youtube_music_url(download_url)
            print(f"Source: {'YouTube Music' if is_youtube_music else 'YouTube'}")
            
            print("Fetching info from URL...")
            cmd = [
                'yt-dlp',
                '--dump-json',
                '--skip-download',
                '--no-warnings',
                '--extractor-args', 'youtube:player_client=android',
                '--no-playlist',
                download_url
            ]
            if '--debug' in sys.argv:
                cmd.insert(-1, '--verbose')
            
            max_retries = 3
            player_clients = ['android', 'ios', 'web']
            entries = []
            for attempt, client in enumerate(player_clients, 1):
                try:
                    print(f"Fetch attempt {attempt}/{max_retries} with player_client={client}")
                    cmd[cmd.index('--extractor-args') + 1] = f'youtube:player_client={client}'
                    print(f"Executing command: {' '.join(cmd)}")
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['fetch_timeout'])
                    if result.returncode != 0:
                        print(f"Error fetching info: {result.stderr}", file=sys.stderr)
                        if attempt < max_retries:
                            print(f"Retrying with player_client={player_clients[attempt]}...")
                            time.sleep(2)
                        continue
                    for line in result.stdout.strip().split('\n'):
                        if line:
                            try:
                                entries.append(json.loads(line))
                            except json.JSONDecodeError as e:
                                print(f"Error parsing yt-dlp output: {e}", file=sys.stderr)
                                continue
                    if entries:
                        break
                    else:
                        print("No entries found in yt-dlp output", file=sys.stderr)
                        if attempt < max_retries:
                            print(f"Retrying with player_client={player_clients[attempt]}...")
                            time.sleep(2)
                except subprocess.TimeoutExpired:
                    print(f"Fetch timed out after {DEFAULT_CONFIG['fetch_timeout']} seconds (attempt {attempt}/{max_retries})", file=sys.stderr)
                    if attempt < max_retries:
                        print(f"Retrying with player_client={player_clients[attempt]}...")
                        time.sleep(2)
                except Exception as e:
                    print(f"Error processing URL {download_url}: {e} (attempt {attempt}/{max_retries})", file=sys.stderr)
                    if '--debug' in sys.argv:
                        traceback.print_exc(file=sys.stderr)
                    if attempt < max_retries:
                        print(f"Retrying with player_client={player_clients[attempt]}...")
                        time.sleep(2)
            
            if not entries:
                print(f"Failed to fetch entries for URL {download_url} after {max_retries} attempts", file=sys.stderr)
                continue
            
            output_dir = Path.cwd()
            playlist_title = None
            if is_playlist_url(input_arg):
                cmd = [
                    'yt-dlp',
                    '--flat-playlist',
                    '--get-title',
                    '--no-warnings',
                    '--extractor-args', 'youtube:player_client=android',
                    input_arg
                ]
                try:
                    print(f"Executing command for playlist title: {' '.join(cmd)}")
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=DEFAULT_CONFIG['fetch_timeout'])
                    titles = [t.strip() for t in result.stdout.strip().split('\n') if t.strip()]
                    if titles:
                        playlist_title = titles[0]
                        print(f"Playlist title: {playlist_title}")
                        safe_playlist = re.sub(r'[^\w\s-]', '', playlist_title).strip().replace(' ', '_')
                        output_dir = Path(safe_playlist)
                        output_dir.mkdir(exist_ok=True)
                    else:
                        print("Playlist title not found, using current directory")
                except Exception as e:
                    print(f"Error fetching playlist title: {e}, using current directory", file=sys.stderr)
            
            for entry in entries:
                process_track(entry, sources, metadata_url, output_dir, meta_source, is_youtube_music)
        
        print("\nAll tasks processed")
    
    except Exception as e:
        print(f"Fatal error in main: {e}", file=sys.stderr)
        if '--debug' in sys.argv:
            traceback.print_exc(file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()