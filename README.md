# yt-msd - YouTube Music Scraper and Downloader

ytmsd is a command-line tool for downloading audio from YouTube or YouTube Music videos or playlists and applying metadata from sources like YouTube Music, MusicBrainz, iTunes, or YouTube's default metadata. It supports single tracks, playlists, and batch processing via CSV files, with options for metadata source selection, cover art downloading, and automatic thumbnail cropping.

## Features

- Downloads audio from YouTube or YouTube Music in MP3 format using yt-dlp.
- Scrapes metadata from YouTube Music, MusicBrainz, or iTunes, with YouTube metadata as a fallback.
- Automatically detects YouTube or YouTube Music URLs for single-link inputs.
- For YouTube Music URLs, uses the URL's metadata unless overridden by `--meta` or `--meta_link`.
- Falls back to YouTube metadata if YouTube Music metadata is unavailable.
- Supports manual metadata input or specific metadata URLs via `--meta_link` or CSV.
- Automatically crops YouTube thumbnails to square 600x600 images using FFmpeg, while preserving YouTube Music thumbnails.
- Configurable metadata sources via an interactive settings menu.
- Supports batch processing with CSV files (format: `download_url,metadata_url,meta_source`).
- Command-line flag `--meta` to force a specific metadata source (`yt`, `ytm`, `it`, `mb`).
- Command-line flag `--meta_link` to specify a metadata URL.
- 10-second countdown for user selection of metadata results, with automatic fallback to YouTube metadata if no input is provided.
- Option to override search results with YouTube metadata by entering `00` during selection.
- Windows-compatible file naming and path handling.

## Installation

### Prerequisites
- Python 3.6 or higher
- yt-dlp: `pip install yt-dlp`
- FFmpeg (optional, for metadata tagging and cover art cropping): Install via package manager or download from [FFmpeg website](https://ffmpeg.org/download.html)

### Setup
1. Clone the repository:
   ```bash
   git clone https://github.com/ztar0light/ytmsd.git
   cd ytmsd
   ```
2. Ensure dependencies are installed:
   ```bash
   pip install yt-dlp
   ```
3. Verify FFmpeg is in your PATH (optional but recommended):
   ```bash
   ffmpeg -version
   ```

## Building the Executable

To create a standalone Windows executable, use PyInstaller to package `ytmsd.py`.

### Prerequisites
- Install PyInstaller:
  ```bash
  pip install pyinstaller
  ```
- Ensure `yt-dlp` is installed (`pip install yt-dlp`).
- FFmpeg must be in the system PATH or bundled with the executable.

### Build Command
Run the following command in the directory containing `ytmsd.py`:
```bash
pyinstaller --onefile --add-data "path_to_ffmpeg;ffmpeg" --hidden-import yt_dlp ytmsd.py
```
- Replace `path_to_ffmpeg` with the path to `ffmpeg.exe` (e.g., `C:\path\to\ffmpeg.exe`).
- The executable will be created in the `dist` folder as `dist\ytmsd.exe`.

### Notes
- If FFmpeg is in the system PATH, omit `--add-data "path_to_ffmpeg;ffmpeg"`.
- The `--onefile` option creates a single portable `.exe`, but it may be large (50-100 MB).
- For a smaller size, omit `--onefile` to create a directory-based executable.
- Use `--log-level DEBUG` for troubleshooting build issues.
- Test the executable:
  ```bash
  .\dist\ytmscp_cli.exe https://youtube.com/watch?v=su7_ozM9xwQ --debug
  ```

## Usage

Run the script with a YouTube or YouTube Music URL, playlist, or CSV file:

```bash
python ytmsd.py [download_url_or_csv] [--meta yt|ytm|it|mb] [--meta_link link] [--debug]
```

### Examples
- Download a single YouTube track with default metadata search:
  ```bash
  python ytmsd.py https://youtube.com/watch?v=su7_ozM9xwQ
  ```
- Download a YouTube Music track using its metadata:
  ```bash
  python ytmsd.py https://music.youtube.com/watch?v=su7_ozM9xwQ
  ```
- Force YouTube Music metadata for a YouTube URL:
  ```bash
  python ytmsd.py https://youtube.com/watch?v=su7_ozM9xwQ --meta ytm
  ```
- Use a specific metadata URL:
  ```bash
  python ytmsd.py https://youtube.com/watch?v=su7_ozM9xwQ --meta_link https://music.youtube.com/watch?v=qXrnP3SFU2U
  ```
- Process a playlist:
  ```bash
  python ytmsd.py https://youtube.com/playlist?list=...
  ```
- Process multiple tracks via CSV:
  ```bash
  python ytmsd.py tracks.csv
  ```
- Configure metadata sources:
  ```bash
  python ytmsd.py --settings
  ```

### CSV Format
The CSV file should have the following columns (all optional except `download_url`):
- `download_url`: YouTube or YouTube Music video or playlist URL (required).
- `metadata_url`: Specific metadata URL (optional).
- `meta_source`: Metadata source override (`yt`, `ytm`, `it`, `mb`) (optional).

Example `tracks.csv`:
```csv
https://youtube.com/watch?v=su7_ozM9xwQ,,yt
https://music.youtube.com/watch?v=another_id,https://music.youtube.com/watch?v=qXrnP3SFU2U,ytm
```

### Options
- `--meta yt|ytm|it|mb`: Force metadata from YouTube (`yt`), YouTube Music (`ytm`), iTunes (`it`), or MusicBrainz (`mb`).
- `--meta_link [link]`: Specify a metadata URL to fetch metadata directly.
- `--debug`: Enable detailed error output for troubleshooting.
- `--settings`: Open interactive menu to toggle metadata sources.

## Configuration
The script stores configuration in `~/.ytmsd_config.json`. Use the `--settings` option to enable/disable metadata sources (YouTube Music, MusicBrainz, iTunes). By default, YouTube Music and MusicBrainz are enabled, and iTunes is disabled.

## Metadata Sources
- **YouTube (`yt`)**: Uses video uploader as artist, upload date, and thumbnail (cropped to 600x600).
- **YouTube Music (`ytm`)**: Fetches metadata and high-quality thumbnails from YouTube Music.
- **MusicBrainz (`mb`)**: Retrieves metadata and cover art from MusicBrainz and Cover Art Archive.
- **iTunes (`it`)**: Fetches metadata and cover art from iTunes/Apple Music.

## Notes
- If a YouTube Music URL is provided, the script uses its metadata unless `--meta` or `--meta_link` is specified.
- If YouTube Music metadata is unavailable, it falls back to YouTube metadata for the same video ID.
- If no metadata sources are enabled or no results are found, the script falls back to YouTube metadata after a 10-second timeout.
- Enter `00` during metadata selection to force YouTube metadata.
- Enter `0` to provide a metadata URL or new search query.
- Thumbnails from YouTube Music (lh3.googleusercontent.com) are used as-is; YouTube thumbnails (ytimg.com) are cropped to square.

## License
MIT License. See LICENSE file for details.

## Contributing
Contributions are welcome! Please submit issues or pull requests via the GitHub repository.
