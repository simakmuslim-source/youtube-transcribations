# Super-hybrid YouTube transcription pipeline

This repository uses several independent ways to collect a transcript. Each video is tracked by playlist position, title, YouTube ID, URL, status, provider, and any error returned by each stage.

## Provider order

1. **Public transcript API** (freetranscriptapi) — first choice when a ready transcript is available. Temporary failures are retried. If the endpoint returns HTTP 429, it is disabled for the remainder of that run instead of keeping the workflow asleep for a long time.
2. **YouTube captions** (youtube-transcript-api) — second choice. Russian and English tracks are preferred; if neither is available, the script tries another available track.
3. **Audio download and local speech recognition** — the script first tries pytubefix; if that fails, it tries yt-dlp. The downloaded audio is transcribed with faster-whisper (small, CPU/int8).
4. **Failure report** — if every provider fails, the entry is preserved with the video title, ID, link, and diagnostics for all attempted stages.

No editorial correction is applied to caption text. Whisper output is automatic speech recognition, so it can differ from the source video's subtitles; the source column identifies how each text was produced.

## Output files

The GitHub Actions artifact youtube-transcripts-super-hybrid contains:

- youtube-transcripts.zip — all successful TXT files plus the reports below.
- report.csv — one row for every video in the playlist, including pending/in-progress/error states.
- failed_videos.csv — every video whose status is not ok at report-generation time.
- failed_videos.txt — readable list with title, status, YouTube ID, URL, and failure reason.
- summary.json — counts and details for incomplete videos.
- metadata.json — playlist metadata and one structured record per video.

The workflow also caches completed transcript text files, so a later run can reuse files it already has. Artifacts are retained for 30 days.

## Why FastAPI is not part of the batch job

FastAPI is a framework for exposing Python functions through an HTTP API. This workflow runs unattended in GitHub Actions and publishes a downloadable artifact, so a web server would add infrastructure without improving the actual transcription stages. It could be considered later if the project gets a web dashboard or user-facing API.

## Project inspirations

This pipeline adopts compatible ideas rather than copying full applications:

- [Dicklesworthstone/bulk_transcribe_youtube_videos_from_playlist](https://github.com/Dicklesworthstone/bulk_transcribe_youtube_videos_from_playlist) — local faster-whisper transcription and audio fallback.
- [Fletchenstein/youtube-transcriber](https://github.com/Fletchenstein/youtube-transcriber) — captions-first strategy, Whisper fallback, playlist output and resume-style processing.
- [lifesized/youtube-transcriber](https://github.com/lifesized/youtube-transcriber) — ordered providers and local Whisper as a final fallback. Its repository is AGPL-3.0 licensed; no code has been copied from it into this workflow.
- [iankauskassergei-tech/youtube-transcriptor](https://github.com/iankauskassergei-tech/youtube-transcriptor) — persistent, structured status and error reporting per video. For this batch workflow, CSV/JSON manifests are used instead of adding a database service.
- [FastAPI](https://github.com/fastapi/fastapi) — evaluated, but intentionally not included because no HTTP service is needed for GitHub Actions batch processing.
