"""
DoVi Convert Web Interface
A FastAPI application providing a web UI for the dovi_convert script.
"""

import asyncio
import os
import json
import re
import traceback
import shlex
import logging
import sys
import aiohttp
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
import subprocess

# Configure logging to output to container logs (stdout)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("dovi_convert")

app = FastAPI(title="DoVi Convert", version="1.1.0")

# Mount static files and templates
app.mount("/static", StaticFiles(directory="/app/static"), name="static")
templates = Jinja2Templates(directory="/app/templates")


# Favicon routes (browser requests these at root level)
@app.get("/favicon.ico")
async def favicon():
    return FileResponse("/app/static/favicon.svg", media_type="image/svg+xml")


@app.get("/favicon.svg")
async def favicon_svg():
    return FileResponse("/app/static/favicon.svg", media_type="image/svg+xml")


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def apple_touch_icon():
    # Return the SVG as a fallback (browsers handle this gracefully)
    return FileResponse("/app/static/favicon.svg", media_type="image/svg+xml")


# Configuration
MEDIA_PATH = os.environ.get("MEDIA_PATH", "/media")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config")


class AppState:
    def __init__(self):
        self.is_running = False
        self.scan_cancelled = False
        self.current_process = None
        self.current_action = None
        self.current_progress = {}  # Track current progress for reconnecting clients
        self.websocket_clients: list[WebSocket] = []
        self.scan_path = MEDIA_PATH
        self.settings = self.load_settings()
        self.scan_results = []
        self.scan_cache = self.load_scan_cache()
        self.conversion_history = self.load_history()
        self.scheduled_task = None
    
    def load_settings(self) -> dict:
        settings_file = Path(CONFIG_PATH) / "settings.json"
        if settings_file.exists():
            with open(settings_file) as f:
                return json.load(f)
        return {
            "scan_path": MEDIA_PATH,
            "auto_cleanup": False,
            "safe_mode": False,
            "include_simple_fel": False,
            "scan_depth": 5,
            "use_temp_storage": False,  # Use /temp_storage mount for faster conversion
            "include_movies": True,
            "include_tv_shows": True,
            "jellyfin_url": "",
            "jellyfin_api_key": "",
            "use_jellyfin": False,
            "schedule_enabled": False,
            "schedule_time": "02:00",
            "schedule_days": [6],
            "auto_convert": False
        }
    
    def save_settings(self):
        settings_file = Path(CONFIG_PATH) / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_file, "w") as f:
            json.dump(self.settings, f, indent=2)
    
    def load_scan_cache(self) -> dict:
        cache_file = Path(CONFIG_PATH) / "scan_cache.json"
        if cache_file.exists():
            try:
                with open(cache_file) as f:
                    return json.load(f)
            except:
                pass
        return {"files": {}, "last_scan": None}
    
    def save_scan_cache(self):
        cache_file = Path(CONFIG_PATH) / "scan_cache.json"
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as f:
            json.dump(self.scan_cache, f, indent=2)
    
    def load_history(self) -> list:
        history_file = Path(CONFIG_PATH) / "history.json"
        if history_file.exists():
            try:
                with open(history_file) as f:
                    return json.load(f)
            except:
                pass
        return []
    
    def save_history(self):
        history_file = Path(CONFIG_PATH) / "history.json"
        history_file.parent.mkdir(parents=True, exist_ok=True)
        with open(history_file, "w") as f:
            json.dump(self.conversion_history[-100:], f, indent=2)  # Keep last 100
    
    def add_to_history(self, filename: str, status: str = "success", log_id: str = None):
        self.conversion_history.append({
            "filename": filename,
            "date": datetime.now().isoformat(),
            "status": status,
            "log_id": log_id or datetime.now().strftime("%Y%m%d%H%M%S%f")
        })
        self.save_history()


state = AppState()


class SettingsUpdate(BaseModel):
    scan_path: Optional[str] = None
    auto_cleanup: Optional[bool] = None
    safe_mode: Optional[bool] = None
    include_simple_fel: Optional[bool] = None
    scan_depth: Optional[int] = None
    use_temp_storage: Optional[bool] = None
    include_movies: Optional[bool] = None
    include_tv_shows: Optional[bool] = None
    jellyfin_url: Optional[str] = None
    jellyfin_api_key: Optional[str] = None
    use_jellyfin: Optional[bool] = None
    schedule_enabled: Optional[bool] = None
    schedule_time: Optional[str] = None
    schedule_days: Optional[List[int]] = None
    auto_convert: Optional[bool] = None


class ConvertRequest(BaseModel):
    files: Optional[List[str]] = None


class ScanRequest(BaseModel):
    incremental: Optional[bool] = True


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {
        "request": request,
        "media_path": MEDIA_PATH
    })


@app.get("/api/status")
async def get_status():
    return {
        "is_running": state.is_running,
        "action": state.current_action,
        "progress": state.current_progress,
        "settings": state.settings,
        "media_path": MEDIA_PATH,
        "websocket_clients": len(state.websocket_clients)
    }


@app.get("/api/debug")
async def debug_info():
    import shutil
    import subprocess
    
    dovi_convert_path = "/usr/local/bin/dovi_convert"
    dovi_convert_exists = Path(dovi_convert_path).exists()
    
    # Get script info if it exists
    script_info = None
    if dovi_convert_exists:
        try:
            result = subprocess.run(["head", "-5", dovi_convert_path], capture_output=True, text=True)
            script_info = result.stdout[:200] if result.returncode == 0 else "Could not read"
        except:
            script_info = "Error reading script"
    
    return {
        "dovi_convert": dovi_convert_path if dovi_convert_exists else None,
        "dovi_convert_exists": dovi_convert_exists,
        "dovi_convert_executable": os.access(dovi_convert_path, os.X_OK) if dovi_convert_exists else False,
        "script_preview": script_info,
        "dovi_tool": shutil.which("dovi_tool"),
        "ffmpeg": shutil.which("ffmpeg"),
        "mediainfo": shutil.which("mediainfo"),
        "bash": shutil.which("bash"),
        "sh": shutil.which("sh"),
        "media_path": MEDIA_PATH,
        "media_exists": Path(MEDIA_PATH).exists(),
        "config_path": CONFIG_PATH,
        "settings": state.settings,
        "websocket_clients": len(state.websocket_clients),
        "is_running": state.is_running,
        "cache_entries": len(state.scan_cache.get("files", {}))
    }


@app.get("/api/settings")
async def get_settings():
    return state.settings


@app.post("/api/settings")
async def update_settings(settings: SettingsUpdate):
    if settings.scan_path is not None:
        if not state.settings.get("use_jellyfin") and not Path(settings.scan_path).exists():
            raise HTTPException(status_code=400, detail="Path does not exist")
        state.settings["scan_path"] = settings.scan_path
    
    if settings.auto_cleanup is not None:
        state.settings["auto_cleanup"] = settings.auto_cleanup
    if settings.safe_mode is not None:
        state.settings["safe_mode"] = settings.safe_mode
    if settings.include_simple_fel is not None:
        state.settings["include_simple_fel"] = settings.include_simple_fel
    if settings.scan_depth is not None:
        state.settings["scan_depth"] = max(1, min(10, settings.scan_depth))
    if settings.use_temp_storage is not None:
        state.settings["use_temp_storage"] = settings.use_temp_storage
    if settings.include_movies is not None:
        state.settings["include_movies"] = settings.include_movies
    if settings.include_tv_shows is not None:
        state.settings["include_tv_shows"] = settings.include_tv_shows
    if settings.jellyfin_url is not None:
        state.settings["jellyfin_url"] = settings.jellyfin_url.rstrip('/')
    if settings.jellyfin_api_key is not None:
        state.settings["jellyfin_api_key"] = settings.jellyfin_api_key
    if settings.use_jellyfin is not None:
        state.settings["use_jellyfin"] = settings.use_jellyfin
    if settings.schedule_enabled is not None:
        state.settings["schedule_enabled"] = settings.schedule_enabled
    if settings.schedule_time is not None:
        state.settings["schedule_time"] = settings.schedule_time
    if settings.schedule_days is not None:
        state.settings["schedule_days"] = settings.schedule_days
    if settings.auto_convert is not None:
        state.settings["auto_convert"] = settings.auto_convert
    
    state.save_settings()
    
    # Update scheduled task if needed
    if settings.schedule_enabled is not None:
        setup_scheduled_scan()
    
    return state.settings


class BackupStatsCache:
    """Cache for backup statistics to avoid repeated filesystem walks."""
    def __init__(self):
        self.count = 0
        self.size = 0
        self.last_update = None
        self.cache_ttl = 60  # Cache for 60 seconds
    
    def is_valid(self):
        if self.last_update is None:
            return False
        return (datetime.now() - self.last_update).total_seconds() < self.cache_ttl
    
    def update(self, count: int, size: int):
        self.count = count
        self.size = size
        self.last_update = datetime.now()


backup_stats_cache = BackupStatsCache()


@app.get("/api/stats")
async def get_stats():
    """Get library statistics and backup info."""
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    
    # Count from cache
    profile7_count = sum(1 for f in state.scan_cache.get("files", {}).values() if f.get("profile") == "profile7")
    profile8_count = sum(1 for f in state.scan_cache.get("files", {}).values() if f.get("profile") == "profile8")
    hdr10_count = sum(1 for f in state.scan_cache.get("files", {}).values() if f.get("profile") == "hdr10")
    sdr_count = sum(1 for f in state.scan_cache.get("files", {}).values() if f.get("profile") == "sdr")
    
    # Use cached backup stats if available
    if backup_stats_cache.is_valid():
        backup_count = backup_stats_cache.count
        backup_size = backup_stats_cache.size
    else:
        # Count backup files (expensive operation - cache it)
        backup_count = 0
        backup_size = 0
        try:
            for root, _, files in os.walk(scan_path):
                for f in files:
                    if f.endswith(('.bak', '.backup', '.original', '.bak.dovi_convert')):
                        backup_count += 1
                        try:
                            backup_size += os.path.getsize(os.path.join(root, f))
                        except:
                            pass
        except:
            pass
        backup_stats_cache.update(backup_count, backup_size)
    
    return {
        "profile7_count": profile7_count,
        "profile8_count": profile8_count,
        "hdr10_count": hdr10_count,
        "sdr_count": sdr_count,
        "backup_count": backup_count,
        "backup_size": backup_size,
        "history": state.conversion_history[-20:],
        "last_scan": state.scan_cache.get("last_scan")
    }


@app.get("/api/disk-space")
async def get_disk_space_info():
    """Get disk space info for scan path and temp storage."""
    import shutil
    
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    TEMP_STORAGE_PATH = "/temp_storage"
    
    result = {}
    
    # Scan path disk space
    try:
        if Path(scan_path).exists():
            total, used, free = shutil.disk_usage(scan_path)
            result["scan_path"] = {
                "path": scan_path,
                "total_gb": total / (1024**3),
                "used_gb": used / (1024**3),
                "free_gb": free / (1024**3),
                "percent_used": round((used / total) * 100, 1)
            }
    except Exception as e:
        result["scan_path"] = {"error": str(e)}
    
    # Temp storage disk space
    try:
        if os.path.isdir(TEMP_STORAGE_PATH) and os.path.ismount(TEMP_STORAGE_PATH):
            total, used, free = shutil.disk_usage(TEMP_STORAGE_PATH)
            result["temp_storage"] = {
                "path": TEMP_STORAGE_PATH,
                "mounted": True,
                "total_gb": total / (1024**3),
                "used_gb": used / (1024**3),
                "free_gb": free / (1024**3),
                "percent_used": round((used / total) * 100, 1)
            }
        else:
            result["temp_storage"] = {"mounted": False}
    except Exception as e:
        result["temp_storage"] = {"error": str(e)}
    
    return result


@app.get("/api/results")
async def get_cached_results():
    """Get cached scan results for display in results pane."""
    files = state.scan_cache.get("files", {})
    last_scan = state.scan_cache.get("last_scan")
    
    if not files:
        return {"results": None, "last_scan": None}
    
    # Build results in the same format as scan results
    dv_profile7_files = []
    dv_profile8_files = []
    
    for filepath, data in files.items():
        profile = data.get("profile")
        if profile in ("profile7", "profile8"):
            fel_type = data.get("fel_type", "unknown")
            hdr_label = f"Dolby Vision Profile {'7' if profile == 'profile7' else '8'}"
            if profile == "profile7" and fel_type:
                hdr_label += f" ({fel_type})"
            
            file_info = {
                "path": filepath,
                "name": Path(filepath).name,
                "hdr": hdr_label,
                "fel_type": fel_type if profile == "profile7" else None,
                "cached": True
            }
            if profile == "profile7":
                dv_profile7_files.append(file_info)
            else:
                dv_profile8_files.append(file_info)
    
    # Only return results if there are DV files
    if not dv_profile7_files and not dv_profile8_files:
        return {"results": None, "last_scan": last_scan}
    
    return {
        "results": {
            "profile7": dv_profile7_files,
            "profile8": dv_profile8_files
        },
        "last_scan": last_scan
    }


