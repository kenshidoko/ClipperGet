# ClipGetter Studio

A complete Windows desktop `.exe` application for clipping any internet video with **AI highlight detection**, **manual precision trimming**, **low-storage section downloading**, **144p–4K quality export**, and **local Whisper AI subtitle & text generation**.

## Running
Double-click `ClipGetter.exe` in `C:\Users\samwe\Downloads\ClipGetter\ClipGetter.exe` (or run `python main.py` from source).

## Features
- **Universal Video Link Support** (`yt-dlp`): YouTube, TikTok, Instagram, X/Twitter, Twitch, Vimeo, Reddit, direct MP4 URLs.
- **AI Highlight Finder**: Combines viewer replay heatmaps, audio energy dynamics, silence/pause snapping, and platform captions (plus optional OpenAI GPT key) to suggest the best standalone clip timeframes.
- **Manual Studio Timeline**: Interactive waveform + heatmap timeline with draggable In/Out handles, whole-window sliding, `0.1s` timecode inputs, `I`/`O` hotkeys, and duration presets (`15s`, `30s`, `45s`, `60s`, `Full`).
- **Low-Storage Range Clipping**: Downloads only the selected `[start, end]` slice rather than multi-GB full videos, tracks cumulative disk savings, and auto-prunes old preview cache.
- **Quality & Format Choices**: `144p`, `240p`, `360p`, `480p`, `720p`, `1080p`, `1440p (2K)`, `2160p (4K)`, plus `MP3`, `M4A`, `Animated GIF`, and `PNG Frame Snapshots`.
- **Whisper AI Subtitles & Text**: Listens specifically to the selected clip audio and generates editable timed captions, plain text, `.SRT` / `.TXT` files, and styled burned-in subtitles (`Bold Shorts`, `Classic`, `Minimal Pill`) with live video preview.
- **Aspect Ratio Framing**: `Original`, `9:16 Crop`, `9:16 Blur Pad`, `1:1 Square`, and `4:5 Feed` with live crop-frame preview overlay.

## Rebuilding the `.exe`
Run `.\build_exe.ps1` from PowerShell.
