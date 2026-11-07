# ZeroLink

A Windows app that downloads video and audio from YouTube and 1,800+ other sites, and converts or compresses any media file on your PC. [yt-dlp](https://github.com/yt-dlp/yt-dlp) does the downloading and ffmpeg does the converting. ZeroLink was called ZERO before version 3.0; it picks up ZERO's settings, history and tools on first start.

## Files tab

For anything that isn't a video page: installers, archives, ISOs, PDFs, fonts, any direct http/https link.

- Paste one link or a whole list (one per line, or any text with links in it) and press Enter.
- Up to 16 connections per file when the server allows byte ranges, so big files come down faster. Use 1 or 2 if a site limits connections.
- Pause, resume and retry. Progress is kept in a small `.zlpart.json` file next to the partial `.zlpart` download, so downloads continue after closing ZeroLink, a crash or a reboot.
- File names come from the server (Content-Disposition, Cyrillic included) or the link; an existing file is never overwritten, a new one gets "(1)".
- Optional sorting into Programs, Compressed, Disk images, Documents, Video, Music, Images, Fonts or Other.
- The speed limit from Settings applies here too; Settings also sets how many files download at once.
- A link ending in .zip, .exe, .iso, .pdf and similar, pasted on the Video & audio page or copied while clipboard watching is on, goes to Files automatically. When yt-dlp can't read a link, "Download as a file instead" sends it here.

## Convert tab

Drop files or a whole folder onto the list (or use Add files / Add folder), pick an output, press Convert. Files from the Video & audio queue and History can be sent here with right-click → Convert / compress.

- Reads practically anything ffmpeg reads: MKV, MP4, AVI, MOV, WMV, FLV, TS/M2TS, WebM, VOB, 3GP, MXF, FLAC, WAV, APE, WMA, AIFF, OGG, AC3/DTS and more.
- Video out: MP4, MKV, WebM, MOV, AVI, TS with H.264, H.265, AV1 or VP9. "Auto" keeps the original video when the new container can hold it, so MKV to MP4 is a remux that takes seconds and loses nothing.
- Audio out: MP3, M4A (AAC), Opus, OGG Vorbis, FLAC, ALAC, WAV, AC3. Bitrate from 64 to 320 kbps.
- Animated GIF (with an optimized palette) and animated WebP, with width and frame rate.
- Subtitles out: every text subtitle track to SRT, ASS or VTT.
- Size: Smaller / Balanced / Best quality, or fit a size: Discord 10 MB, WhatsApp 16 MB, email 25 MB, Viber 200 MB or any custom MB. Fitting a size encodes twice (2-pass) and lowers the resolution when the size can't carry it. Files already under the size are only remuxed.
- Resolution down to 360p, trim (from/to), even loudness (EBU R128, -16 LUFS), keep subtitles, keep all audio tracks.
- Every file shows its size before and the expected size after; the panel shows what will happen to the selected file.
- The GPU encoder from Settings → Converter is used for quality-based H.264/H.265/AV1 encodes, with an automatic retry on the CPU.
- Results are saved next to the original (or a folder you pick) and never overwrite it.

## Download features

- Paste a link and press **Analyze** to see the title, thumbnail, length and every available quality.
- **Format**: MP4 video, MKV video, MP3 audio, M4A audio, or the original audio with no conversion.
- **Quality**: for video, Best, 4K, 1440p, 1080p, 720p, 480p or 360p, or an exact stream from the list. For MP3, Best VBR (~245 kbps), 320, 256, 192 or 128 kbps.
- **Codec** for video: Auto, AV1, VP9, H.264 or H.265 (HEVC). The resolution you chose is kept.
  - AV1 and VP9: if a video doesn't have that codec, the next best one is used.
  - H.264 and H.265: if the site doesn't offer them (YouTube never offers H.265 and has no H.264 above 1080p), ZeroLink downloads the best stream and re-encodes it with ffmpeg. The queue shows "Converting to H.265" with progress. Converting on the CPU is slow, so in **Settings → Converter** you can pick an NVIDIA, Intel or AMD GPU instead. If the GPU encoder isn't available, ZeroLink falls back to the CPU. You can also turn conversion off, and then ZeroLink just picks the closest codec the site has.
  - To play AV1, Windows needs the free "AV1 Video Extension". For H.265 it needs "HEVC Video Extensions". VLC plays both.
- **Size estimates**: every format, quality and codec shows its expected file size. Codecs also show how much bigger or smaller they are than Auto (e.g. "H.264 · ~134 MB (+87% vs Auto)"). A summary under the options gives the total and the codec you will actually get. Sizes come from the site's own numbers. Sizes after conversion are rough estimates.
- Playlists and channels: tick the items you want. Files are numbered inside a folder named after the playlist. A bare channel link opens the channel's Videos tab.
- Subtitles in the languages you choose, including auto-generated ones, embedded in the file and also saved as .srt.
- A queue with 1–8 downloads at once, plus pause, resume, cancel, retry and per-item error details. Unfinished downloads resume after you restart the app.
- History with search, open file, show in folder and download again.
- Skips videos you already downloaded (an archive of video IDs).
- Embeds the cover thumbnail, metadata and chapters.
- Optional: browser cookies for members-only or age-restricted videos, a speed limit, clipboard watching, and a notification when a download finishes.
- **Update yt-dlp** button. When YouTube changes something and downloads start failing, one click usually fixes it.

## Install (no Python needed)

1. Double-click **ZeroLink-Setup-3.1.0.exe**.
2. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**. Windows shows this for any app that isn't signed with a paid code-signing certificate.
3. Click Next a few times. ZeroLink installs for your Windows user only, so it never asks for an administrator password. You get a Start menu entry and, if you leave the box ticked, a desktop icon.
4. To remove it, go to **Settings → Apps → Installed apps → ZeroLink → Uninstall**.

**Portable version:** double-click **ZeroLink-Portable-3.1.0.exe** and choose a folder; a USB stick works too. It unpacks ZeroLink there without installing anything. Start it with `ZeroLink.exe` in that folder. Settings, history and tools stay in a `data` folder next to it (that's what `portable.txt` does; delete that file to use %LOCALAPPDATA%\ZeroLink instead). To remove it, delete the folder.

Works on Windows 10 (version 1903 or newer) and Windows 11, 64-bit.

## For developers: building it yourself

- On Windows with Python: run `build.bat` to get `dist\ZeroLink\ZeroLink.exe`. Then run `makensis installer.nsi` to make the installer ([NSIS](https://nsis.sourceforge.io) must be installed).
- On Linux, without a Windows PC: run `tools/build_windows_on_linux.sh <cpython-windows-tarball>`. It uses Wine, a standalone Windows Python and NSIS, and produces both the installer and the portable version.

## First launch

ZeroLink offers to download three official tools into `%LOCALAPPDATA%\ZeroLink\bin`:

| Tool | Why | Source |
|---|---|---|
| yt-dlp.exe | The download engine | github.com/yt-dlp/yt-dlp |
| ffmpeg.exe, ffprobe.exe | Merging, MP3 conversion, embedding | github.com/yt-dlp/FFmpeg-Builds |
| deno.exe | Solves YouTube's JavaScript challenges (yt-dlp needs a JS runtime for YouTube since late 2025) | github.com/denoland/deno |

You can re-run this any time from **Settings → Install / repair components**.

## When something stops working

1. Go to **Settings → Update yt-dlp**.
2. If a video needs a login, set **Browser cookies** to Firefox. Chrome and Edge encrypt their cookies, so with those browsers the browser must be fully closed while you download.
3. Double-click a failed item in the queue to see yt-dlp's own error message.

## How it is built

```
main.py                 entry point
zero/
  binaries.py           finds/installs yt-dlp, ffmpeg, deno; kills process trees
  presets.py            quality presets -> yt-dlp arguments; command builder
  probe.py              yt-dlp -J --flat-playlist -> title, formats, playlist entries
  jobs.py               Job model, JobRunner (one yt-dlp process per job), QueueManager
  estimate.py           predicts which streams yt-dlp will pick + file sizes
  convert.py            ffmpeg re-encode to H.264/H.265 (CPU, NVENC, Quick Sync, AMF)
  transcode.py          Convert tab: probing, output presets, ffmpeg command plans, size estimates
  convert_queue.py      runs conversion plans with progress, cancel and GPU-to-CPU retry
  filedl.py             Files tab engine: multi-connection, resumable direct downloads
  history.py            SQLite history
  settings.py           JSON settings
  ui/                   PySide6 interface (main window, download panel, queue, convert, history, settings)
    theme.py            design tokens, fonts, Lucide icons, stylesheet
assets/                 icon, wordmark, fonts (IBM Plex Sans, JetBrains Mono), icons (Lucide)
installer.nsi           one-click per-user Windows installer
portable.nsi            self-unpacking portable version
tools/                  Linux -> Windows build script
```

Each download is a separate `yt-dlp.exe` process. ZeroLink reads its progress as JSON lines (`--progress-template`) and gets the final file path from `--print after_move:filepath`. yt-dlp is not compiled into the app, so `yt-dlp -U` can update the engine without rebuilding ZeroLink. Partial files go to a hidden `.zero-tmp\<job>` folder inside your download folder, so two downloads of the same video (for example MP4 and MP3) never overwrite each other.

App data (settings, history, queue, archive, tools) lives in `%LOCALAPPDATA%\ZeroLink`.

## Interface

Dark theme on near-black with the #49B265 green as the only accent. Text meets WCAG AA contrast (4.5:1 or better), every control is at least 36 px tall, keyboard focus shows a green ring, and status is always written out, not only coloured. Shortcuts: Ctrl+1 to Ctrl+5 switch pages, Ctrl+Shift+V pastes a link and analyzes it, Ctrl+Enter starts the download or conversion, Ctrl+O adds files on the Convert page.

Bundled under their own licenses: IBM Plex Sans and JetBrains Mono (SIL Open Font License 1.1), Lucide icons (ISC). The license texts are in `assets/fonts` and `assets/icons`.

## Legal

Only download content you have the right to download. Many sites' terms of service forbid downloading.
