"""ClipGetter Studio — Desktop application entry point (FastAPI + PyWebView)."""

from __future__ import annotations

import ctypes
import logging
import os
import re
import socket
import subprocess
import threading
import time
import uuid
import webbrowser
from pathlib import Path
from typing import Any

import uvicorn
import webview
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend.ai_suggester import suggest_clips
from backend.exporter import export_clip
from backend.paths import ensure_ffmpeg_bin_dir, get_assets_dir, get_cache_dir, get_ffmpeg_exe, get_frontend_dir
from backend.storage import (
    IO_LOCK,
    clear_cache,
    clear_drafts,
    delete_draft,
    delete_history_item,
    get_storage_overview,
    load_drafts,
    load_history,
    load_settings,
    prune_old_cache,
    save_draft,
    save_settings,
)
from backend.video_service import (
    analyze_url,
    compute_waveform_bars,
    ensure_analysis_audio,
    ensure_preview_video,
    extract_clip_audio,
)
from backend.whisper_service import segments_to_srt, segments_to_vtt, transcribe_clip_audio

logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

app = FastAPI(title="ClipGetter Studio", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory session state & background job tracking
VIDEO_CACHE: dict[str, dict[str, Any]] = {}
JOBS: dict[str, dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()
MAIN_WINDOW: webview.Window | None = None


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _clean_err(exc: Exception | str) -> str:
    return _ANSI_RE.sub("", str(exc)).strip()


def _update_job(job_id: str, **fields: Any) -> None:
    if "error" in fields and fields["error"]:
        fields["error"] = _clean_err(fields["error"])
    if "message" in fields and fields["message"]:
        fields["message"] = _clean_err(fields["message"])
    with JOBS_LOCK:
        job = JOBS.setdefault(job_id, {})
        job.update(fields)


def _create_job(kind: str) -> str:
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "running",
            "progress": 0.0,
            "message": "Starting...",
            "result": None,
            "error": None,
        }
    return job_id


class AnalyzeReq(BaseModel):
    url: str


class PrepareReq(BaseModel):
    video_id: str
    target_duration: float = 30.0


class SuggestReq(BaseModel):
    video_id: str
    target_duration: float = 30.0


class TranscribeReq(BaseModel):
    video_id: str
    start: float
    end: float
    model_size: str = "base"
    caption_mode: str = "shorts"
    language: str = "auto"


class ExportReq(BaseModel):
    video_id: str
    start: float
    end: float
    quality: str = "1080p"
    export_format: str = "mp4"
    storage_mode: str = "balanced"
    aspect_ratio: str = "original"
    burn_subtitles: bool = False
    subtitle_style: str = "bold_shorts"
    subtitle_segments: list[dict[str, Any]] = []
    text_layers: list[dict[str, Any]] = []
    video_transform: dict[str, Any] = {}
    video_adjustments: dict[str, Any] = {}
    save_srt: bool = False
    save_txt: bool = False
    normalize_audio: bool = False
    audio_fade: bool = True
    custom_filename: str = ""
    est_full_bytes: int = 0


class BatchClipItem(BaseModel):
    title: str
    start: float
    end: float


class BatchExportReq(BaseModel):
    video_id: str
    clips: list[BatchClipItem]
    quality: str = "1080p"
    storage_mode: str = "balanced"
    aspect_ratio: str = "original"
    auto_subtitles: bool = False
    burn_subtitles: bool = False
    subtitle_style: str = "bold_shorts"
    model_size: str = "base"
    est_full_bytes: int = 0


class SnapshotReq(BaseModel):
    video_id: str
    timestamp: float


class SubtitleSerializeReq(BaseModel):
    segments: list[dict[str, Any]]
    fmt: str = "srt"
    filename: str = "subtitles"


class SettingsReq(BaseModel):
    updates: dict[str, Any]


class PathReq(BaseModel):
    path: str


class DraftReq(BaseModel):
    draft: dict[str, Any]


@app.get("/", response_class=HTMLResponse)
def serve_index() -> HTMLResponse:
    index_file = get_frontend_dir() / "index.html"
    return HTMLResponse(index_file.read_text(encoding="utf-8"))


@app.post("/api/analyze")
def api_analyze(req: AnalyzeReq) -> dict[str, Any]:
    url = req.url.strip()
    if not url:
        raise HTTPException(status_code=400, detail="Please paste a valid video URL.")
    try:
        info = analyze_url(url)
        VIDEO_CACHE[info["video_id"]] = info
        return info
    except Exception as exc:
        raise HTTPException(status_code=400, detail=_clean_err(exc))


@app.post("/api/prepare_media")
def api_prepare_media(req: PrepareReq) -> dict[str, str]:
    info = VIDEO_CACHE.get(req.video_id)
    if not info:
        raise HTTPException(status_code=404, detail="Video session not found. Analyze URL first.")

    job_id = _create_job("prepare")

    def _worker() -> None:
        try:
            _update_job(job_id, progress=15.0, message="Downloading lightweight 360p preview...")
            ensure_preview_video(info["url"], req.video_id)

            _update_job(job_id, progress=55.0, message="Extracting compact audio & waveform...")
            wav_path = ensure_analysis_audio(info["url"], req.video_id)
            waveform = compute_waveform_bars(wav_path, num_bars=240)

            _update_job(job_id, progress=80.0, message="Scanning audio peaks & replay moments...")
            settings = load_settings()
            ai_res = suggest_clips(
                info,
                wav_path=wav_path,
                target_duration=req.target_duration,
                openai_api_key=settings.get("openai_api_key", ""),
            )

            _update_job(
                job_id,
                status="done",
                progress=100.0,
                message="Preview & highlights ready",
                result={
                    "preview_url": f"/api/preview/{req.video_id}?t={int(time.time())}",
                    "waveform": waveform,
                    "suggestions": ai_res["suggestions"],
                    "heatmap_bars": ai_res["heatmap_bars"],
                },
            )
        except Exception as exc:
            _update_job(job_id, status="error", error=str(exc), message=str(exc))

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job_id}