@app.get("/api/backups")
async def list_backups():
    """List all backup files that can be restored."""
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    backups = []
    
    # Backup file patterns from dovi_convert
    backup_patterns = ('.bak.dovi_convert', '.mkv.bak', '.bak', '.backup', '.original')
    
    try:
        for root, _, files in os.walk(scan_path):
            for f in files:
                if any(f.endswith(ext) for ext in backup_patterns):
                    filepath = os.path.join(root, f)
                    try:
                        stat = os.stat(filepath)
                        
                        # Determine what the original filename would be
                        original_name = f
                        for ext in backup_patterns:
                            if f.endswith(ext):
                                original_name = f[:-len(ext)]
                                if not original_name.endswith('.mkv'):
                                    original_name += '.mkv'
                                break
                        
                        original_path = os.path.join(root, original_name)
                        converted_exists = os.path.exists(original_path)
                        
                        backups.append({
                            "backup_path": filepath,
                            "backup_name": f,
                            "original_name": original_name,
                            "original_path": original_path,
                            "converted_exists": converted_exists,
                            "size": stat.st_size,
                            "modified": stat.st_mtime,
                            "directory": root
                        })
                    except:
                        pass
    except Exception as e:
        logger.error(f"Error listing backups: {e}")
    
    # Sort by modification time, newest first
    backups.sort(key=lambda x: x.get("modified", 0), reverse=True)
    
    return {"backups": backups, "total": len(backups)}


class RestoreRequest(BaseModel):
    backup_path: str


@app.post("/api/backups/restore")
async def restore_backup(request: RestoreRequest):
    """Restore a backup file, replacing the converted version."""
    backup_path = request.backup_path
    
    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="Backup file not found")
    
    # Determine original filename
    backup_patterns = ('.bak.dovi_convert', '.mkv.bak', '.bak', '.backup', '.original')
    original_name = os.path.basename(backup_path)
    
    for ext in backup_patterns:
        if original_name.endswith(ext):
            original_name = original_name[:-len(ext)]
            if not original_name.endswith('.mkv'):
                original_name += '.mkv'
            break
    
    original_path = os.path.join(os.path.dirname(backup_path), original_name)
    
    try:
        # If converted file exists, remove it first
        if os.path.exists(original_path):
            logger.info(f"Removing converted file: {original_path}")
            os.remove(original_path)
        
        # Rename backup to original
        logger.info(f"Restoring backup: {backup_path} -> {original_path}")
        os.rename(backup_path, original_path)
        
        # Update cache - mark as profile7 again
        if original_path in state.scan_cache.get("files", {}):
            state.scan_cache["files"][original_path]["profile"] = "profile7"
            state.save_scan_cache()
        
        # Refresh Jellyfin metadata if enabled
        if state.settings.get("use_jellyfin"):
            logger.info("Jellyfin integration enabled - triggering metadata refresh after restore")
            await refresh_jellyfin_item(original_path)
        
        return {
            "success": True,
            "restored": original_name,
            "backup_removed": backup_path
        }
    except Exception as e:
        logger.error(f"Error restoring backup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/backups/delete")
async def delete_single_backup(request: RestoreRequest):
    """Delete a single backup file."""
    backup_path = request.backup_path
    
    if not os.path.exists(backup_path):
        raise HTTPException(status_code=404, detail="Backup file not found")
    
    try:
        size = os.path.getsize(backup_path)
        os.remove(backup_path)
        logger.info(f"Deleted backup: {backup_path}")
        return {"success": True, "freed": size}
    except Exception as e:
        logger.error(f"Error deleting backup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/backups/clean")
async def clean_backups():
    """Delete all backup files."""
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    deleted = 0
    freed = 0
    backup_patterns = ('.bak.dovi_convert', '.mkv.bak', '.bak', '.backup', '.original')
    
    try:
        for root, _, files in os.walk(scan_path):
            for f in files:
                if any(f.endswith(ext) for ext in backup_patterns):
                    filepath = os.path.join(root, f)
                    try:
                        size = os.path.getsize(filepath)
                        os.remove(filepath)
                        deleted += 1
                        freed += size
                        logger.info(f"Deleted backup: {filepath}")
                    except Exception as e:
                        logger.warning(f"Failed to delete {filepath}: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    
    return {"deleted": deleted, "freed": freed}


@app.get("/api/browse")
async def browse_directory(path: str = "/"):
    try:
        target_path = Path(path)
        if not target_path.exists():
            target_path = Path(MEDIA_PATH)
        
        directories = []
        for item in sorted(target_path.iterdir()):
            if item.is_dir() and not item.name.startswith('.'):
                directories.append({
                    "name": item.name,
                    "path": str(item)
                })
        
        return {
            "current": str(target_path),
            "parent": str(target_path.parent) if target_path != target_path.parent else None,
            "directories": directories
        }
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied")


@app.get("/api/jellyfin/libraries")
async def get_jellyfin_libraries():
    """Get Jellyfin library paths to help with path mapping."""
    url = state.settings.get("jellyfin_url", "")
    api_key = state.settings.get("jellyfin_api_key", "")
    
    if not url or not api_key:
        raise HTTPException(status_code=400, detail="Jellyfin URL and API key are required")
    
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"X-Emby-Token": api_key}
            async with session.get(f"{url}/Library/VirtualFolders", headers=headers) as resp:
                if resp.status == 200:
                    libraries = await resp.json()
                    result = []
                    all_paths = set()
                    
                    for lib in libraries:
                        lib_info = {
                            "name": lib.get("Name", "Unknown"),
                            "type": lib.get("CollectionType", "unknown"),
                            "paths": lib.get("Locations", [])
                        }
                        result.append(lib_info)
                        for p in lib_info["paths"]:
                            all_paths.add(p)
                    
                    # Extract common root paths
                    root_paths = set()
                    for p in all_paths:
                        # Get the first directory component after root
                        parts = p.strip('/').split('/')
                        if parts:
                            root_paths.add('/' + parts[0])
                    
                    return {
                        "success": True,
                        "libraries": result,
                        "all_paths": list(all_paths),
                        "root_paths": list(root_paths),
                        "suggestion": "Mount these paths in your container with matching names"
                    }
                else:
                    raise HTTPException(status_code=resp.status, detail="Failed to get Jellyfin libraries")
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")


