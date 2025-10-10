# ytmscp - YouTube Music Scraper and Downloader

ytmscp is a command-line tool for downloading audio from YouTube videos or playlists and applying metadata from sources like YouTube Music, MusicBrainz, iTunes, or YouTube's default metadata. It supports single tracks, playlists, and batch processing via CSV files, with options for metadata source selection, cover art downloading, and automatic thumbnail cropping.

## Features

- Downloads audio from YouTube in MP3 format using yt-dlp.
- Scrapes metadata from YouTube Music, MusicBrainz, or iTunes, with YouTube metadata as a fallback.
- Supports manual metadata input or specific metadata URLs.
- Automatically crops YouTube thumbnails to square 600x600 images using FFmpeg, while preserving YouTube Music thumbnails.
- Configurable metadata sources via an interactive settings menu.
- Supports batch processing with CSV files (format: `download_url,metadata_url,meta_source`).
- Command-line flag `--meta` to force a specific metadata source (`yt`, `ytm`, `it`, `mb`).
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
   git clone https://github.com/yourusername/ytmscp.git
   cd ytmscp


Ensure dependencies are installed:pip install yt-dlp


Verify FFmpeg is in your PATH (optional but recommended):ffmpeg -version



Usage
Run the script with a YouTube URL, playlist, or CSV file:
python ytmscp_cli.py [download_url_or_csv] [metadata_url] [--meta yt|ytm|it|mb] [--debug]

Examples

Download a single track with default metadata search:python ytmscp_cli.py https://youtube.com/watch?v=su7_ozM9xwQ


Use a specific metadata URL and force YouTube Music metadata:python ytmscp_cli.py https://youtube.com/watch?v=su7_ozM9xwQ https://music.youtube.com/watch?v=qXrnP3SFU2U --meta ytm


Process a playlist:python ytmscp_cli.py https://youtube.com/playlist?list=...


Process multiple tracks via CSV:python ytmscp_cli.py tracks.csv


Configure metadata sources:python ytmscp_cli.py --settings



CSV Format
The CSV file should have the following columns (all optional except download_url):

download_url: YouTube video or playlist URL (required).
metadata_url: Specific metadata URL (optional).
meta_source: Metadata source override (yt, ytm, it, mb) (optional).

Example tracks.csv:
https://youtube.com/watch?v=su7_ozM9xwQ,,yt
https://youtube.com/watch?v=another_id,https://music.youtube.com/watch?v=qXrnP3SFU2U,ytm

Options

--meta yt|ytm|it|mb: Force metadata from YouTube (yt), YouTube Music (ytm), iTunes (it), or MusicBrainz (mb).
--debug: Enable detailed error output for troubleshooting.
--settings: Open interactive menu to toggle metadata sources.

Configuration
The script stores configuration in ~/.ytmscp_config.json. Use the --settings option to enable/disable metadata sources (YouTube Music, MusicBrainz, iTunes). By default, YouTube Music and MusicBrainz are enabled, and iTunes is disabled.
Metadata Sources

YouTube (yt): Uses video uploader as artist, upload date, and thumbnail (cropped to 600x600).
YouTube Music (ytm): Fetches metadata and high-quality thumbnails from YouTube Music.
MusicBrainz (mb): Retrieves metadata and cover art from MusicBrainz and Cover Art Archive.
iTunes (it): Fetches metadata and cover art from iTunes/Apple Music.

Notes

If no metadata sources are enabled or no results are found, the script falls back to YouTube metadata after a 10-second timeout.
Enter 00 during metadata selection to force YouTube metadata.
Enter 0 to provide a metadata URL or new search query.
Thumbnails from YouTube Music (lh3.googleusercontent.com) are used as-is; YouTube thumbnails (ytimg.com) are cropped to square.

License
MIT License. See LICENSE file for details.
Contributing
Contributions are welcome! Please submit issues or pull requests via the GitHub repository.```
