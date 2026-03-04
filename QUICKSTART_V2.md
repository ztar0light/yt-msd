# Quick Start Guide - ytmsd v2.0

## 🚀 Get Started in 30 Seconds

### 1. Install Dependencies
```bash
pip install yt-dlp colorama
```

### 2. Download a Song
```bash
python ytmsd_v2.py "https://youtube.com/watch?v=..."
```

That's it! 🎉

## Common Use Cases

### Download a Playlist
```bash
python ytmsd_v2.py "https://youtube.com/playlist?list=..."
```
Creates a subfolder: `Playlist_Name-Author/`

### Download Multiple Playlists
```bash
python ytmsd_v2.py playlist1 playlist2 playlist3
```
Each gets its own subfolder! 📁

### Parallel Downloads (Faster!)
```bash
python ytmsd_v2.py URL1 URL2 URL3 --mode parallel
```

### See What Would Be Downloaded
```bash
python ytmsd_v2.py URL --dry-run
```

### Verbose Output (See Progress)
```bash
python ytmsd_v2.py URL -d
```

### Debug Output (See Everything)
```bash
python ytmsd_v2.py URL -dd
```

### Quiet Mode (Errors Only)
```bash
python ytmsd_v2.py URL -q
```

## Debug Levels Explained

### Normal (Default)
```
ℹ Searching YouTube Music: Artist Song
✓ Found 3 results
✓ Completed: Artist_Song.mp3
```
**Use when**: Normal usage

### Verbose (`-d`)
```
→ Searching YouTube Music: Artist Song
  Found 3 results from YouTube Music
→ Downloading audio
  Download attempt 1/3
✓ Completed: Artist_Song.mp3
```
**Use when**: Want to see progress details

### Debug (`-dd`)
```
[DEBUG] YouTube Music search attempt 1/3
[DEBUG] Running command: python -m yt_dlp...
[DEBUG] Selected YTM thumbnail: https://...
✓ Completed: Artist_Song.mp3
```
**Use when**: Troubleshooting issues

### Quiet (`-q`)
```
✗ Download failed: Network timeout
```
**Use when**: Automation, only want errors

## Common Options

### Custom Output Directory
```bash
python ytmsd_v2.py URL --output ./music
```

### Force Metadata Source
```bash
python ytmsd_v2.py URL --meta ytm  # YouTube Music
python ytmsd_v2.py URL --meta it   # iTunes
python ytmsd_v2.py URL --meta mb   # MusicBrainz
```

### Different Audio Format
```bash
python ytmsd_v2.py URL --format opus
python ytmsd_v2.py URL --format flac
python ytmsd_v2.py URL --format m4a
```

### Custom Quality (MP3 only)
```bash
python ytmsd_v2.py URL --quality 5  # or -Q 5
# 0 = best, 9 = worst
```

### Limit Playlist Tracks
```bash
python ytmsd_v2.py playlist --limit 10
```

### Skip Existing Files
```bash
python ytmsd_v2.py playlist --skip-existing
```

## Playlist Folder Structure

### Single Playlist
```
./Best_Songs_2024-ChannelName/
  ├── Artist1_Song1.mp3
  ├── Artist2_Song2.mp3
  └── Artist3_Song3.mp3
```

### Multiple Playlists
```
./Chill_Vibes-MusicChannel/
  ├── song1.mp3
  └── song2.mp3
./Workout_Mix-FitnessBeats/
  ├── song3.mp3
  └── song4.mp3
```

### With Custom Output
```
./music/
  ├── Playlist1-Author1/
  │   ├── song1.mp3
  │   └── song2.mp3
  └── Playlist2-Author2/
      ├── song3.mp3
      └── song4.mp3
```

## CSV Batch Processing

### 1. Create CSV File
```csv
download_url,metadata_url,meta_source
https://youtube.com/watch?v=...,https://music.youtube.com/watch?v=...,ytm
https://youtube.com/watch?v=...,,it
https://youtube.com/watch?v=...
```

### 2. Process CSV
```bash
python ytmsd_v2.py tracks.csv
```

### 3. Parallel Processing
```bash
python ytmsd_v2.py tracks.csv --mode parallel
```

## Settings Menu

### Open Settings
```bash
python ytmsd_v2.py --settings
```

### Configure
- Metadata sources (enable/disable)
- Default timeout values
- Audio format and quality
- Output template
- Cover art size
- Max filename length

Settings saved to `~/.ytmsd_config.json`

## Troubleshooting

### Network Error
```
✗ DNS/network error: Could not resolve hostname
```
**Fix**: Check internet connection, DNS, VPN/proxy

### FFmpeg Not Found
```
⚠ Metadata application failed
```
**Fix**: Install FFmpeg: https://ffmpeg.org/download.html

### Quality Flag Not Working
```
error: unrecognized arguments: -q 5
```
**Fix**: Use `-Q` instead of `-q`:
```bash
python ytmsd_v2.py URL -Q 5
```

### Playlist Subfolder Not Created
**Fix**: Don't use `--output` flag, or subfolder will be inside your output directory

## Tips & Tricks

### 1. Use Dry Run First
```bash
python ytmsd_v2.py playlist --dry-run --limit 5
```
See what would be downloaded before actually downloading

### 2. Use Verbose Mode for Long Operations
```bash
python ytmsd_v2.py large_playlist -d
```
See progress instead of waiting in silence

### 3. Use Quiet Mode for Automation
```bash
python ytmsd_v2.py URL -q 2>> errors.log
```
Only errors go to log file

### 4. Combine Options
```bash
python ytmsd_v2.py playlist \
  --output ./music \
  --format opus \
  --limit 20 \
  --skip-existing \
  --mode parallel \
  -d
```

### 5. Custom Filename Template
```bash
python ytmsd_v2.py URL --output-template "{artist} - {title} [{date}]"
# Output: Artist Name - Song Title [2024].mp3
```

## Migrating from v1?

### Only One Change!
**Quality flag**: `-q` → `-Q`

```bash
# Old (v1)
python ytmsd.py URL -q 5

# New (v2)
python ytmsd_v2.py URL -Q 5
```

Everything else works the same!

See [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md) for details.

## Need More Help?

- **Full documentation**: See [README_V2.md](README_V2.md)
- **Feature showcase**: See [V2_FEATURES.md](V2_FEATURES.md)
- **Migration guide**: See [MIGRATION_GUIDE.md](MIGRATION_GUIDE.md)
- **Technical details**: See [REFACTORING_NOTES.md](REFACTORING_NOTES.md)

## Quick Reference

### Debug Levels
- Normal: `python ytmsd_v2.py URL`
- Verbose: `python ytmsd_v2.py URL -d`
- Debug: `python ytmsd_v2.py URL -dd`
- Quiet: `python ytmsd_v2.py URL -q`

### Common Flags
- `-o DIR` - Output directory
- `-f FORMAT` - Audio format (mp3/opus/m4a/flac)
- `-Q N` - Quality (0-9, 0=best)
- `-M parallel` - Parallel mode
- `-j N` - Max concurrent jobs
- `--limit N` - Limit playlist tracks
- `--skip-existing` - Skip existing files
- `--dry-run` - Preview only
- `--no-cover` - Skip cover art
- `-s` - Settings menu
- `-v` - Show version

### Metadata Sources
- `--meta ytm` - YouTube Music
- `--meta yt` - YouTube
- `--meta it` - iTunes
- `--meta mb` - MusicBrainz

---

**That's it! You're ready to go! 🎵**

For more examples and advanced usage, see [README_V2.md](README_V2.md)