@app.post("/api/jellyfin/test")
async def test_jellyfin():
    url = state.settings.get("jellyfin_url", "")
    api_key = state.settings.get("jellyfin_api_key", "")
    
    if not url or not api_key:
        raise HTTPException(status_code=400, detail="Jellyfin URL and API key are required")
    
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"X-Emby-Token": api_key}
            async with session.get(f"{url}/System/Info", headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = {
                        "success": True,
                        "server_name": data.get("ServerName", "Unknown"),
                        "version": data.get("Version", "Unknown")
                    }
                    
                    # Also get library paths for path mapping help
                    try:
                        async with session.get(f"{url}/Library/VirtualFolders", headers=headers) as lib_resp:
                            if lib_resp.status == 200:
                                libraries = await lib_resp.json()
                                paths = []
                                for lib in libraries:
                                    for p in lib.get("Locations", []):
                                        if p not in paths:
                                            paths.append(p)
                                result["library_paths"] = paths
                                if paths:
                                    result["path_warning"] = f"Jellyfin uses paths like: {paths[0]}. Make sure these are mounted in the container."
                    except:
                        pass
                    
                    return result
                else:
                    raise HTTPException(status_code=resp.status, detail="Failed to connect to Jellyfin")
    except aiohttp.ClientError as e:
        raise HTTPException(status_code=500, detail=f"Connection error: {str(e)}")


@app.post("/api/scan")
async def start_scan(request: ScanRequest = ScanRequest()):
    if state.is_running:
        raise HTTPException(status_code=409, detail="A process is already running")
    
    state.current_action = "scan"
    await broadcast_message({"type": "status", "running": True, "action": "scan"})
    
    if state.settings.get("use_jellyfin"):
        jellyfin_url = state.settings.get("jellyfin_url", "")
        jellyfin_key = state.settings.get("jellyfin_api_key", "")
        
        if not jellyfin_url or not jellyfin_key:
            await broadcast_message({"type": "output", "data": "❌ Jellyfin URL and API key are required\n"})
            await broadcast_message({"type": "status", "running": False})
            return {"status": "error", "message": "Jellyfin not configured"}
        
        asyncio.create_task(run_jellyfin_scan())
    else:
        asyncio.create_task(run_scan(incremental=request.incremental))
    
    return {"status": "started", "action": "scan"}


@app.post("/api/convert")
async def start_convert(request: ConvertRequest = ConvertRequest()):
    if state.is_running:
        raise HTTPException(status_code=409, detail="A process is already running")
    
    state.current_action = "convert"
    await broadcast_message({"type": "status", "running": True, "action": "convert"})
    asyncio.create_task(run_convert(files=request.files))
    return {"status": "started", "action": "convert"}


@app.post("/api/stop")
async def stop_process():
    if state.is_running:
        state.scan_cancelled = True
        if state.current_process:
            try:
                state.current_process.terminate()
            except:
                pass
        await broadcast_message({"type": "output", "data": "\n⚠️ Stop requested...\n"})
        return {"status": "stopping"}
    raise HTTPException(status_code=404, detail="No process running")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    try:
        await websocket.accept()
    except Exception as e:
        logger.error(f"WebSocket accept failed: {e}")
        return
        
    state.websocket_clients.append(websocket)
    logger.info(f"WebSocket connected. Total clients: {len(state.websocket_clients)}")
    
    try:
        # Send current status including progress if conversion is running
        await websocket.send_json({
            "type": "status",
            "running": state.is_running,
            "action": state.current_action,
            "settings": state.settings
        })
        
        # If a conversion is in progress, send current progress
        if state.is_running and state.current_progress:
            await websocket.send_json({
                "type": "progress",
                "data": state.current_progress
            })
        
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                try:
                    await websocket.send_json({"type": "keepalive"})
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected normally")
    except Exception as e:
        logger.error(f"WebSocket error: {type(e).__name__}: {e}")
    finally:
        if websocket in state.websocket_clients:
            state.websocket_clients.remove(websocket)
        logger.info(f"WebSocket disconnected. Total clients: {len(state.websocket_clients)}")


async def broadcast_message(message: dict):
    msg_type = message.get("type", "unknown")
    
    # Store progress state for reconnecting clients
    if msg_type == "progress":
        state.current_progress = message.get("data", {})
        # Clear progress when complete
        if state.current_progress.get("status") == "complete":
            state.current_progress = {}
    
    if not state.websocket_clients:
        # Log important messages that would be missed
        if msg_type in ("results", "conversion_complete", "status"):
            logger.warning(f"No WebSocket clients to receive '{msg_type}' message")
        return
        
    disconnected = []
    for client in state.websocket_clients:
        try:
            await client.send_json(message)
        except Exception as e:
            logger.debug(f"Failed to send to WebSocket client: {e}")
            disconnected.append(client)
    
    for client in disconnected:
        if client in state.websocket_clients:
            state.websocket_clients.remove(client)
            logger.info(f"Removed disconnected client. Remaining: {len(state.websocket_clients)}")


async def refresh_jellyfin_item(filepath: str):
    """Refresh Jellyfin metadata for a converted file."""
    logger.info(f"Attempting Jellyfin refresh for: {filepath}")
    
    url = state.settings.get("jellyfin_url", "")
    api_key = state.settings.get("jellyfin_api_key", "")
    
    if not url or not api_key:
        logger.warning("Jellyfin refresh skipped - URL or API key not configured")
        await broadcast_message({"type": "output", "data": f"⚠️ Jellyfin refresh skipped - URL or API key not configured\n"})
        return
    
    try:
        logger.info(f"Jellyfin refresh starting - URL: {url}")
        await broadcast_message({"type": "output", "data": f"🔄 Refreshing Jellyfin metadata...\n"})
        
        filename = Path(filepath).name
        headers = {"X-Emby-Token": api_key}
        
        async with aiohttp.ClientSession() as session:
            # Search for the item by filename
            search_url = f"{url}/Items"
            params = {
                "searchTerm": Path(filepath).stem,  # Search by name without extension
                "IncludeItemTypes": "Movie,Episode",
                "Recursive": "true",
                "Fields": "Path",
                "Limit": "50"
            }
            
            async with session.get(search_url, headers=headers, params=params) as response:
                if response.status != 200:
                    await broadcast_message({"type": "output", "data": f"⚠️ Could not search Jellyfin: {response.status}\n"})
                    return
                
                data = await response.json()
                items = data.get("Items", [])
                
                # Find the matching item by path
                item_id = None
                for item in items:
                    item_path = item.get("Path", "")
                    # Check if paths match (handle different mount points)
                    if item_path and (filepath in item_path or Path(filepath).name in item_path):
                        item_id = item.get("Id")
                        break
                
                if not item_id:
                    # Try direct path search
                    for item in items:
                        if item.get("Name", "").lower() in filename.lower() or filename.lower() in item.get("Name", "").lower():
                            item_id = item.get("Id")
                            break
                
                if item_id:
                    logger.info(f"Found Jellyfin item ID: {item_id}")
                    # Trigger metadata refresh for the item
                    refresh_url = f"{url}/Items/{item_id}/Refresh"
                    refresh_params = {
                        "Recursive": "false",
                        "MetadataRefreshMode": "FullRefresh",
                        "ImageRefreshMode": "None",
                        "ReplaceAllMetadata": "false",
                        "ReplaceAllImages": "false"
                    }
                    
                    async with session.post(refresh_url, headers=headers, params=refresh_params) as refresh_response:
                        if refresh_response.status in (200, 204):
                            logger.info(f"Jellyfin metadata refresh triggered successfully")
                            await broadcast_message({"type": "output", "data": f"✅ Jellyfin metadata refresh triggered\n"})
                        else:
                            logger.warning(f"Jellyfin refresh returned status: {refresh_response.status}")
                            await broadcast_message({"type": "output", "data": f"⚠️ Jellyfin refresh returned: {refresh_response.status}\n"})
                else:
                    logger.warning(f"Could not find item in Jellyfin - searched {len(items)} items")
                    await broadcast_message({"type": "output", "data": f"⚠️ Could not find item in Jellyfin for refresh\n"})
                    await broadcast_message({"type": "output", "data": f"💡 Try running a library scan in Jellyfin to update media info\n"})
                    
    except Exception as e:
        logger.error(f"Jellyfin refresh error: {type(e).__name__}: {str(e)}")
        await broadcast_message({"type": "output", "data": f"⚠️ Jellyfin refresh error: {str(e)}\n"})


async def run_jellyfin_scan():
    """Scan Jellyfin library for Dolby Vision files using two-phase approach."""
    state.is_running = True
    state.scan_cancelled = False
    
    # Reset FEL scan statistics
    FelScanStats.reset()
    
    url = state.settings.get("jellyfin_url", "")
    api_key = state.settings.get("jellyfin_api_key", "")
    
    await broadcast_message({"type": "output", "data": f"{'='*60}\n"})
    await broadcast_message({"type": "output", "data": "🔍 JELLYFIN LIBRARY SCAN (Two-Phase)\n"})
    await broadcast_message({"type": "output", "data": f"{'='*60}\n\n"})
    await broadcast_message({"type": "output", "data": f"🌐 Server: {url}\n\n"})
    
    dv_profile7_files = []
    dv_profile8_files = []
    hdr10_files = []
    sdr_count = 0
    
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"X-Emby-Token": api_key}
            
            include_movies = state.settings.get("include_movies", True)
            include_tv = state.settings.get("include_tv_shows", True)
            
            item_types = []
            if include_movies:
                item_types.append("Movie")
            if include_tv:
                item_types.append("Episode")
            
            if not item_types:
                await broadcast_message({"type": "output", "data": "❌ No content types selected.\n"})
                state.is_running = False
                await broadcast_message({"type": "status", "running": False})
                return
            
            type_str = ",".join(item_types)
            type_display = " and ".join(["Movies" if t == "Movie" else "TV Shows" for t in item_types])
            
            await broadcast_message({"type": "output", "data": f"📡 Fetching {type_display}...\n"})
            
            params = {
                "IncludeItemTypes": type_str,
                "Recursive": "true",
                "Fields": "MediaStreams,Path,MediaSources",
                "Limit": "10000"
            }
            
            async with session.get(f"{url}/Items", headers=headers, params=params) as resp:
                if resp.status != 200:
                    await broadcast_message({"type": "output", "data": f"❌ Failed: HTTP {resp.status}\n"})
                    state.is_running = False
                    await broadcast_message({"type": "status", "running": False})
                    return
                
                data = await resp.json()
                items = data.get("Items", [])
            
            total_items = len(items)
            await broadcast_message({"type": "output", "data": f"📂 Found {total_items} items\n\n"})
            
            # ============================================
            # PHASE 1: Quick scan using Jellyfin metadata
            # ============================================
            await broadcast_message({"type": "output", "data": "📋 PHASE 1: Quick metadata scan...\n"})
            
            needs_deep_scan = []  # Files that need dovi_tool analysis
            
            for i, item in enumerate(items, 1):
                if state.scan_cancelled:
                    await broadcast_message({"type": "output", "data": "\n⚠️ Scan cancelled\n"})
                    break
                
                await broadcast_message({
                    "type": "progress",
                    "data": {
                        "current": i,
                        "total": total_items,
                        "percent": round((i / total_items) * 50),  # Phase 1 is 0-50%
                        "filename": item.get("Name", "Unknown"),
                        "status": "scanning",
                        "step": "Phase 1: Quick scan"
                    }
                })
                
                media_streams = item.get("MediaStreams", [])
                video_stream = next((s for s in media_streams if s.get("Type") == "Video"), None)
                
                if video_stream:
                    video_range = video_stream.get("VideoRange", "")
                    video_range_type = video_stream.get("VideoRangeType", "")
                    hdr_format = video_stream.get("VideoDoViTitle", "") or video_stream.get("Title", "")
                    
                    file_path = item.get("Path", "")
                    file_name = item.get("Name", "Unknown")
                    item_type = item.get("Type", "Unknown")
                    
                    # Extract media details
                    width = video_stream.get("Width", 0)
                    height = video_stream.get("Height", 0)
                    resolution = f"{width}x{height}" if width and height else ""
                    if height >= 2160:
                        resolution = "4K UHD"
                    elif height >= 1080:
                        resolution = "1080p"
                    elif height >= 720:
                        resolution = "720p"
                    
                    codec = video_stream.get("Codec", "") or video_stream.get("VideoCodec", "")
                    if "hevc" in codec.lower() or "h265" in codec.lower():
                        codec = "HEVC"
                    elif "avc" in codec.lower() or "h264" in codec.lower():
                        codec = "H.264"
                    
                    bitrate = video_stream.get("BitRate", 0)
                    bitrate_str = f"{bitrate // 1000000} Mbps" if bitrate else ""
                    
                    # Get file size from item
                    media_sources = item.get("MediaSources", [{}])
                    file_size = media_sources[0].get("Size", 0) if media_sources else 0
                    
                    is_dv = "DoVi" in video_range_type or "Dolby Vision" in str(hdr_format) or video_stream.get("VideoDoViTitle")
                    
                    media_info = {
                        "path": file_path,
                        "name": file_name,
                        "type": item_type,
                        "resolution": resolution,
                        "codec": codec,
                        "bitrate": bitrate_str,
                        "size": file_size
                    }
                    
                    if is_dv:
                        dovi_title = video_stream.get("VideoDoViTitle", "") or hdr_format
                        dovi_title_str = str(dovi_title).lower()
                        
                        # Check for Profile 7 - must have actual DV profile indicator, not just any "7"
                        is_profile7 = (
                            "profile 7" in dovi_title_str or
                            "dvhe.07" in dovi_title_str or
                            "dv profile 7" in dovi_title_str or
                            # Match "7.6" or "7.1" but not random numbers like "2097"
                            bool(re.search(r'\b7\.[0-9]', dovi_title_str))
                        )
                        
                        if is_profile7:
                            # Quick FEL check from metadata
                            hdr_info_str = str(dovi_title) + " " + str(hdr_format)
                            quick_fel = detect_fel_from_mediainfo(hdr_info_str)
                            
                            # Log detections to help debug
                            total_p7 = len(dv_profile7_files) + len(needs_deep_scan)
                            if total_p7 < 5 or quick_fel == 'needs_deep_scan':
                                logger.info(f"[Quick Scan] '{file_name}': hdr='{hdr_info_str[:80]}' -> {quick_fel}")
                            
                            file_entry = {
                                **media_info,
                                "hdr": "Dolby Vision Profile 7",
                                "profile": dovi_title,
                                "hdr_info_str": hdr_info_str  # Save for potential deep scan
                            }
                            
                            if quick_fel in ('MEL', 'FEL'):
                                # Quick detection succeeded - add to results immediately
                                file_entry["fel_type"] = quick_fel
                                dv_profile7_files.append(file_entry)
                                state.scan_cache["files"][file_path] = {"profile": "profile7", "mtime": 0, "fel_type": quick_fel}
                                FelScanStats.quick_detections += 1
                            else:
                                # Needs deep scan - tag for Phase 2
                                file_entry["fel_type"] = "pending"
                                needs_deep_scan.append(file_entry)
                        else:
                            dv_profile8_files.append({
                                **media_info,
                                "hdr": "Dolby Vision Profile 8",
                                "profile": dovi_title,
                            })
                            state.scan_cache["files"][file_path] = {"profile": "profile8", "mtime": 0}
                    elif "HDR" in video_range or "HDR10" in video_range_type:
                        hdr10_files.append({**media_info, "hdr": "HDR10"})
                        state.scan_cache["files"][file_path] = {"profile": "hdr10", "mtime": 0}
                    else:
                        sdr_count += 1
                        state.scan_cache["files"][file_path] = {"profile": "sdr", "mtime": 0}
            
            await broadcast_message({"type": "output", "data": f"✅ Phase 1 complete: {len(dv_profile7_files)} identified, {len(needs_deep_scan)} need deep scan\n\n"})
            
            # ============================================
            # PHASE 2: Deep scan files that need dovi_tool
            # ============================================
            if needs_deep_scan and not state.scan_cancelled:
                await broadcast_message({"type": "output", "data": f"🔬 PHASE 2: Deep scanning {len(needs_deep_scan)} files...\n"})
                await broadcast_message({"type": "output", "data": "   (Extracting HEVC tracks for dovi_tool analysis - this takes ~30-60s per file)\n\n"})
                
                for i, file_entry in enumerate(needs_deep_scan, 1):
                    if state.scan_cancelled:
                        await broadcast_message({"type": "output", "data": "\n⚠️ Scan cancelled\n"})
                        break
                    
                    file_path = file_entry["path"]
                    file_name = file_entry["name"]
                    
                    # For Phase 2, show progress out of deep scan count, not total items
                    await broadcast_message({
                        "type": "progress",
                        "data": {
                            "current": i,
                            "total": len(needs_deep_scan),
                            "percent": round((i / len(needs_deep_scan)) * 100),  # 0-100% for deep scan phase
                            "filename": file_name,
                            "status": "deep_scanning",
                            "step": f"🔬 Deep scan: {i}/{len(needs_deep_scan)}"
                        }
                    })
                    
                    await broadcast_message({"type": "output", "data": f"  🔬 Analyzing: {file_name}...\n"})
                    
                    # Deep scan using dovi_tool
                    if Path(file_path).exists():
                        fel_type = await detect_fel_type_deep(file_path)
                        FelScanStats.deep_scans += 1
                    else:
                        fel_type = "unknown"
                        await broadcast_message({"type": "output", "data": f"     ⚠️ File not accessible\n"})
                    
                    file_entry["fel_type"] = fel_type
                    if "hdr_info_str" in file_entry:
                        del file_entry["hdr_info_str"]  # Clean up temp field
                    
                    # Add to results
                    dv_profile7_files.append(file_entry)
                    state.scan_cache["files"][file_path] = {"profile": "profile7", "mtime": 0, "fel_type": fel_type}
                    
                    fel_indicator = "✅ MEL" if fel_type == "MEL" else "⚠️ FEL" if fel_type == "FEL" else "❓ unknown"
                    await broadcast_message({"type": "output", "data": f"     → {fel_indicator}\n"})
                
                await broadcast_message({"type": "output", "data": f"\n✅ Phase 2 complete\n"})
            
            state.scan_cache["last_scan"] = datetime.now().isoformat()
            state.save_scan_cache()
            
            # Output results
            await broadcast_message({"type": "output", "data": f"\n{'='*60}\n"})
            await broadcast_message({"type": "output", "data": "📊 SCAN RESULTS\n"})
            await broadcast_message({"type": "output", "data": f"{'='*60}\n\n"})
            await broadcast_message({"type": "output", "data": f"🎯 Profile 7 (need conversion): {len(dv_profile7_files)}\n"})
            await broadcast_message({"type": "output", "data": f"✅ Profile 8 (compatible):       {len(dv_profile8_files)}\n"})
            await broadcast_message({"type": "output", "data": f"🔶 HDR10:                        {len(hdr10_files)}\n"})
            await broadcast_message({"type": "output", "data": f"⚪ SDR:                          {sdr_count}\n\n"})
            
            if dv_profile7_files:
                # Count FEL types
                fel_count = sum(1 for f in dv_profile7_files if f.get("fel_type") == "FEL")
                mel_count = sum(1 for f in dv_profile7_files if f.get("fel_type") in ("MEL", "standard"))
                unknown_count = sum(1 for f in dv_profile7_files if f.get("fel_type") in ("unknown", None))
                
                await broadcast_message({"type": "output", "data": "🎯 FILES NEEDING CONVERSION:\n\n"})
                
                if fel_count > 0:
                    await broadcast_message({"type": "output", "data": f"  ⚠️  {fel_count} FEL files (complex - quality loss if converted)\n"})
                if mel_count > 0:
                    await broadcast_message({"type": "output", "data": f"  ✅ {mel_count} MEL/standard files (safe to convert)\n"})
                if unknown_count > 0:
                    await broadcast_message({"type": "output", "data": f"  ❓ {unknown_count} files (FEL type unknown)\n"})
                
                await broadcast_message({"type": "output", "data": "\n"})
                
                for f in dv_profile7_files[:10]:
                    fel_indicator = ""
                    if f.get("fel_type") == "FEL":
                        fel_indicator = " ⚠️ FEL"
                    elif f.get("fel_type") in ("MEL", "standard"):
                        fel_indicator = " ✅"
                    await broadcast_message({"type": "output", "data": f"  📄 {f['name']}{fel_indicator}\n"})
                if len(dv_profile7_files) > 10:
                    await broadcast_message({"type": "output", "data": f"  ... and {len(dv_profile7_files) - 10} more\n"})
                
                if fel_count > 0:
                    await broadcast_message({"type": "output", "data": "\n⚠️  WARNING: FEL files will lose enhancement layer data if converted.\n"})
                    await broadcast_message({"type": "output", "data": "   Consider keeping original files or only converting MEL/standard files.\n"})
            
            await broadcast_message({
                "type": "results",
                "data": {
                    "profile7": dv_profile7_files,
                    "profile8": dv_profile8_files,
                    "hdr10_count": len(hdr10_files),
                    "sdr_count": sdr_count,
                    "source": "jellyfin"
                }
            })
            
            # Log FEL scan statistics
            if FelScanStats.quick_detections > 0 or FelScanStats.deep_scans > 0:
                await broadcast_message({"type": "output", "data": f"\n📊 FEL Detection: {FelScanStats.quick_detections} quick, {FelScanStats.deep_scans} deep scans\n"})
            
            await broadcast_message({"type": "output", "data": f"\n✅ Jellyfin scan complete\n"})
            
    except Exception as e:
        await broadcast_message({"type": "output", "data": f"\n❌ Error: {str(e)}\n"})
        traceback.print_exc()
    finally:
        state.is_running = False
        state.current_action = None
        await broadcast_message({"type": "status", "running": False})
        await broadcast_message({"type": "progress", "data": {"status": "complete"}})


