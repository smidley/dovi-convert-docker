"""
DoVi Convert Web Interface
A FastAPI application providing a web UI for the dovi_convert script.
"""

import asyncio
import os
import json
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


@app.get("/api/stats")
async def get_stats():
    """Get library statistics and backup info."""
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    
    # Count from cache
    profile7_count = sum(1 for f in state.scan_cache.get("files", {}).values() if f.get("profile") == "profile7")
    profile8_count = sum(1 for f in state.scan_cache.get("files", {}).values() if f.get("profile") == "profile8")
    hdr10_count = sum(1 for f in state.scan_cache.get("files", {}).values() if f.get("profile") == "hdr10")
    sdr_count = sum(1 for f in state.scan_cache.get("files", {}).values() if f.get("profile") == "sdr")
    
    # Count backup files
    backup_count = 0
    backup_size = 0
    try:
        for root, _, files in os.walk(scan_path):
            for f in files:
                if f.endswith(('.bak', '.backup', '.original')):
                    backup_count += 1
                    try:
                        backup_size += os.path.getsize(os.path.join(root, f))
                    except:
                        pass
    except:
        pass
    
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
            file_info = {
                "path": filepath,
                "name": Path(filepath).name,
                "hdr": f"Dolby Vision Profile {'7' if profile == 'profile7' else '8'}",
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
                    return {
                        "success": True,
                        "server_name": data.get("ServerName", "Unknown"),
                        "version": data.get("Version", "Unknown")
                    }
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
        await websocket.send_json({
            "type": "status",
            "running": state.is_running,
            "settings": state.settings
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
    """Scan Jellyfin library for Dolby Vision files."""
    state.is_running = True
    state.scan_cancelled = False
    
    url = state.settings.get("jellyfin_url", "")
    api_key = state.settings.get("jellyfin_api_key", "")
    
    await broadcast_message({"type": "output", "data": f"{'='*60}\n"})
    await broadcast_message({"type": "output", "data": "🔍 JELLYFIN LIBRARY SCAN (Instant)\n"})
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
            await broadcast_message({"type": "output", "data": "🎬 Analyzing HDR formats...\n\n"})
            
            for i, item in enumerate(items, 1):
                if state.scan_cancelled:
                    await broadcast_message({"type": "output", "data": "\n⚠️ Scan cancelled\n"})
                    break
                
                await broadcast_message({
                    "type": "progress",
                    "data": {
                        "current": i,
                        "total": total_items,
                        "percent": round((i / total_items) * 100),
                        "filename": item.get("Name", "Unknown"),
                        "status": "scanning"
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
                        
                        if "7" in str(dovi_title) or "dvhe.07" in str(dovi_title).lower():
                            dv_profile7_files.append({
                                **media_info,
                                "hdr": "Dolby Vision Profile 7",
                                "profile": dovi_title,
                            })
                            state.scan_cache["files"][file_path] = {"profile": "profile7", "mtime": 0}
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
                await broadcast_message({"type": "output", "data": "🎯 FILES NEEDING CONVERSION:\n\n"})
                for f in dv_profile7_files[:10]:
                    await broadcast_message({"type": "output", "data": f"  📄 {f['name']}\n"})
                if len(dv_profile7_files) > 10:
                    await broadcast_message({"type": "output", "data": f"  ... and {len(dv_profile7_files) - 10} more\n"})
            
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
            
            await broadcast_message({"type": "output", "data": f"\n✅ Jellyfin scan complete\n"})
            
    except Exception as e:
        await broadcast_message({"type": "output", "data": f"\n❌ Error: {str(e)}\n"})
        traceback.print_exc()
    finally:
        state.is_running = False
        state.current_action = None
        await broadcast_message({"type": "status", "running": False})
        await broadcast_message({"type": "progress", "data": {"status": "complete"}})


async def run_scan(incremental: bool = True):
    """Run Dolby Vision scan using mediainfo."""
    logger.info(f"Starting {'incremental' if incremental else 'full'} scan")
    state.is_running = True
    state.scan_cancelled = False
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    depth = state.settings.get("scan_depth", 5)
    logger.info(f"Scan path: {scan_path}, depth: {depth}")
    
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
        
        # Scan new/changed files
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
                    "percent": round(((i + skipped) / len(mkv_files)) * 100),
                    "filename": filename,
                    "status": "scanning"
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
                    "size": file_size
                }
                
                if "Dolby Vision" in full_hdr_info:
                    if "dvhe.07" in full_hdr_info or "Profile 7" in full_hdr_info.replace(" ", ""):
                        dv_profile7_files.append({
                            **media_info,
                            "hdr": full_hdr_info
                        })
                        state.scan_cache["files"][filepath] = {"profile": "profile7", "mtime": mtime}
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
            await broadcast_message({"type": "output", "data": "🎯 FILES NEEDING CONVERSION:\n\n"})
            for f in dv_profile7_files[:10]:
                await broadcast_message({"type": "output", "data": f"  📄 {f['name']}\n"})
            if len(dv_profile7_files) > 10:
                await broadcast_message({"type": "output", "data": f"  ... and {len(dv_profile7_files) - 10} more\n"})
        
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


async def run_convert(files: List[str] = None):
    """Run conversion on selected files or batch."""
    logger.info(f"Starting conversion - files: {len(files) if files else 'batch'}")
    state.is_running = True
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    safe_mode = state.settings.get("safe_mode", False)
    include_simple = state.settings.get("include_simple_fel", False)
    logger.info(f"Conversion settings - safe_mode: {safe_mode}, include_simple: {include_simple}")
    
    conversion_results = []  # Track success/failure for each file
    final_status = "complete"  # Track overall status for progress bar
    
    try:
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
                if not Path(filepath).exists():
                    # Try to find the file by searching in scan_path
                    await broadcast_message({"type": "output", "data": f"⚠️ File not found at: {filepath}\n"})
                    await broadcast_message({"type": "output", "data": f"🔍 Searching in {scan_path}...\n"})
                    
                    # Search for the file by name
                    found_path = None
                    for root, dirs, files_in_dir in os.walk(scan_path):
                        if filename in files_in_dir:
                            found_path = os.path.join(root, filename)
                            break
                    
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
                await broadcast_message({"type": "output", "data": f"{'='*60}\n"})
                
                cmd = ["/usr/local/bin/dovi_convert", "-convert", actual_filepath]
                if safe_mode:
                    cmd.append("-safe")
                if include_simple:
                    cmd.append("-include-simple")
                cmd.append("-y")
                
                # Run command and track result
                success = await run_convert_command(cmd, cwd=str(Path(actual_filepath).parent), 
                                                    file_num=i, total_files=total, filename=filename,
                                                    file_size=file_size)
                
                if success:
                    # Verify conversion by checking for backup file
                    backup_path = actual_filepath + ".bak.dovi_convert"
                    backup_exists = Path(backup_path).exists()
                    
                    if backup_exists:
                        logger.info(f"Backup file verified: {backup_path}")
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
                
                if not success:
                    conversion_results.append({"file": filename, "status": "failed"})
                    state.add_to_history(filename, "failed", log_id)
                    await broadcast_message({"type": "output", "data": f"\n❌ {filename} - CONVERSION FAILED\n"})
            
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


async def run_convert_command(cmd: list, cwd: str = None, file_num: int = 1, total_files: int = 1, filename: str = "", file_size: int = 0):
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
        
        await broadcast_message({"type": "output", "data": f"🔧 Running: {cmd_str}\n\n"})
        
        process = await asyncio.create_subprocess_shell(
            full_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd
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
            nonlocal saw_error, saw_success, current_step, file_percent, last_progress_update, current_step_num, script_elapsed_secs
            
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
                        "filepath": cwd,
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
    
    if state.settings.get("schedule_enabled"):
        logger.info("Scheduled scans enabled - starting scheduler")
        setup_scheduled_scan()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
