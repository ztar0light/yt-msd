# Quick Wins - Immediate Improvements

These changes can be applied to the existing code with minimal risk and immediate benefit.

## 1. Add Retry Decorator (Saves ~500 lines)

Add this near the top of ytmsd.py after the color functions:

```python
from functools import wraps
from typing import Callable, TypeVar, Optional

T = TypeVar('T')

def with_retry(func: Callable[..., T]) -> Callable[..., T]:
    """
    Decorator that adds retry logic to any function.
    Uses OPTIONS.no_search_retry to determine retry count.
    """
    @wraps(func)
    def wrapper(*args, **kwargs) -> T:
        max_retries = 1 if OPTIONS.no_search_retry else 3
        last_exception = None
        
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except subprocess.TimeoutExpired as e:
                last_exception = e
                if attempt < max_retries - 1:
                    print(color_warning(f"Timeout (attempt {attempt + 1}/{max_retries})"))
                    print(color_dim("Retrying..."))
                    time.sleep(1)
            except (json.JSONDecodeError, ValueError) as e:
                # Don't retry on parse errors
                raise
            except Exception as e:
                last_exception = e
                if attempt < max_retries - 1:
                    print(color_warning(f"Error (attempt {attempt + 1}/{max_retries}): {e}"))
                    if OPTIONS.debug:
                        traceback.print_exc(file=sys.stderr)
                    print(color_dim("Retrying..."))
                    time.sleep(1)
        
        # All retries failed
        if last_exception:
            raise last_exception
        return None
    
    return wrapper
```

Then replace retry loops with decorator:

**Before** (15 lines):
```python
max_retries = 1 if OPTIONS.no_search_retry else 3
for attempt in range(max_retries):
    try:
        result = _run_cmd(cmd, timeout=DEFAULT_CONFIG['timeout'])
        # ... process result
        return results
    except subprocess.TimeoutExpired:
        print(f"Timeout (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
        if attempt < max_retries - 1:
            print("Retrying...")
            time.sleep(1)
    except Exception as e:
        print(f"Error: {e} (attempt {attempt + 1}/{max_retries})", file=sys.stderr)
        if attempt < max_retries - 1:
            print("Retrying...")
            time.sleep(1)
return []
```

**After** (5 lines):
```python
@with_retry
def _do_search():
    result = _run_cmd(cmd, timeout=DEFAULT_CONFIG['timeout'])
    # ... process result
    return results

return _do_search()
```

## 2. Extract Common Patterns

### 2.1 Max Retries Helper
```python
def get_max_retries() -> int:
    """Get max retries based on OPTIONS.no_search_retry."""
    return 1 if OPTIONS.no_search_retry else 3
```

Replace all instances of `1 if OPTIONS.no_search_retry else 3` with `get_max_retries()`

### 2.2 Sanitize Filename Helper
```python
def sanitize_for_filename(text: str, max_length: int = 100) -> str:
    """Remove invalid filename characters and truncate."""
    # Remove invalid characters
    text = re.sub(r'[<>:"/\\|?*]', '', text)
    # Truncate if too long
    if len(text) > max_length:
        text = text[:max_length]
    return text.strip()
```

Use this for playlist folder names and other filename generation.

### 2.3 Run Command with Timeout Helper
```python
def run_ytdlp(args: List[str], timeout: int = None) -> subprocess.CompletedProcess:
    """Run yt-dlp command with standard options."""
    cmd = _ytdlp_cmd() + args
    if OPTIONS.debug:
        cmd.insert(len(_ytdlp_cmd()), '--verbose')
    
    timeout = timeout or DEFAULT_CONFIG.get('timeout', 10)
    return _run_cmd(cmd, timeout=timeout)
```

## 3. Consolidate Color Functions

Replace individual color functions with a class:

```python
class C:  # Short name for convenience
    """Color utilities - short name for easy use."""
    
    @staticmethod
    def info(text): return color_info(text)
    
    @staticmethod
    def ok(text): return color_success(text)
    
    @staticmethod
    def warn(text): return color_warning(text)
    
    @staticmethod
    def err(text): return color_error(text)
    
    @staticmethod
    def hi(text): return color_highlight(text)
    
    @staticmethod
    def dim(text): return color_dim(text)
```