def detect_fel_from_mediainfo(hdr_info: str) -> str:
    """
    Quick FEL detection from mediainfo/Jellyfin HDR format string.
    Uses the Dolby Vision compatibility ID to determine FEL type.
    
    Compatibility IDs (last number in profile):
    - 06 = Cross-compatible (MEL) - safe to convert
    - 01 = FEL - quality loss if converted
    
    Handles various formats:
    - dvhe.07.06, dvhe.07.01
    - Profile 7.06, Profile 7.01
    - DV Profile 7.6, DV 7.1
    - Dolby Vision Profile 7 (06), etc.
    
    Returns: 'MEL', 'FEL', or 'needs_deep_scan'
    """
    import re
    
    hdr_lower = hdr_info.lower()
    
    # Pattern 1: dvhe.07.XX (standard format)
    match = re.search(r'dvhe\.07\.(\d+)', hdr_lower)
    if match:
        compat_id = match.group(1)
        if compat_id == '06' or compat_id == '6':
            return 'MEL'
        elif compat_id == '01' or compat_id == '1':
            return 'FEL'
    
    # Pattern 2: Profile 7.XX or Profile 7 (XX) - Jellyfin format
    match = re.search(r'profile\s*7[.\s]*(\d+)', hdr_lower)
    if match:
        compat_id = match.group(1)
        if compat_id in ('06', '6'):
            return 'MEL'
        elif compat_id in ('01', '1'):
            return 'FEL'
    
    # Pattern 3: DV 7.X or DV7.X
    match = re.search(r'dv\s*7[.\s]*(\d+)', hdr_lower)
    if match:
        compat_id = match.group(1)
        if compat_id in ('06', '6'):
            return 'MEL'
        elif compat_id in ('01', '1'):
            return 'FEL'
    
    # Pattern 4: Look for compatibility ID in parentheses like "(06)" or "(6)"
    match = re.search(r'\(0?([16])\)', hdr_lower)
    if match:
        compat_id = match.group(1)
        if compat_id == '6':
            return 'MEL'
        elif compat_id == '1':
            return 'FEL'
    
    # Pattern 5: Explicit MEL/FEL text
    if 'mel' in hdr_lower or 'cross-compatible' in hdr_lower or 'cross compatible' in hdr_lower:
        return 'MEL'
    if 'fel' in hdr_lower or 'full enhancement' in hdr_lower:
        return 'FEL'
    
    # Pattern 6: BL+EL+RPU indicates FEL, BL+RPU indicates MEL
    if 'bl+el+rpu' in hdr_lower or 'bl + el + rpu' in hdr_lower:
        return 'FEL'
    if 'bl+rpu' in hdr_lower or 'bl + rpu' in hdr_lower:
        return 'MEL'
    
    # Can't determine from metadata - needs deep scan
    return 'needs_deep_scan'


