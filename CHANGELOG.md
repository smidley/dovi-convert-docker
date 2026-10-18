# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.2] - 2026-10-18

### Fixed
- Web UI: the dovi_convert spinner is now shown as a single-line text progress bar that updates in place (`[████░░░░] 45%  Step 1/3 … ⏱ elapsed  ETA`) instead of a new line per update
- Web UI: ANSI escape codes from the dovi_convert spinner (clear-line, cursor hide/show, colors) are stripped from the output
- Web UI: far fewer WebSocket messages and no localStorage write per spinner tick, so the UI stays responsive during long conversion steps

## [1.2.1] - 2026-10-18

### Fixed
- Web UI: progress-bar redraws of dovi_convert (carriage-return lines) no longer flood the output with one line per update; they now update a single line in place

## [1.2.0] - 2026-10-15

### Fixed
- Jellyfin 12.x compatibility: authenticate via `Authorization: MediaBrowser ... Token="..."` instead of the removed `X-Emby-Token` header (also works on 10.x)
- Clearer error message when Jellyfin rejects the API key
- Jellyfin refresh no longer matches items by name fragments; uses exact path, then unique exact filename, then falls back to `/Library/Media/Updated`

### Changed
- Jellyfin library scan is paged (500 items per request) instead of a single 10000-item request
- Jellyfin requests use a timeout
- Updated Python dependencies (FastAPI, aiohttp, pydantic, uvicorn, ...)

## [1.0.0] - 2024-12-26

### Added
- Initial release
- Web interface for dovi_convert script
- Directory browser for selecting media paths
- Real-time output streaming via WebSocket
- Scan functionality to find Dolby Vision files
- Batch conversion with configurable options
- Safe mode support
- Auto-cleanup option for backup files
- Persistent settings storage
- Docker multi-architecture support (amd64, arm64)
- Unraid Community Applications template
- GitHub Actions for automated builds

### Dependencies
- dovi_convert script v6.6.3
- dovi_tool v2.1.2
- FFmpeg
- MKVToolNix
- MediaInfo


