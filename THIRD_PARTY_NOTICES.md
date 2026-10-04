# Third-party components

## Yoinks

The URL importer in `engine/url_import.py` adapts resolution selection, format
ranking, estimated download sizes, metadata reuse and progress parsing from
[Yoinks](https://github.com/pablostanley/yoinks) by Pablo Stanley.

- Upstream version: 0.3.1
- Pinned source commit: `6f03720813c22f907d3fb9ba98567f2169446b82`
- Original source: `src/lib/ytdlp.ts`, kept for reference in
  `third_party/yoinks/ytdlp.original.ts`
- License: MIT; the complete notice is in `third_party/yoinks/LICENSE`.

ClipCanvas replaces the terminal interface with its browser interface and
adapts the core to Python. It adds persistent jobs, cancellation, local-library
import, public-URL validation, fixed output paths and an isolated downloader
runtime. It does not install or run the Yoinks terminal interface.

## Download and media runtimes

URL extraction and downloading use [yt-dlp](https://github.com/yt-dlp/yt-dlp).
JavaScript challenges, where supported, use the bundled yt-dlp-ejs and Deno
dependencies. ClipCanvas does not automatically import browser cookies or
account credentials. Site availability depends on the site and the installed
extractor version; support for every URL is not guaranteed.

Video processing uses FFmpeg via imageio-ffmpeg; subtitle recognition uses
faster-whisper and its model dependencies. Each installed distribution retains
its own license files and notices under `.runtime/deps`.
