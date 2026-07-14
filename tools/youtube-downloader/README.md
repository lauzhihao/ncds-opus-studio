# YouTube Downloader

Standalone helper for `yt-dlp` batch downloads.

## Files

- `download.bat`: Windows entrypoint.
- `download.ps1`: Windows implementation used by `download.bat`.
- `download.sh`: macOS/Linux shell entrypoint.
- `links.txt`: one URL per line. Blank lines and `#` comments are skipped.
- `cookies.txt`: optional cookies. Paste either a raw browser `Cookie:` header value or a Netscape cookie export.
- `.chrome-profile/`: optional local Chrome profile created by login mode.
- `outputs/`: created automatically. Files are named as `<work-id>.<ext>`.

`cookies.txt`, `links.txt`, generated binaries, runtime files, and downloads are ignored by git.

## Usage

Windows:

```bat
download.bat
```

If neither `cookies.txt` nor `.chrome-profile/` exists, this opens a dedicated Chrome profile first.

Open a dedicated Chrome profile for manual Google/YouTube login:

```bat
download.bat login
```

macOS/Linux:

```bash
chmod +x download.sh
./download.sh
```

If neither `cookies.txt` nor `.chrome-profile/` exists, this opens a dedicated Chrome profile first.

Open a dedicated Chrome profile for manual Google/YouTube login:

```bash
./download.sh login
```

Open Chrome, wait for manual login, then download:

```bash
./download.sh login-download
```

## Notes

- `yt-dlp` is auto-downloaded into `bin/` when it is not already available.
- `ffmpeg` must be installed and available in `PATH` for video/audio merge.
- Node.js is recommended for YouTube JS challenge solving. The scripts enable `--remote-components ejs:github`.
- If `cookies.txt` has content, it is used first.
- If `cookies.txt` is empty and `.chrome-profile/` exists, downloads use `yt-dlp --cookies-from-browser chrome:<profile>`.
- If both are missing, the default download command opens Chrome once and waits for you to finish login.
- The scripts do not export browser cookies into `cookies.txt`.
- Do not paste account cookies into a terminal. Edit `cookies.txt` directly only when you intentionally want file-based cookies.