Then use: `print(C.info("Searching..."))` instead of `print(color_info("Searching..."))`

## 4. Extract Playlist Metadata Fetching

```python
def fetch_playlist_metadata(url: str) -> tuple[Optional[str], Optional[str]]:
    """
    Fetch playlist title and uploader.
    Returns (title, uploader) or (None, None) on failure.
    """
    cmd = _ytdlp_cmd() + [
        '--dump-json',
        '--playlist-items', '0',
        '--no-warnings',
        '--extractor-args', 'youtube:player_client=android,web',
        url
    ]
    
    try:
        print(color_dim("Fetching playlist metadata..."))
        result = _run_cmd(cmd, timeout=DEFAULT_CONFIG['timeout'])
        if result.stdout.strip():
            data = json.loads(result.stdout.strip().split('\n')[0])
            title = data.get('playlist_title') or data.get('title', 'Playlist')
            uploader = data.get('uploader') or data.get('channel', 'Unknown')
            
            # Sanitize for folder name
            title = sanitize_for_filename(title)
            uploader = sanitize_for_filename(uploader)
            
            print(color_success(f"Playlist: {title} by {uploader}"))
            return title, uploader
    except Exception as e:
        print(color_warning(f"Could not fetch playlist metadata: {e}"))
        if OPTIONS.debug:
            traceback.print_exc(file=sys.stderr)
    
    return None, None
```

## 5. Type Hints for Key Functions

Add type hints to improve IDE support:

```python
def download_audio(
    url: str, 
    output_path: str, 
    is_youtube_music: bool = False
) -> bool:
    """Download audio from URL."""
    # ... implementation

def apply_metadata(
    audio_file: str, 
    metadata: Dict[str, Any], 
    cover_path: Optional[str] = None
) -> bool:
    """Apply metadata to audio file."""
    # ... implementation

def process_one_task(
    task_data: tuple, 
    task_idx: int, 
    total: int, 
    output_dir: Path, 
    sources: List[MetadataSource], 
    no_interactive: bool, 
    report: Optional[ParallelReport] = None
) -> None:
    """Process a single download task."""
    # ... implementation
```

## 6. Constants for Magic Numbers

```python
# At top of file
DEFAULT_TIMEOUT = 10
DEFAULT_FETCH_TIMEOUT = 60
DEFAULT_COVER_SIZE = '600x600'
DEFAULT_MAX_FILENAME_LENGTH = 200
DEFAULT_QUALITY = 0
PLAYLIST_FOLDER_MAX_LENGTH = 100
SEARCH_RESULT_LIMIT = 3
COUNTDOWN_SECONDS = 10
```

Replace hardcoded numbers with these constants.

## Implementation Order

1. ✅ Add constants (5 minutes, zero risk)
2. ✅ Add helper functions (15 minutes, zero risk)
3. ✅ Add type hints (30 minutes, zero risk)
4. ✅ Add retry decorator (1 hour, low risk)
5. ✅ Replace retry loops with decorator (2 hours, medium risk - test thoroughly)
6. ✅ Extract playlist metadata function (30 minutes, low risk)

**Total Time**: ~4-5 hours
**Risk**: Low (if tested incrementally)
**Benefit**: ~500 lines removed, much cleaner code

## Testing Strategy

After each change:
1. Run `python -m py_compile ytmsd.py` to check syntax
2. Test with a single video: `python ytmsd.py <video_url> --dry-run`
3. Test with a playlist: `python ytmsd.py <playlist_url> --dry-run --limit 2`
4. Test with CSV file
5. Test parallel mode

## Summary

These quick wins can be implemented in an afternoon and will:
- ✅ Reduce code by ~30%
- ✅ Eliminate duplicate retry logic
- ✅ Make code more maintainable
- ✅ Improve IDE support with type hints
- ✅ Make constants configurable
- ✅ Extract reusable functions

All with minimal risk if tested incrementally.