@app.api_route("/api/preview/{video_id}", methods=["GET", "HEAD"])
def api_serve_preview(video_id: str) -> FileResponse:
    preview_path = get_cache_dir() / video_id / "preview_360p.mp4"
    if not preview_path.is_file():
        raise HTTPException(status_code=404, detail="Preview video not ready yet.")
    return FileResponse(str(preview_path), media_type="video/mp4")


@app.post("/api/ai_suggest")
def api_ai_suggest(req: SuggestReq) -> dict[str, Any]:
    info = VIDEO_CACHE.get(req.video_id)
    if not info:
        raise HTTPException(status_code=404, detail="Video session not found.")
    wav_path = get_cache_dir() / req.video_id / "analysis_16k.wav"
    if not wav_path.is_file():
        wav_path = ensure_analysis_audio(info["url"], req.video_id)
    settings = load_settings()
    return suggest_clips(
        info,
        wav_path=wav_path,
        target_duration=req.target_duration,
        openai_api_key=settings.get("openai_api_key", ""),
    )


@app.post("/api/transcribe")
def api_transcribe(req: TranscribeReq) -> dict[str, str]:
    info = VIDEO_CACHE.get(req.video_id)
    if not info:
        raise HTTPException(status_code=404, detail="Video session not found.")
    if req.end <= req.start:
        raise HTTPException(status_code=400, detail="End time must be after start time.")

    job_id = _create_job("transcribe")

    def _worker() -> None:
        clip_wav = get_cache_dir() / req.video_id / f"clip_{uuid.uuid4().hex[:6]}.wav"
        try:
            _update_job(job_id, progress=20.0, message="Isolating clip audio track...")
            extract_clip_audio(info["url"], req.video_id, req.start, req.end, clip_wav)

            _update_job(
                job_id,
                progress=50.0,
                message="Transcribing speech from clip...",
            )
            result = transcribe_clip_audio(
                clip_wav,
                model_size=req.model_size,
                caption_mode=req.caption_mode,
                language=req.language,
            )
            _update_job(
                job_id,
                status="done",
                progress=100.0,
                message="Subtitles & transcript ready",
                result=result,
            )
        except Exception as exc:
            _update_job(job_id, status="error", error=str(exc), message=str(exc))
        finally:
            clip_wav.unlink(missing_ok=True)

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/export")
def api_export(req: ExportReq) -> dict[str, str]:
    info = VIDEO_CACHE.get(req.video_id)
    if not info:
        raise HTTPException(status_code=404, detail="Video session not found.")
    if req.end <= req.start:
        raise HTTPException(status_code=400, detail="End time must be after start time.")

    job_id = _create_job("export")

    def _worker() -> None:
        try:
            res = export_clip(
                url=info["url"],
                title=info["title"],
                start=req.start,
                end=req.end,
                quality=req.quality,
                export_format=req.export_format,
                storage_mode=req.storage_mode,
                aspect_ratio=req.aspect_ratio,
                burn_subtitles=req.burn_subtitles,
                subtitle_style=req.subtitle_style,
                subtitle_segments=req.subtitle_segments,
                text_layers=req.text_layers,
                video_transform=req.video_transform,
                video_adjustments=req.video_adjustments,
                save_srt=req.save_srt,
                save_txt=req.save_txt,
                normalize_audio=req.normalize_audio,
                audio_fade=req.audio_fade,
                est_full_bytes=req.est_full_bytes,
                src_width=int(info.get("width") or 1920),
                src_height=int(info.get("height") or 1080),
                custom_filename=req.custom_filename or None,
                progress_cb=lambda p, m: _update_job(job_id, progress=round(p, 1), message=m),
            )
            _update_job(
                job_id,
                status="done",
                progress=100.0,
                message="Clip exported successfully!",
                result=res,
            )
        except Exception as exc:
            _update_job(job_id, status="error", error=str(exc), message=str(exc))

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/batch_export")
def api_batch_export(req: BatchExportReq) -> dict[str, str]:
    info = VIDEO_CACHE.get(req.video_id)
    if not info:
        raise HTTPException(status_code=404, detail="Video session not found.")
    if not req.clips:
        raise HTTPException(status_code=400, detail="No clips selected for batch export.")

    job_id = _create_job("batch_export")

    def _worker() -> None:
        exported: list[dict[str, Any]] = []
        total = len(req.clips)
        try:
            for idx, clip in enumerate(req.clips):
                base_pct = (idx / total) * 100.0
                span_pct = 100.0 / total
                segs: list[dict[str, Any]] = []
                if req.auto_subtitles:
                    _update_job(
                        job_id,
                        progress=round(base_pct + span_pct * 0.15, 1),
                        message=f"[{idx+1}/{total}] Transcribing '{clip.title}'...",
                    )
                    clip_wav = get_cache_dir() / req.video_id / f"batch_{idx}.wav"
                    try:
                        extract_clip_audio(info["url"], req.video_id, clip.start, clip.end, clip_wav)
                        tr = transcribe_clip_audio(clip_wav, model_size=req.model_size, caption_mode="shorts")
                        segs = tr["segments"]
                    finally:
                        clip_wav.unlink(missing_ok=True)

                res = export_clip(
                    url=info["url"],
                    title=clip.title,
                    start=clip.start,
                    end=clip.end,
                    quality=req.quality,
                    export_format="mp4",
                    storage_mode=req.storage_mode,
                    aspect_ratio=req.aspect_ratio,
                    burn_subtitles=req.burn_subtitles and bool(segs),
                    subtitle_style=req.subtitle_style,
                    subtitle_segments=segs,
                    save_srt=req.auto_subtitles,
                    save_txt=req.auto_subtitles,
                    est_full_bytes=req.est_full_bytes,
                    src_width=int(info.get("width") or 1920),
                    src_height=int(info.get("height") or 1080),
                    progress_cb=lambda p, m, b=base_pct, s=span_pct, i=idx: _update_job(
                        job_id,
                        progress=round(b + s * (0.2 + 0.8 * (p / 100.0)), 1),
                        message=f"[{i+1}/{total}] {m}",
                    ),
                )
                exported.append(res["record"])

            _update_job(
                job_id,
                status="done",
                progress=100.0,
                message=f"Batch exported {len(exported)} clips!",
                result={"records": exported, "storage": get_storage_overview()},
            )
        except Exception as exc:
            _update_job(job_id, status="error", error=str(exc), message=str(exc))

    threading.Thread(target=_worker, daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/snapshot")
def api_snapshot(req: SnapshotReq) -> dict[str, Any]:
    info = VIDEO_CACHE.get(req.video_id)
    preview_path = get_cache_dir() / req.video_id / "preview_360p.mp4"
    if not info or not preview_path.is_file():
        raise HTTPException(status_code=404, detail="Preview video not ready for frame capture.")

    settings = load_settings()
    out_dir = Path(settings["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    safe_title = "".join(c for c in info["title"] if c.isalnum() or c in " _-")[:40].strip().replace(" ", "_")
    out_png = out_dir / f"{safe_title or 'frame'}_{int(req.timestamp)}s.png"
    counter = 1
    while out_png.exists():
        out_png = out_dir / f"{safe_title or 'frame'}_{int(req.timestamp)}s_{counter}.png"
        counter += 1

    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    proc = subprocess.run(
        [
            get_ffmpeg_exe(),
            "-y",
            "-ss",
            f"{max(0.0, req.timestamp):.3f}",
            "-i",
            str(preview_path),
            "-vframes",
            "1",
            str(out_png),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    if proc.returncode != 0 or not out_png.is_file():
        raise HTTPException(status_code=500, detail="Could not capture frame.")
    return {"path": str(out_png), "filename": out_png.name}


@app.post("/api/subtitles/save")
def api_save_subtitles(req: SubtitleSerializeReq) -> dict[str, str]:
    settings = load_settings()
    out_dir = Path(settings["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    safe = "".join(c for c in req.filename if c.isalnum() or c in " _-")[:48].strip().replace(" ", "_") or "subtitles"
    fmt = req.fmt.lower()
    if fmt == "vtt":
        content = segments_to_vtt(req.segments)
        ext = "vtt"
    elif fmt == "txt":
        content = " ".join(str(s.get("text", "")).strip() for s in req.segments if s.get("text")) + "\n"
        ext = "txt"
    else:
        content = segments_to_srt(req.segments)
        ext = "srt"

    target = out_dir / f"{safe}.{ext}"
    counter = 1
    while target.exists():
        target = out_dir / f"{safe}_{counter}.{ext}"
        counter += 1
    target.write_text(content, encoding="utf-8")
    return {"path": str(target), "filename": target.name}


@app.post("/api/subtitles/format", response_class=PlainTextResponse)
def api_format_subtitles(req: SubtitleSerializeReq) -> str:
    fmt = req.fmt.lower()
    if fmt == "vtt":
        return segments_to_vtt(req.segments)
    if fmt == "txt":
        return " ".join(str(s.get("text", "")).strip() for s in req.segments if s.get("text"))
    return segments_to_srt(req.segments)


@app.get("/api/jobs/{job_id}")
def api_job_status(job_id: str) -> dict[str, Any]:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return dict(job)


@app.get("/api/settings")
def api_get_settings() -> dict[str, Any]:
    return {
        "settings": load_settings(),
        "storage": get_storage_overview(),
        "history": load_history(),
        "drafts": load_drafts(),
    }


@app.get("/api/drafts")
def api_get_drafts() -> dict[str, Any]:
    return {"drafts": load_drafts()}


@app.post("/api/drafts")
def api_save_draft(req: DraftReq) -> dict[str, Any]:
    draft = req.draft
    if isinstance(draft.get("video"), dict) and draft["video"].get("video_id"):
        VIDEO_CACHE[draft["video"]["video_id"]] = draft["video"]
    return {"drafts": save_draft(draft)}


@app.post("/api/drafts/resume")
def api_resume_draft(req: DraftReq) -> dict[str, Any]:
    draft = req.draft
    vid = str(draft.get("id") or "")
    video_info = draft.get("video") or {}
    if vid and isinstance(video_info, dict):
        VIDEO_CACHE[vid] = video_info
    preview_path = get_cache_dir() / vid / "preview_360p.mp4" if vid else None
    has_preview = bool(preview_path and preview_path.is_file() and preview_path.stat().st_size > 10_000)
    return {
        "ok": True,
        "has_preview": has_preview,
        "preview_url": f"/api/preview/{vid}?t={int(time.time())}" if has_preview else None,
    }


@app.delete("/api/drafts/{draft_id}")
def api_delete_draft(draft_id: str) -> dict[str, Any]:
    return {"drafts": delete_draft(draft_id)}


@app.delete("/api/drafts")
def api_clear_drafts() -> dict[str, Any]:
    return {"drafts": clear_drafts()}


@app.post("/api/settings")
def api_save_settings(req: SettingsReq) -> dict[str, Any]:
    updated = save_settings(req.updates)
    return {"settings": updated, "storage": get_storage_overview()}


@app.post("/api/storage/clear_cache")
def api_clear_cache(keep_video_id: str | None = None) -> dict[str, Any]:
    freed = clear_cache(keep_video_id=keep_video_id)
    return {"freed_bytes": freed, "storage": get_storage_overview()}


@app.delete("/api/history/{item_id}")
def api_delete_history(item_id: str) -> dict[str, Any]:
    return {"history": delete_history_item(item_id)}


@app.post("/api/pick_folder")
def api_pick_folder() -> dict[str, Any]:
    if MAIN_WINDOW is None:
        return {"selected": None}
    res = MAIN_WINDOW.create_file_dialog(webview.FOLDER_DIALOG)
    if res and len(res) > 0:
        folder = str(res[0])
        updated = save_settings({"output_dir": folder})
        return {"selected": folder, "settings": updated, "storage": get_storage_overview()}
    return {"selected": None}


@app.post("/api/open_file")
def api_open_file(req: PathReq) -> dict[str, bool]:
    p = Path(req.path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found on disk.")
    os.startfile(str(p))
    return {"ok": True}


@app.post("/api/reveal_file")
def api_reveal_file(req: PathReq) -> dict[str, bool]:
    p = Path(req.path)
    if not p.exists():
        folder = p.parent if p.parent.exists() else Path(load_settings()["output_dir"])
        os.startfile(str(folder))
        return {"ok": True}
    if p.is_dir():
        os.startfile(str(p))
    else:
        subprocess.Popen(["explorer", "/select,", str(p)])
    return {"ok": True}


# Mount static frontend assets after API routes
frontend_dir = get_frontend_dir()
if frontend_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> None:
    ensure_ffmpeg_bin_dir()
    settings = load_settings()
    if settings.get("auto_clean_cache", True):
        threading.Thread(target=prune_old_cache, kwargs={"max_age_hours": 24}, daemon=True).start()

    port = _find_free_port()
    uv_config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
    )
    server = uvicorn.Server(uv_config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait briefly until HTTP server accepts connections
    for _ in range(40):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    global MAIN_WINDOW
    icon_path = get_assets_dir() / "icon.ico"
    MAIN_WINDOW = webview.create_window(
        title="ClipGetter Studio",
        url=f"http://127.0.0.1:{port}/",
        width=1420,
        height=900,
        min_size=(1100, 720),
        background_color="#0c0e12",
        text_select=True,
    )

    def _on_window_closed() -> None:
        server.should_exit = True

    MAIN_WINDOW.events.closed += _on_window_closed
    try:
        webview.start(
            icon=str(icon_path) if icon_path.is_file() else None,
            debug=False,
        )
    except Exception:
        # Fallback for older Windows PCs missing Edge WebView2 Runtime: open in default browser
        local_url = f"http://127.0.0.1:{port}/"
        webbrowser.open(local_url)
        ctypes.windll.user32.MessageBoxW(
            0,
            f"ClipGetter Studio opened in your web browser at:\n{local_url}\n\n"
            "Keep this message open while editing, then click OK to close ClipGetter Studio.",
            "ClipGetter Studio — Developed by Viennn",
            0x40,
        )

    # Flush any final draft write from pagehide/beforeunload before fast process exit
    server.should_exit = True
    time.sleep(0.08)
    with IO_LOCK:
        pass
    os._exit(0)


if __name__ == "__main__":
    main()