async def detect_fel_type_deep(filepath: str) -> str:
    """
    Deep FEL detection using dovi_tool (slow - extracts HEVC track).
    Only called when mediainfo can't determine FEL type.
    
    Returns: 'MEL' (safe), 'FEL' (complex, quality loss), or 'unknown'
    """
    filename = Path(filepath).name
    logger.info(f"[Deep Scan] Starting for: {filename}")
    
    try:
        # Get video track info from mkvmerge
        proc = await asyncio.create_subprocess_exec(
            "mkvmerge", "-i", filepath,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        output = stdout.decode()
        
        logger.info(f"[Deep Scan] mkvmerge output: {output[:200]}...")
        
        # Find the HEVC track ID
        hevc_track = None
        import re
        for line in output.split('\n'):
            if 'HEVC' in line or 'video' in line.lower():
                match = re.search(r'Track ID (\d+):', line)
                if match:
                    hevc_track = match.group(1)
                    logger.info(f"[Deep Scan] Found HEVC track: {hevc_track}")
                    break
        
        if not hevc_track:
            logger.warning(f"[Deep Scan] No HEVC track found in {filename}")
            return "unknown"
        
        # Extract HEVC track to temp file
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.hevc', delete=False) as tmp:
            tmp_path = tmp.name
        
        try:
            logger.info(f"[Deep Scan] Extracting track {hevc_track} to {tmp_path}")
            proc = await asyncio.create_subprocess_exec(
                "mkvextract", filepath, "tracks", f"{hevc_track}:{tmp_path}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            
            # Wait max 120 seconds for extraction (large files need more time)
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
                logger.info(f"[Deep Scan] mkvextract completed, exit code: {proc.returncode}")
            except asyncio.TimeoutError:
                logger.warning(f"[Deep Scan] mkvextract timed out for {filename}")
                proc.kill()
                await proc.wait()
                return "unknown"
            
            if not Path(tmp_path).exists():
                logger.warning(f"[Deep Scan] Temp file not created for {filename}")
                return "unknown"
            
            file_size = Path(tmp_path).stat().st_size
            logger.info(f"[Deep Scan] Extracted file size: {file_size / 1024 / 1024:.1f} MB")
            
            if file_size == 0:
                logger.warning(f"[Deep Scan] Extracted file is empty for {filename}")
                return "unknown"
            
            # Run dovi_tool info
            logger.info(f"[Deep Scan] Running dovi_tool info...")
            proc = await asyncio.create_subprocess_exec(
                "dovi_tool", "info", "-i", tmp_path, "--summary",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            info_output = stdout.decode() + stderr.decode()
            
            # Log the full dovi_tool output for debugging
            logger.info(f"[Deep Scan] dovi_tool output for {filename}:\n{info_output}")
            
            info_lower = info_output.lower()
            
            # Parse the output
            result = "unknown"
            if "fel" in info_lower or "full enhancement" in info_lower:
                result = "FEL"
            elif "mel" in info_lower or "minimal enhancement" in info_lower:
                result = "MEL"
            elif "el_present: true" in info_lower or "enhancement layer: yes" in info_lower:
                result = "FEL"  # Has EL but type unknown - assume FEL for safety
            elif "profile 7" in info_lower:
                result = "standard"  # Profile 7 but no EL - safe
            
            logger.info(f"[Deep Scan] Result for {filename}: {result}")
            return result
                
        finally:
            try:
                if Path(tmp_path).exists():
                    Path(tmp_path).unlink()
            except:
                pass
                
    except Exception as e:
        logger.error(f"[Deep Scan] Failed for {filepath}: {e}")
        return "unknown"


class FelScanStats:
    """Track FEL scan statistics for reporting."""
    quick_detections = 0
    deep_scans = 0
    
    @classmethod
    def reset(cls):
        cls.quick_detections = 0
        cls.deep_scans = 0


async def detect_fel_type(filepath: str, hdr_info: str = "") -> str:
    """
    Two-phase FEL detection:
    1. Quick check using mediainfo HDR format string
    2. Deep scan with dovi_tool only if needed
    
    Returns: 'MEL' (safe), 'FEL' (quality loss), 'standard' (safe), or 'unknown'
    """
    # Phase 1: Quick detection from mediainfo
    if hdr_info:
        quick_result = detect_fel_from_mediainfo(hdr_info)
        if quick_result in ('MEL', 'FEL'):
            FelScanStats.quick_detections += 1
            return quick_result
    
    # Phase 2: Deep scan needed
    FelScanStats.deep_scans += 1
    return await detect_fel_type_deep(filepath)


async def run_scan(incremental: bool = True):
    """Run Dolby Vision scan using mediainfo."""
    logger.info(f"Starting {'incremental' if incremental else 'full'} scan")
    state.is_running = True
    state.scan_cancelled = False
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    depth = state.settings.get("scan_depth", 5)
    logger.info(f"Scan path: {scan_path}, depth: {depth}")
    
    # Reset FEL scan statistics
    FelScanStats.reset()
    
    await broadcast_message({"type": "output", "data": f"{'='*60}\n"})
    await broadcast_message({"type": "output", "data": f"🔍 DOLBY VISION SCAN {'(Incremental)' if incremental else '(Full)'}\n"})
    await broadcast_message({"type": "output", "data": f"{'='*60}\n\n"})
    await broadcast_message({"type": "output", "data": f"📁 Scan path: {scan_path}\n"})
    await broadcast_message({"type": "output", "data": f"📊 Scan depth: {depth} levels\n\n"})
    
    try:
        # Find all MKV files
        await broadcast_message({"type": "output", "data": "🔎 Searching for MKV files...\n"})
        
        find_cmd = ["find", scan_path, "-maxdepth", str(depth), "-type", "f", "-name", "*.mkv"]
        find_proc = await asyncio.create_subprocess_exec(
            *find_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await find_proc.communicate()
        mkv_files = [f for f in stdout.decode().strip().split('\n') if f]
        
        if not mkv_files:
            await broadcast_message({"type": "output", "data": f"⚠️ No MKV files found\n"})
            state.is_running = False
            await broadcast_message({"type": "status", "running": False})
            return
        
        # Filter files for incremental scan
        files_to_scan = []
        skipped = 0
        
        for filepath in mkv_files:
            try:
                mtime = os.path.getmtime(filepath)
                cached = state.scan_cache.get("files", {}).get(filepath)
                
                if incremental and cached and cached.get("mtime") == mtime:
                    skipped += 1
                else:
                    files_to_scan.append((filepath, mtime))
            except:
                files_to_scan.append((filepath, 0))
        
        await broadcast_message({"type": "output", "data": f"📂 Found {len(mkv_files)} MKV files\n"})
        if skipped > 0:
            await broadcast_message({"type": "output", "data": f"⏭️ Skipping {skipped} unchanged files\n"})
        await broadcast_message({"type": "output", "data": f"📝 Scanning {len(files_to_scan)} files...\n\n"})
        
        if not files_to_scan and skipped > 0:
            await broadcast_message({"type": "output", "data": "✅ All files cached, no new scans needed\n"})
        
        dv_profile7_files = []
        dv_profile8_files = []
        hdr10_count = 0
        sdr_count = 0
        needs_deep_scan = []  # Files that need dovi_tool analysis
        
        # Load existing cache results for skipped files
        for filepath in mkv_files:
            if filepath not in [f[0] for f in files_to_scan]:
                cached = state.scan_cache.get("files", {}).get(filepath)
                if cached:
                    if cached.get("profile") == "profile7":
                        dv_profile7_files.append({
                            "path": filepath,
                            "name": Path(filepath).name,
                            "hdr": "Dolby Vision Profile 7",
                            "fel_type": cached.get("fel_type", "unknown"),
                            "cached": True
                        })
                    elif cached.get("profile") == "profile8":
                        dv_profile8_files.append({
                            "path": filepath,
                            "name": Path(filepath).name,
                            "hdr": "Dolby Vision Profile 8",
                            "cached": True
                        })
                    elif cached.get("profile") == "hdr10":
                        hdr10_count += 1
                    elif cached.get("profile") == "sdr":
                        sdr_count += 1
        
        # ============================================
        # PHASE 1: Quick scan using mediainfo
        # ============================================
        if files_to_scan:
            await broadcast_message({"type": "output", "data": "📋 PHASE 1: Quick metadata scan...\n"})
        
        for i, (filepath, mtime) in enumerate(files_to_scan, 1):
            if state.scan_cancelled:
                await broadcast_message({"type": "output", "data": "\n⚠️ Scan cancelled\n"})
                break
            
            filename = Path(filepath).name
            
            await broadcast_message({
                "type": "progress",
                "data": {
                    "current": i + skipped,
                    "total": len(mkv_files),
                    "percent": round(((i + skipped) / len(mkv_files)) * 50),  # Phase 1 is 0-50%
                    "filename": filename,
                    "status": "scanning",
                    "step": "Phase 1: Quick scan"
                }
            })
            
            try:
                # Get HDR format and media details
                proc = await asyncio.create_subprocess_exec(
                    "mediainfo", "--Output=Video;%HDR_Format%\\n%HDR_Format_Profile%\\n%Width%\\n%Height%\\n%Format%\\n%BitRate%",
                    filepath,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await proc.communicate()
                parts = stdout.decode().strip().split('\n')
                
                hdr_info = parts[0] if len(parts) > 0 else ""
                hdr_profile = parts[1] if len(parts) > 1 else ""
                width = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
                height = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
                codec = parts[4] if len(parts) > 4 else ""
                bitrate = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
                
                # Format resolution
                resolution = ""
                if height >= 2160:
                    resolution = "4K UHD"
                elif height >= 1080:
                    resolution = "1080p"
                elif height >= 720:
                    resolution = "720p"
                elif height > 0:
                    resolution = f"{width}x{height}"
                
                # Format codec
                if "HEVC" in codec or "H.265" in codec:
                    codec = "HEVC"
                elif "AVC" in codec or "H.264" in codec:
                    codec = "H.264"
                
                # Format bitrate
                bitrate_str = f"{bitrate // 1000000} Mbps" if bitrate else ""
                
                # Get file size
                try:
                    file_size = os.path.getsize(filepath)
                except:
                    file_size = 0
                
                full_hdr_info = f"{hdr_info} {hdr_profile}".strip()
                
                media_info = {
                    "path": filepath,
                    "name": filename,
                    "resolution": resolution,
                    "codec": codec,
                    "bitrate": bitrate_str,
                    "size": file_size,
                    "mtime": mtime
                }
                
                if "Dolby Vision" in full_hdr_info:
                    if "dvhe.07" in full_hdr_info or "Profile 7" in full_hdr_info.replace(" ", ""):
                        # Quick FEL check from mediainfo
                        quick_fel = detect_fel_from_mediainfo(full_hdr_info)
                        
                        file_entry = {
                            **media_info,
                            "hdr": full_hdr_info,
                        }
                        
                        if quick_fel in ('MEL', 'FEL'):
                            # Quick detection succeeded - add to results immediately
                            file_entry["fel_type"] = quick_fel
                            dv_profile7_files.append(file_entry)
                            state.scan_cache["files"][filepath] = {
                                "profile": "profile7", 
                                "mtime": mtime,
                                "fel_type": quick_fel
                            }
                            FelScanStats.quick_detections += 1
                        else:
                            # Needs deep scan - tag for Phase 2
                            file_entry["fel_type"] = "pending"
                            needs_deep_scan.append(file_entry)
                    else:
                        dv_profile8_files.append({
                            **media_info,
                            "hdr": full_hdr_info
                        })
                        state.scan_cache["files"][filepath] = {"profile": "profile8", "mtime": mtime}
                elif "HDR10" in full_hdr_info or "SMPTE ST 2086" in full_hdr_info:
                    hdr10_count += 1
                    state.scan_cache["files"][filepath] = {"profile": "hdr10", "mtime": mtime}
                else:
                    sdr_count += 1
                    state.scan_cache["files"][filepath] = {"profile": "sdr", "mtime": mtime}
                    
            except Exception:
                pass
        
        if files_to_scan:
            await broadcast_message({"type": "output", "data": f"✅ Phase 1 complete: {len(dv_profile7_files)} identified, {len(needs_deep_scan)} need deep scan\n\n"})
        
        # ============================================
        # PHASE 2: Deep scan files that need dovi_tool
        # ============================================
        if needs_deep_scan and not state.scan_cancelled:
            await broadcast_message({"type": "output", "data": f"🔬 PHASE 2: Deep scanning {len(needs_deep_scan)} files...\n"})
            await broadcast_message({"type": "output", "data": "   (Extracting HEVC tracks for dovi_tool analysis - this takes ~30-60s per file)\n\n"})
            
            for i, file_entry in enumerate(needs_deep_scan, 1):
                if state.scan_cancelled:
                    await broadcast_message({"type": "output", "data": "\n⚠️ Scan cancelled\n"})
                    break
                
                filepath = file_entry["path"]
                filename = file_entry["name"]
                mtime = file_entry.get("mtime", 0)
                
                # For Phase 2, show progress out of deep scan count
                await broadcast_message({
                    "type": "progress",
                    "data": {
                        "current": i,
                        "total": len(needs_deep_scan),
                        "percent": round((i / len(needs_deep_scan)) * 100),  # 0-100% for deep scan phase
                        "filename": filename,
                        "status": "deep_scanning",
                        "step": f"🔬 Deep scan: {i}/{len(needs_deep_scan)}"
                    }
                })
                
                await broadcast_message({"type": "output", "data": f"  🔬 Analyzing: {filename}...\n"})
                
                # Deep scan using dovi_tool
                fel_type = await detect_fel_type_deep(filepath)
                FelScanStats.deep_scans += 1
                
                file_entry["fel_type"] = fel_type
                if "mtime" in file_entry:
                    del file_entry["mtime"]  # Clean up temp field
                
                # Add to results
                dv_profile7_files.append(file_entry)
                state.scan_cache["files"][filepath] = {
                    "profile": "profile7", 
                    "mtime": mtime,
                    "fel_type": fel_type
                }
                
                fel_indicator = "✅ MEL" if fel_type == "MEL" else "⚠️ FEL" if fel_type == "FEL" else "❓ unknown"
                await broadcast_message({"type": "output", "data": f"     → {fel_indicator}\n"})
            
            await broadcast_message({"type": "output", "data": f"\n✅ Phase 2 complete\n"})
        
        state.scan_cache["last_scan"] = datetime.now().isoformat()
        state.save_scan_cache()
        
        # Output results
        await broadcast_message({"type": "output", "data": f"\n{'='*60}\n"})
        await broadcast_message({"type": "output", "data": "📊 SCAN RESULTS\n"})
        await broadcast_message({"type": "output", "data": f"{'='*60}\n\n"})
        await broadcast_message({"type": "output", "data": f"🎯 Profile 7 (need conversion): {len(dv_profile7_files)}\n"})
        await broadcast_message({"type": "output", "data": f"✅ Profile 8 (compatible):       {len(dv_profile8_files)}\n"})
        await broadcast_message({"type": "output", "data": f"🔶 HDR10:                        {hdr10_count}\n"})
        await broadcast_message({"type": "output", "data": f"⚪ SDR:                          {sdr_count}\n\n"})
        
        if dv_profile7_files:
            # Count FEL types
            fel_count = sum(1 for f in dv_profile7_files if f.get("fel_type") == "FEL")
            mel_count = sum(1 for f in dv_profile7_files if f.get("fel_type") in ("MEL", "standard"))
            unknown_count = sum(1 for f in dv_profile7_files if f.get("fel_type") in ("unknown", None))
            
            await broadcast_message({"type": "output", "data": "🎯 FILES NEEDING CONVERSION:\n\n"})
            
            if fel_count > 0:
                await broadcast_message({"type": "output", "data": f"  ⚠️  {fel_count} FEL files (complex - quality loss if converted)\n"})
            if mel_count > 0:
                await broadcast_message({"type": "output", "data": f"  ✅ {mel_count} MEL/standard files (safe to convert)\n"})
            if unknown_count > 0:
                await broadcast_message({"type": "output", "data": f"  ❓ {unknown_count} files (FEL type unknown)\n"})
            
            await broadcast_message({"type": "output", "data": "\n"})
            
            for f in dv_profile7_files[:10]:
                fel_indicator = ""
                if f.get("fel_type") == "FEL":
                    fel_indicator = " ⚠️ FEL"
                elif f.get("fel_type") in ("MEL", "standard"):
                    fel_indicator = " ✅"
                await broadcast_message({"type": "output", "data": f"  📄 {f['name']}{fel_indicator}\n"})
            if len(dv_profile7_files) > 10:
                await broadcast_message({"type": "output", "data": f"  ... and {len(dv_profile7_files) - 10} more\n"})
            
            if fel_count > 0:
                await broadcast_message({"type": "output", "data": "\n⚠️  WARNING: FEL files will lose enhancement layer data if converted.\n"})
                await broadcast_message({"type": "output", "data": "   Consider keeping original files or only converting MEL/standard files.\n"})
        
        # Log FEL scan statistics
        if FelScanStats.quick_detections > 0 or FelScanStats.deep_scans > 0:
            await broadcast_message({"type": "output", "data": f"\n📊 FEL Detection: {FelScanStats.quick_detections} quick, {FelScanStats.deep_scans} deep scans\n"})
        
        logger.info(f"Scan complete - Profile 7: {len(dv_profile7_files)}, Profile 8: {len(dv_profile8_files)}, HDR10: {hdr10_count}, SDR: {sdr_count}")
        logger.info(f"Broadcasting results to {len(state.websocket_clients)} WebSocket clients")
        
        await broadcast_message({
            "type": "results",
            "data": {
                "profile7": dv_profile7_files,
                "profile8": dv_profile8_files,
                "hdr10_count": hdr10_count,
                "sdr_count": sdr_count
            }
        })
        
        await broadcast_message({"type": "output", "data": f"\n✅ Scan complete\n"})
        
    except Exception as e:
        await broadcast_message({"type": "output", "data": f"\n❌ Error: {str(e)}\n"})
    finally:
        state.is_running = False
        state.scan_cancelled = False
        state.current_action = None
        await broadcast_message({"type": "status", "running": False})
        await broadcast_message({"type": "progress", "data": {"status": "complete"}})


async def copy_file_with_progress(src: str, dst: str, file_num: int, total_files: int, filename: str, operation: str = "Copying"):
    """Copy a file with progress updates using async I/O."""
    src_path = Path(src)
    dst_path = Path(dst)
    
    if not src_path.exists():
        raise FileNotFoundError(f"Source file not found: {src}")
    
    file_size = src_path.stat().st_size
    file_size_gb = file_size / (1024**3)
    
    # Format size for display
    if file_size > 1024**3:
        size_str = f"{file_size_gb:.1f} GB"
    elif file_size > 1024**2:
        size_str = f"{file_size / (1024**2):.1f} MB"
    else:
        size_str = f"{file_size / 1024:.1f} KB"
    
    await broadcast_message({"type": "output", "data": f"\n{'─'*50}\n"})
    await broadcast_message({"type": "output", "data": f"📋 {operation}: {filename}\n"})
    await broadcast_message({"type": "output", "data": f"📊 Size: {size_str}\n"})
    await broadcast_message({"type": "output", "data": f"📍 From: {src}\n"})
    await broadcast_message({"type": "output", "data": f"📍 To: {dst}\n"})
    await broadcast_message({"type": "output", "data": f"{'─'*50}\n\n"})
    
    copied = 0
    chunk_size = 1024 * 1024 * 50  # 50MB chunks for better throughput
    start_time = asyncio.get_event_loop().time()
    last_update = start_time
    last_output_update = start_time
    
    try:
        # Use run_in_executor for non-blocking file I/O
        loop = asyncio.get_event_loop()
        
        def copy_chunk(fsrc, fdst, size):
            """Read and write a chunk, return bytes copied."""
            chunk = fsrc.read(size)
            if chunk:
                fdst.write(chunk)
            return len(chunk) if chunk else 0
        
        with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
            while True:
                # Run the blocking I/O in a thread pool
                bytes_copied = await loop.run_in_executor(None, copy_chunk, fsrc, fdst, chunk_size)
                
                if bytes_copied == 0:
                    break
                    
                copied += bytes_copied
                current_time = asyncio.get_event_loop().time()
                elapsed = current_time - start_time
                
                # Calculate progress
                percent = (copied / file_size) * 100
                speed = copied / elapsed if elapsed > 0 else 0
                remaining_bytes = file_size - copied
                eta = remaining_bytes / speed if speed > 0 else 0
                
                # Format strings
                speed_str = f"{speed / (1024**2):.1f} MB/s"
                copied_gb = copied / (1024**3)
                
                if eta < 60:
                    eta_str = f"{int(eta)}s"
                elif eta < 3600:
                    eta_str = f"{int(eta // 60)}m {int(eta % 60)}s"
                else:
                    eta_str = f"{int(eta // 3600)}h {int((eta % 3600) // 60)}m"
                
                # Update progress bar frequently (every 200ms)
                if current_time - last_update >= 0.2:
                    last_update = current_time
                    
                    await broadcast_message({
                        "type": "progress",
                        "data": {
                            "current": file_num,
                            "total": total_files,
                            "percent": int(((file_num - 1) / total_files) * 100 + (percent * 0.1 / total_files)),
                            "filename": filename,
                            "current_file": src,
                            "status": "converting",
                            "step": f"{operation}: {percent:.0f}% @ {speed_str}",
                            "file_percent": int(percent * 0.1),
                            "eta": eta_str,
                            "elapsed": int(elapsed)
                        }
                    })
                
                # Output text update every 2 seconds
                if current_time - last_output_update >= 2.0:
                    last_output_update = current_time
                    await broadcast_message({
                        "type": "output", 
                        "data": f"  📦 {copied_gb:.2f} / {file_size_gb:.2f} GB ({percent:.1f}%) - {speed_str} - ETA: {eta_str}\n"
                    })
        
        # Final completion message
        total_time = asyncio.get_event_loop().time() - start_time
        avg_speed = file_size / total_time if total_time > 0 else 0
        await broadcast_message({
            "type": "output", 
            "data": f"  ✅ Complete: {file_size_gb:.2f} GB in {int(total_time)}s ({avg_speed / (1024**2):.1f} MB/s avg)\n\n"
        })
        
        return True
    except Exception as e:
        logger.error(f"Copy failed: {e}")
        await broadcast_message({"type": "output", "data": f"❌ Copy failed: {e}\n"})
        # Clean up partial file
        if dst_path.exists():
            try:
                dst_path.unlink()
            except:
                pass
        return False


async def move_file_with_progress(src: str, dst: str, file_num: int, total_files: int, filename: str):
    """Move a file back from temp storage with progress."""
    src_path = Path(src)
    dst_path = Path(dst)
    
    # If same filesystem, just rename (instant)
    try:
        os.rename(src, dst)
        await broadcast_message({"type": "output", "data": f"📋 Moved result to original location (instant)\n"})
        return True
    except OSError:
        # Cross-filesystem, need to copy then delete
        if await copy_file_with_progress(src, dst, file_num, total_files, filename, "Moving back to media"):
            try:
                src_path.unlink()
            except:
                pass
            return True
        return False


def get_disk_space(path: str) -> dict:
    """Get disk space info for a path."""
    try:
        import shutil
        total, used, free = shutil.disk_usage(path)
        return {
            "total": total,
            "used": used,
            "free": free,
            "free_gb": free / (1024**3),
            "percent_used": (used / total) * 100
        }
    except Exception as e:
        logger.warning(f"Could not get disk space for {path}: {e}")
        return None


async def run_convert(files: List[str] = None):
    """Run conversion on selected files or batch."""
    logger.info(f"Starting conversion - files: {len(files) if files else 'batch'}")
    state.is_running = True
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    safe_mode = state.settings.get("safe_mode", False)
    include_simple = state.settings.get("include_simple_fel", False)
    
    # Fixed temp storage path - must be mounted by user in Docker/Unraid
    TEMP_STORAGE_PATH = "/temp_storage"
    use_temp_storage_setting = state.settings.get("use_temp_storage", False)
    temp_storage_available = os.path.isdir(TEMP_STORAGE_PATH) and os.path.ismount(TEMP_STORAGE_PATH)
    use_temp_storage = use_temp_storage_setting and temp_storage_available and safe_mode
    temp_path = TEMP_STORAGE_PATH if use_temp_storage else ""
    
    logger.info(f"Conversion settings - safe_mode: {safe_mode}, include_simple: {include_simple}, use_temp_storage: {use_temp_storage_setting}, temp_available: {temp_storage_available}")
    
    # Check disk space before starting
    if files:
        largest_file_size = 0
        for filepath in files:
            try:
                size = Path(filepath).stat().st_size if Path(filepath).exists() else 0
                largest_file_size = max(largest_file_size, size)
            except:
                pass
        
        # Need at least 2x largest file size (for temp files during conversion)
        required_space = largest_file_size * 2
        required_space_gb = required_space / (1024**3)
        
        # Check temp storage space if using it
        if use_temp_storage:
            temp_space = get_disk_space(TEMP_STORAGE_PATH)
            if temp_space and temp_space["free"] < required_space:
                await broadcast_message({"type": "output", "data": f"❌ Insufficient space in temp storage!\n"})
                await broadcast_message({"type": "output", "data": f"   Required: ~{required_space_gb:.1f} GB, Available: {temp_space['free_gb']:.1f} GB\n"})
                await broadcast_message({"type": "output", "data": f"   Free up space or disable temp storage.\n"})
                state.is_running = False
                state.current_action = None
                await broadcast_message({"type": "status", "running": False})
                await broadcast_message({"type": "progress", "data": {"status": "failed"}})
                return
            elif temp_space:
                await broadcast_message({"type": "output", "data": f"💾 Temp storage: {temp_space['free_gb']:.1f} GB free\n"})
        
        # Check destination space
        dest_space = get_disk_space(scan_path)
        if dest_space and dest_space["free"] < required_space:
            await broadcast_message({"type": "output", "data": f"⚠️ Low disk space warning!\n"})
            await broadcast_message({"type": "output", "data": f"   Required: ~{required_space_gb:.1f} GB, Available: {dest_space['free_gb']:.1f} GB\n"})
            await broadcast_message({"type": "output", "data": f"   Conversion may fail if space runs out.\n\n"})
    
    conversion_results = []  # Track success/failure for each file
    final_status = "complete"  # Track overall status for progress bar
    
    try:
        # Warn if temp storage is enabled but not available
        if use_temp_storage_setting and not temp_storage_available:
            await broadcast_message({"type": "output", "data": f"⚠️ Temp storage enabled but /temp_storage is not mounted!\n"})
            await broadcast_message({"type": "output", "data": f"💡 Add a path mapping in Docker/Unraid: Host Path → /temp_storage\n"})
            await broadcast_message({"type": "output", "data": f"📍 Converting in place (slower on HDD)\n\n"})
        elif use_temp_storage:
            await broadcast_message({"type": "output", "data": f"💾 Using temp storage at {TEMP_STORAGE_PATH} for faster conversion\n\n"})
        
        if files:
            # Convert specific files
            total = len(files)
            logger.info(f"Converting {total} specific files")
            await broadcast_message({"type": "output", "data": f"🎬 Converting {total} files...\n\n"})
            
            for i, filepath in enumerate(files, 1):
                if state.scan_cancelled:
                    await broadcast_message({"type": "output", "data": "\n⚠️ Conversion cancelled\n"})
                    break
                
                filename = Path(filepath).name
                # Generate unique log ID for this conversion
                log_id = datetime.now().strftime("%Y%m%d%H%M%S%f")
                
                # Check if file exists, try to remap path if not
                actual_filepath = filepath
                
                # Helper to normalize ligatures in paths
                def normalize_ligatures(text):
                    ligatures = {'æ': 'ae', 'Æ': 'Ae', 'œ': 'oe', 'Œ': 'Oe', 'ß': 'ss'}
                    for lig, replacement in ligatures.items():
                        text = text.replace(lig, replacement)
                    return text
                
                # Try path case correction first (e.g., /movies -> /Movies)
                if not Path(filepath).exists():
                    # Check if it's just a case mismatch in the path prefix
                    filepath_lower = filepath.lower()
                    scan_path_lower = scan_path.lower()
                    
                    # Try to find a matching prefix (handles /movies vs /Movies)
                    if filepath_lower.startswith(scan_path_lower):
                        # Replace the prefix with the actual scan_path case
                        corrected_path = scan_path + filepath[len(scan_path):]
                        logger.info(f"Trying case-corrected path: {corrected_path}")
                        if Path(corrected_path).exists():
                            actual_filepath = corrected_path
                            filepath = corrected_path
                            logger.info(f"Case-corrected path exists!")
                        else:
                            # Also try with ligature normalization
                            corrected_path_normalized = normalize_ligatures(corrected_path)
                            logger.info(f"Trying ligature-normalized path: {corrected_path_normalized}")
                            if Path(corrected_path_normalized).exists():
                                actual_filepath = corrected_path_normalized
                                filepath = corrected_path_normalized
                                logger.info(f"Ligature-normalized path exists!")
                    
                    # Also check if scan_path itself exists
                    if not Path(scan_path).exists():
                        logger.warning(f"Scan path does not exist: {scan_path}")
                        await broadcast_message({"type": "output", "data": f"⚠️ Scan path does not exist: {scan_path}\n"})
                        # Try common alternatives
                        for alt_path in ['/media', '/movies', '/Movies', '/mnt/media']:
                            if Path(alt_path).exists():
                                logger.info(f"Found alternative path: {alt_path}")
                                await broadcast_message({"type": "output", "data": f"💡 Found media at: {alt_path}\n"})
                                scan_path = alt_path
                                break
                
                if not Path(actual_filepath).exists():
                    # Try to find the file by searching in scan_path
                    await broadcast_message({"type": "output", "data": f"⚠️ File not found at: {filepath}\n"})
                    
                    # Log the attempted path corrections
                    logger.info(f"Path correction attempt - original: {filepath}")
                    logger.info(f"Path correction attempt - actual_filepath: {actual_filepath}")
                    
                    await broadcast_message({"type": "output", "data": f"🔍 Searching in {scan_path}...\n"})
                    
                    # Normalize filename for comparison (handle Unicode like Æ vs Ae)
                    import unicodedata
                    def normalize_name(name):
                        # Normalize Unicode and lowercase for comparison
                        normalized = unicodedata.normalize('NFKD', name)
                        # Handle common ligatures that don't decompose
                        ligatures = {
                            'æ': 'ae', 'Æ': 'ae',
                            'œ': 'oe', 'Œ': 'oe',
                            'ß': 'ss',
                            'ﬁ': 'fi', 'ﬂ': 'fl',
                            'ĳ': 'ij', 'Ĳ': 'ij',
                        }
                        for lig, replacement in ligatures.items():
                            normalized = normalized.replace(lig, replacement)
                        return normalized.lower()
                    
                    target_normalized = normalize_name(filename)
                    logger.info(f"Searching for filename: {filename}")
                    logger.info(f"Normalized target: {target_normalized}")
                    
                    # Search for the file by name (with Unicode normalization)
                    found_path = None
                    dirs_searched = 0
                    for root, dirs, files_in_dir in os.walk(scan_path):
                        dirs_searched += 1
                        # First try exact match
                        if filename in files_in_dir:
                            found_path = os.path.join(root, filename)
                            logger.info(f"Found exact match: {found_path}")
                            break
                        
                        # Then try normalized match
                        for f in files_in_dir:
                            if normalize_name(f) == target_normalized:
                                found_path = os.path.join(root, f)
                                logger.info(f"Found normalized match: {found_path}")
                                break
                        
                        if found_path:
                            break
                    
                    logger.info(f"Searched {dirs_searched} directories, found_path: {found_path}")
                    
                    if found_path and Path(found_path).exists():
                        actual_filepath = found_path
                        await broadcast_message({"type": "output", "data": f"✅ Found at: {actual_filepath}\n"})
                    else:
                        await broadcast_message({"type": "log_marker", "data": {"id": log_id, "filename": filename}})
                        await broadcast_message({"type": "output", "data": f"❌ Could not locate file: {filename}\n"})
                        await broadcast_message({"type": "output", "data": f"💡 Make sure your media is mounted at: {scan_path}\n"})
                        conversion_results.append({"file": filename, "status": "failed"})
                        state.add_to_history(filename, "failed", log_id)
                        continue
                
                # Send initial progress
                await broadcast_message({
                    "type": "progress",
                    "data": {
                        "current": i,
                        "total": total,
                        "percent": round(((i - 1) / total) * 100),
                        "filename": filename,
                        "current_file": actual_filepath,
                        "status": "converting",
                        "step": "Starting...",
                        "file_percent": 0
                    }
                })
                
                # Get file size for progress estimation
                file_size = 0
                file_size_str = ""
                try:
                    file_size = Path(actual_filepath).stat().st_size
                    if file_size > 1024**3:
                        file_size_str = f"{file_size / 1024**3:.1f} GB"
                    elif file_size > 1024**2:
                        file_size_str = f"{file_size / 1024**2:.1f} MB"
                    else:
                        file_size_str = f"{file_size / 1024:.1f} KB"
                except:
                    pass
                
                # Output log marker with ID for linking from history
                await broadcast_message({"type": "log_marker", "data": {"id": log_id, "filename": filename}})
                await broadcast_message({"type": "output", "data": f"\n{'='*60}\n"})
                await broadcast_message({"type": "output", "data": f"[{i}/{total}] {filename}\n"})
                await broadcast_message({"type": "output", "data": f"📁 Path: {actual_filepath}\n"})
                if file_size_str:
                    await broadcast_message({"type": "output", "data": f"📊 Size: {file_size_str}\n"})
                if use_temp_storage:
                    await broadcast_message({"type": "output", "data": f"💾 Temp storage: {temp_path}\n"})
                await broadcast_message({"type": "output", "data": f"{'='*60}\n"})
                
                # Variables for temp storage workflow
                convert_filepath = actual_filepath
                temp_file = None
                
                # If using temp storage, copy file there first
                if use_temp_storage:
                    temp_file = os.path.join(temp_path, filename)
                    
                    copy_success = await copy_file_with_progress(
                        actual_filepath, temp_file, i, total, filename, "Copying to temp storage"
                    )
                    
                    if not copy_success:
                        conversion_results.append({"file": filename, "status": "failed"})
                        state.add_to_history(filename, "failed", log_id)
                        continue
                    
                    convert_filepath = temp_file
                    await broadcast_message({"type": "output", "data": f"✅ Copied to temp storage\n\n"})
                
                cmd = ["/usr/local/bin/dovi_convert", "-convert", convert_filepath]
                if safe_mode:
                    cmd.append("-safe")
                if include_simple:
                    cmd.append("-include-simple")
                cmd.append("-y")
                
                # Run command and track result
                success = await run_convert_command(cmd, cwd=str(Path(convert_filepath).parent), 
                                                    file_num=i, total_files=total, filename=filename,
                                                    file_size=file_size, filepath=actual_filepath)
                
                if success:
                    # Verify conversion by checking for backup file
                    backup_path = convert_filepath + ".bak.dovi_convert"
                    backup_exists = Path(backup_path).exists()
                    
                    if backup_exists:
                        logger.info(f"Backup file verified: {backup_path}")
                        
                        # If using temp storage, move converted file back
                        if use_temp_storage and temp_file:
                            await broadcast_message({"type": "output", "data": f"\n📋 Moving converted file back to original location...\n"})
                            
                            # Move the converted file back to original location
                            move_success = await move_file_with_progress(
                                convert_filepath, actual_filepath, i, total, filename
                            )
                            
                            if not move_success:
                                logger.error("Failed to move converted file back")
                                conversion_results.append({"file": filename, "status": "failed"})
                                state.add_to_history(filename, "failed", log_id)
                                await broadcast_message({"type": "output", "data": f"❌ Failed to move file back to original location\n"})
                                # Clean up temp backup
                                if Path(backup_path).exists():
                                    Path(backup_path).unlink()
                                continue
                            
                            # Move backup from temp to original location
                            original_backup_path = actual_filepath + ".bak.dovi_convert"
                            try:
                                if Path(backup_path).exists():
                                    # Try rename first (same filesystem)
                                    try:
                                        os.rename(backup_path, original_backup_path)
                                    except OSError:
                                        # Cross-filesystem copy
                                        import shutil
                                        shutil.move(backup_path, original_backup_path)
                                    await broadcast_message({"type": "output", "data": f"📦 Backup moved to original location\n"})
                            except Exception as e:
                                logger.warning(f"Could not move backup: {e}")
                            
                            backup_path = original_backup_path
                        
                        conversion_results.append({"file": filename, "status": "success"})
                        state.add_to_history(filename, "success", log_id)
                        await broadcast_message({"type": "output", "data": f"\n✅ {filename} - CONVERTED SUCCESSFULLY\n"})
                        await broadcast_message({"type": "output", "data": f"📦 Backup created: {Path(backup_path).name}\n"})
                        
                        # Update cache - file was converted (update both original and actual path)
                        if filepath in state.scan_cache.get("files", {}):
                            state.scan_cache["files"][filepath]["profile"] = "profile8"
                        if actual_filepath != filepath and actual_filepath in state.scan_cache.get("files", {}):
                            state.scan_cache["files"][actual_filepath]["profile"] = "profile8"
                        state.save_scan_cache()
                        
                        # Refresh Jellyfin metadata if Jellyfin integration is enabled
                        if state.settings.get("use_jellyfin"):
                            logger.info("Jellyfin integration enabled - triggering refresh")
                            await refresh_jellyfin_item(actual_filepath)
                        else:
                            logger.info("Jellyfin integration not enabled - skipping refresh")
                    else:
                        # Command reported success but no backup = didn't actually convert
                        logger.warning(f"No backup file found at {backup_path} - conversion may not have occurred")
                        success = False
                        conversion_results.append({"file": filename, "status": "failed"})
                        state.add_to_history(filename, "failed", log_id)
                        await broadcast_message({"type": "output", "data": f"\n⚠️ {filename} - NO BACKUP FILE CREATED\n"})
                        await broadcast_message({"type": "output", "data": f"💡 dovi_convert may have skipped this file (not Profile 7?) or failed silently\n"})
                        
                        # Clean up temp file if used
                        if use_temp_storage and temp_file and Path(temp_file).exists():
                            Path(temp_file).unlink()
                
                if not success:
                    conversion_results.append({"file": filename, "status": "failed"})
                    state.add_to_history(filename, "failed", log_id)
                    await broadcast_message({"type": "output", "data": f"\n❌ {filename} - CONVERSION FAILED\n"})
                    
                    # Clean up temp files if used
                    if use_temp_storage and temp_file:
                        for temp_cleanup in [temp_file, temp_file + ".bak.dovi_convert"]:
                            if Path(temp_cleanup).exists():
                                try:
                                    Path(temp_cleanup).unlink()
                                    logger.debug(f"Cleaned up temp file: {temp_cleanup}")
                                except Exception as e:
                                    logger.warning(f"Could not clean up {temp_cleanup}: {e}")
            
            # Final summary
            successful = sum(1 for r in conversion_results if r["status"] == "success")
            failed = sum(1 for r in conversion_results if r["status"] == "failed")
            
            await broadcast_message({"type": "output", "data": f"\n{'='*60}\n"})
            await broadcast_message({"type": "output", "data": f"📊 CONVERSION SUMMARY\n"})
            await broadcast_message({"type": "output", "data": f"{'='*60}\n"})
            await broadcast_message({"type": "output", "data": f"✅ Successful: {successful}\n"})
            await broadcast_message({"type": "output", "data": f"❌ Failed: {failed}\n"})
            await broadcast_message({"type": "output", "data": f"{'='*60}\n"})
            
            await broadcast_message({
                "type": "conversion_complete", 
                "data": {"successful": successful, "failed": failed, "results": conversion_results}
            })
            
            # Set final status based on results
            if failed > 0 and successful == 0:
                final_status = "failed"
            elif failed > 0:
                final_status = "partial"  # Some succeeded, some failed
        else:
            # Batch conversion
            await broadcast_message({"type": "output", "data": f"🎬 Starting batch conversion in: {scan_path}\n"})
            
            cmd = ["/usr/local/bin/dovi_convert", "-batch", str(state.settings.get("scan_depth", 5)), "-y"]
            if safe_mode:
                cmd.append("-safe")
            if include_simple:
                cmd.append("-include-simple")
            
            await broadcast_message({"type": "output", "data": f"Running: {' '.join(cmd)}\n\n"})
            await run_command(cmd, cwd=scan_path)
        
        # Auto cleanup if enabled
        if state.settings.get("auto_cleanup", False):
            await broadcast_message({"type": "output", "data": "\n🧹 Running cleanup...\n"})
            cleanup_cmd = ["/usr/local/bin/dovi_convert", "-cleanup", "-r"]
            await run_command(cleanup_cmd, cwd=scan_path)
            
    except Exception as e:
        await broadcast_message({"type": "output", "data": f"\n❌ Error: {str(e)}\n"})
        final_status = "failed"
    finally:
        state.is_running = False
        state.current_action = None
        await broadcast_message({"type": "status", "running": False})
        await broadcast_message({"type": "progress", "data": {"status": final_status}})


async def run_command(cmd: list, cwd: str = None):
    """Run a command and stream output."""
    try:
        # Check if main executable exists first
        main_cmd = cmd[0]
        if main_cmd.startswith('/') and not Path(main_cmd).exists():
            await broadcast_message({"type": "output", "data": f"❌ Script not found: {main_cmd}\n"})
            await broadcast_message({"type": "output", "data": "💡 Try pulling the latest Docker image:\n"})
            await broadcast_message({"type": "output", "data": "   docker pull smidley/dovi-convert:latest\n"})
            return
        
        # Join command into a string for shell execution with proper escaping
        cmd_str = " ".join(shlex.quote(c) for c in cmd)
        
        await broadcast_message({"type": "output", "data": f"🔧 Running: {cmd_str}\n"})
        
        process = await asyncio.create_subprocess_shell(
            cmd_str,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd
        )
        
        state.current_process = process
        
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            text = line.decode('utf-8', errors='replace')
            await broadcast_message({"type": "output", "data": text})
        
        await process.wait()
        
        if process.returncode == 0:
            await broadcast_message({"type": "output", "data": "\n✅ Completed successfully\n"})
        elif process.returncode == 127:
            await broadcast_message({"type": "output", "data": f"\n❌ Command not found (exit 127). Script may be missing.\n"})
        else:
            await broadcast_message({"type": "output", "data": f"\n⚠️ Exited with code {process.returncode}\n"})
    except FileNotFoundError as e:
        await broadcast_message({"type": "output", "data": f"❌ Shell not found: {str(e)}\n"})
    except Exception as e:
        await broadcast_message({"type": "output", "data": f"❌ Error: {type(e).__name__}: {str(e)}\n"})


async def run_convert_command(cmd: list, cwd: str = None, file_num: int = 1, total_files: int = 1, filename: str = "", file_size: int = 0, filepath: str = ""):
    """Run a conversion command with progress parsing."""
    import re
    
    logger.info(f"Running conversion [{file_num}/{total_files}]: {filename} ({file_size / 1024**3:.1f} GB)" if file_size else f"Running conversion [{file_num}/{total_files}]: {filename}")
    
    try:
        # Check if main executable exists first
        main_cmd = cmd[0]
        if main_cmd.startswith('/') and not Path(main_cmd).exists():
            logger.error(f"Script not found: {main_cmd}")
            await broadcast_message({"type": "output", "data": f"❌ Script not found: {main_cmd}\n"})
            return False
        
        # Join command into a string for shell execution with proper escaping
        cmd_str = " ".join(shlex.quote(c) for c in cmd)
        logger.info(f"Command: {cmd_str}")
        
        # Pipe 'y' to handle Simple FEL confirmation prompt (script bug: -y doesn't auto-confirm this)
        # Multiple 'y' answers in case there are multiple prompts
        full_cmd = f"echo 'y\ny\ny' | {cmd_str}"
        
        # Set up environment with temp storage path if configured and available
        env = os.environ.copy()
        TEMP_STORAGE_PATH = "/temp_storage"
        use_temp = state.settings.get("use_temp_storage", False)
        if use_temp and os.path.isdir(TEMP_STORAGE_PATH) and os.path.ismount(TEMP_STORAGE_PATH):
            temp_path = TEMP_STORAGE_PATH
            env["TMPDIR"] = temp_path
            env["TEMP"] = temp_path
            env["TMP"] = temp_path
            await broadcast_message({"type": "output", "data": f"📁 Using temp storage: {temp_path}\n"})
        
        await broadcast_message({"type": "output", "data": f"🔧 Running: {cmd_str}\n\n"})
        
        process = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
            env=env
        )
        
        state.current_process = process
        current_step = "Initializing"
        file_percent = 0
        start_time = asyncio.get_event_loop().time()
        last_progress_time = start_time
        
        # Progress patterns to match dovi_convert output
        step_patterns = [
            (r"Extracting|Extract", "Extracting video stream"),
            (r"Analyzing|Analyz", "Analyzing Dolby Vision"),
            (r"Converting|Convert", "Converting to Profile 8"),
            (r"Remux|Muxing|mux", "Remuxing to MKV"),
            (r"Cleanup|Clean", "Cleaning up temp files"),
            (r"Verif", "Verifying output"),
            (r"Progress|progress", "Processing"),
        ]
        
        # Percentage pattern
        percent_pattern = re.compile(r'(\d+(?:\.\d+)?)\s*%')
        
        output_lines = []
        saw_error = False
        saw_success = False
        buffer = ""
        last_progress_update = 0
        
        current_step_num = 0
        total_steps = 3  # dovi_convert typically has 3 steps: Extract, Convert, Remux
        script_elapsed_secs = 0  # Elapsed time parsed from script output
        
        # Estimate expected time based on file size
        # Typical speeds: SSD ~400MB/s, HDD ~150MB/s, Network ~100MB/s
        # Conservative estimate: ~100 MB/s average, mostly extraction time
        # Extraction is ~70% of total time, Convert ~20%, Remux ~10%
        estimated_total_time = 0
        if file_size > 0:
            # Rough estimate: 1 minute per 6GB (100 MB/s)
            estimated_total_time = (file_size / (100 * 1024 * 1024)) * 1.3  # 30% buffer for convert/remux
        
        async def process_line(text):
            """Process a single line of output"""
            nonlocal saw_error, saw_success, current_step, file_percent, last_progress_update, current_step_num, total_steps, script_elapsed_secs
            
            output_lines.append(text)
            await broadcast_message({"type": "output", "data": text})
            
            # Check for error indicators in output
            if re.search(r'Unknown command|Error:|ERROR|FAILED|failed|No such file|not found', text, re.IGNORECASE):
                if not re.search(r'Command not found', text):  # Ignore our own messages
                    saw_error = True
            
            # Check for success indicators
            if re.search(r'successfully|completed|done|finished|✓|SUCCESS', text, re.IGNORECASE):
                saw_success = True
            
            # Parse elapsed time from script output like "(1m 44s)" or "(5s)"
            time_match = re.search(r'\((\d+)m\s*(\d+)s\)', text)
            if time_match:
                script_elapsed_secs = int(time_match.group(1)) * 60 + int(time_match.group(2))
            else:
                time_match_sec = re.search(r'\((\d+)s\)', text)
                if time_match_sec:
                    script_elapsed_secs = int(time_match_sec.group(1))
            
            # Parse step number from output like "[1/3] Extracting..."
            step_match = re.search(r'\[(\d+)/(\d+)\]', text)
            if step_match:
                current_step_num = int(step_match.group(1))
                total_steps_parsed = int(step_match.group(2))
                if total_steps_parsed > 0:
                    total_steps = total_steps_parsed
                    # Calculate progress based on step and elapsed time
                    # Step weight: Extract ~70%, Convert ~20%, Remux ~10%
                    step_weights = [0.70, 0.20, 0.10]  # Cumulative: 0, 70, 90, 100
                    step_starts = [0, 70, 90]
                    
                    if current_step_num <= len(step_weights):
                        base_percent = step_starts[current_step_num - 1] if current_step_num > 0 else 0
                        step_weight = step_weights[current_step_num - 1] if current_step_num > 0 else step_weights[0]
                        
                        # Estimate progress within step using elapsed time if we have file size estimate
                        if estimated_total_time > 0 and script_elapsed_secs > 0:
                            # Estimate how far through current step based on time
                            step_expected_time = estimated_total_time * step_weight
                            step_progress = min(0.95, script_elapsed_secs / step_expected_time) if step_expected_time > 0 else 0
                            file_percent = int(base_percent + (step_weight * 100 * step_progress))
                        else:
                            file_percent = int(base_percent)
            
            # Parse step name from output
            for pattern, step_name in step_patterns:
                if re.search(pattern, text, re.IGNORECASE):
                    current_step = step_name
                    break
            
            # Parse explicit percentage from output if available
            percent_match = percent_pattern.search(text)
            if percent_match:
                try:
                    explicit_percent = int(float(percent_match.group(1)))
                    # If we have step info, add percentage within step
                    if current_step_num > 0:
                        step_weights = [0.70, 0.20, 0.10]
                        step_starts = [0, 70, 90]
                        base_percent = step_starts[current_step_num - 1] if current_step_num <= len(step_starts) else 90
                        step_weight = step_weights[current_step_num - 1] if current_step_num <= len(step_weights) else 0.10
                        file_percent = int(base_percent + (explicit_percent / 100 * step_weight * 100))
                    else:
                        file_percent = min(99, explicit_percent)
                except:
                    pass
            
            # Send progress update (throttle to avoid flooding)
            current_time = asyncio.get_event_loop().time()
            if current_time - last_progress_update >= 0.5:  # Update every 500ms max
                last_progress_update = current_time
                elapsed = current_time - start_time
                
                # Calculate ETA
                eta_str = ""
                remaining = 0
                
                # Use file-size-based estimate if available
                if estimated_total_time > 0 and elapsed > 5:
                    # Adjust estimate based on actual progress
                    if file_percent > 5:
                        actual_rate = elapsed / (file_percent / 100)
                        remaining = actual_rate - elapsed
                    else:
                        remaining = estimated_total_time - elapsed
                elif file_percent > 5 and elapsed > 5:
                    estimated_total = elapsed / (file_percent / 100)
                    remaining = estimated_total - elapsed
                
                if remaining > 0:
                    if remaining < 60:
                        eta_str = f"{int(remaining)}s"
                    elif remaining < 3600:
                        mins = int(remaining // 60)
                        secs = int(remaining % 60)
                        eta_str = f"{mins}m {secs}s"
                    else:
                        hours = int(remaining // 3600)
                        mins = int((remaining % 3600) // 60)
                        eta_str = f"{hours}h {mins}m"
                
                overall_percent = round(((file_num - 1) / total_files) * 100 + (file_percent / total_files))
                
                # Build step display with step number if available
                step_display = current_step
                if current_step_num > 0:
                    step_display = f"Step {current_step_num}/{total_steps}: {current_step}"
                
                await broadcast_message({
                    "type": "progress",
                    "data": {
                        "current": file_num,
                        "total": total_files,
                        "percent": overall_percent,
                        "filename": filename,
                        "current_file": filepath,
                        "status": "converting",
                        "step": step_display,
                        "file_percent": file_percent,
                        "eta": eta_str,
                        "elapsed": int(elapsed)
                    }
                })
        
        while True:
            # Read in chunks to handle long lines without newlines (like progress bars)
            try:
                chunk = await process.stdout.read(4096)
            except Exception as read_error:
                logger.warning(f"Read error (continuing): {read_error}")
                break
                
            if not chunk:
                # Process any remaining buffer
                if buffer.strip():
                    await process_line(buffer)
                break
            
            # Decode and add to buffer
            text = chunk.decode('utf-8', errors='replace')
            buffer += text
            
            # Handle carriage returns (progress updates) - treat as line breaks
            buffer = buffer.replace('\r\n', '\n').replace('\r', '\n')
            
            # Process complete lines from buffer
            while '\n' in buffer:
                line, buffer = buffer.split('\n', 1)
                if line.strip():  # Only process non-empty lines
                    await process_line(line + '\n')
        
        await process.wait()
        logger.info(f"Process exited with code {process.returncode}")
        
        # Check if output looks like just usage info (script didn't actually run)
        output_text = ''.join(output_lines)
        output_line_count = len(output_lines)
        is_just_usage = 'Usage:' in output_text and 'dovi_convert -' in output_text and not saw_success
        is_empty_output = output_line_count == 0 or len(output_text.strip()) == 0
        
        logger.info(f"Output analysis - lines: {output_line_count}, saw_error: {saw_error}, saw_success: {saw_success}, is_just_usage: {is_just_usage}, is_empty: {is_empty_output}")
        if output_line_count > 0:
            logger.info(f"First line of output: {output_lines[0][:100] if output_lines else 'N/A'}")
        
        # Determine success based on exit code AND output content
        # Empty output is suspicious - script should produce SOMETHING
        if process.returncode == 0 and not saw_error and not is_just_usage and not is_empty_output:
            logger.info(f"Conversion SUCCESS: {filename}")
            await broadcast_message({
                "type": "progress",
                "data": {
                    "current": file_num,
                    "total": total_files,
                    "percent": round((file_num / total_files) * 100),
                    "filename": filename,
                    "current_file": filepath,
                    "status": "converting",
                    "step": "Complete",
                    "file_percent": 100
                }
            })
            return True
        else:
            logger.warning(f"Conversion FAILED: {filename} - exit_code={process.returncode}, saw_error={saw_error}, is_just_usage={is_just_usage}, is_empty={is_empty_output}")
            if is_empty_output:
                logger.warning("Script produced no output - command may not have executed properly")
                await broadcast_message({"type": "output", "data": "\n⚠️ dovi_convert produced no output - script may not have executed\n"})
                await broadcast_message({"type": "output", "data": "💡 Check that dovi_convert is properly installed in the container\n"})
            elif is_just_usage:
                logger.warning("Script showed usage info without converting - possible command format issue")
                await broadcast_message({"type": "output", "data": "\n⚠️ dovi_convert showed usage info but didn't convert - check command format\n"})
            elif saw_error:
                await broadcast_message({"type": "output", "data": "\n⚠️ Errors detected in output\n"})
            return False
            
    except Exception as e:
        logger.error(f"Conversion error: {type(e).__name__}: {str(e)}")
        await broadcast_message({"type": "output", "data": f"❌ Error: {type(e).__name__}: {str(e)}\n"})
        return False


def setup_scheduled_scan():
    """Setup or cancel scheduled scans based on settings."""
    # Cancel existing task if any
    if state.scheduled_task:
        state.scheduled_task.cancel()
        state.scheduled_task = None
    
    if state.settings.get("schedule_enabled"):
        state.scheduled_task = asyncio.create_task(run_scheduler())


async def run_scheduler():
    """Background scheduler for automated scans."""
    while True:
        try:
            schedule_time = state.settings.get("schedule_time", "02:00")
            schedule_days = state.settings.get("schedule_days", [6])
            
            now = datetime.now()
            target_hour, target_minute = map(int, schedule_time.split(":"))
            
            # Check if we should run today
            if now.weekday() in schedule_days:
                target = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
                
                if now >= target and now < target.replace(minute=target_minute + 5):
                    # Time to scan!
                    if not state.is_running:
                        await broadcast_message({"type": "output", "data": "\n⏰ Scheduled scan starting...\n"})
                        
                        if state.settings.get("use_jellyfin"):
                            await run_jellyfin_scan()
                        else:
                            await run_scan(incremental=True)
                        
                        # Auto convert if enabled
                        if state.settings.get("auto_convert"):
                            profile7_count = sum(1 for f in state.scan_cache.get("files", {}).values() if f.get("profile") == "profile7")
                            if profile7_count > 0:
                                await broadcast_message({"type": "output", "data": f"\n🔄 Auto-converting {profile7_count} Profile 7 files...\n"})
                                await run_convert()
            
            # Sleep for 1 minute
            await asyncio.sleep(60)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Scheduler error: {e}")
            await asyncio.sleep(60)


def cleanup_temp_storage():
    """Clean up any orphaned files in temp storage from previous runs."""
    TEMP_STORAGE_PATH = "/temp_storage"
    if not os.path.isdir(TEMP_STORAGE_PATH) or not os.path.ismount(TEMP_STORAGE_PATH):
        return
    
    try:
        cleaned = 0
        freed = 0
        for item in os.listdir(TEMP_STORAGE_PATH):
            item_path = os.path.join(TEMP_STORAGE_PATH, item)
            # Clean up any .mkv or .bak files left from interrupted conversions
            if item.endswith(('.mkv', '.bak', '.bak.dovi_convert', '.hevc', '.bin', '.rpu')):
                try:
                    size = os.path.getsize(item_path)
                    os.remove(item_path)
                    cleaned += 1
                    freed += size
                    logger.info(f"Cleaned up orphaned temp file: {item}")
                except Exception as e:
                    logger.warning(f"Could not clean up {item_path}: {e}")
        
        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} orphaned temp files, freed {freed / (1024**3):.2f} GB")
    except Exception as e:
        logger.warning(f"Error during temp storage cleanup: {e}")


@app.on_event("startup")
async def startup_event():
    """Initialize scheduler on startup."""
    logger.info("="*50)
    logger.info("DoVi Convert Web Interface starting up")
    logger.info(f"Media path: {MEDIA_PATH}")
    logger.info(f"Config path: {CONFIG_PATH}")
    logger.info(f"Scan path (from settings): {state.settings.get('scan_path', MEDIA_PATH)}")
    logger.info(f"Cached files: {len(state.scan_cache.get('files', {}))}")
    logger.info(f"Conversion history: {len(state.conversion_history)} entries")
    logger.info("="*50)
    
    # Clean up any orphaned temp files from previous runs
    cleanup_temp_storage()
    
    if state.settings.get("schedule_enabled"):
        logger.info("Scheduled scans enabled - starting scheduler")
        setup_scheduled_scan()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
