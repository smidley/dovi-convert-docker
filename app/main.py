"""
DoVi Convert Web Interface
A FastAPI application providing a web UI for the dovi_convert script.
"""

import asyncio
import os
import json
import traceback
import aiohttp
from pathlib import Path
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import subprocess

app = FastAPI(title="DoVi Convert", version="1.0.0")

# Mount static files and templates
app.mount("/static", StaticFiles(directory="/app/static"), name="static")
templates = Jinja2Templates(directory="/app/templates")

# Configuration
MEDIA_PATH = os.environ.get("MEDIA_PATH", "/media")
CONFIG_PATH = os.environ.get("CONFIG_PATH", "/config")

# State management
class AppState:
    def __init__(self):
        self.is_running = False
        self.scan_cancelled = False
        self.current_process = None
        self.websocket_clients: list[WebSocket] = []
        self.scan_path = MEDIA_PATH
        self.settings = self.load_settings()
        self.scan_results = []
    
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
            "use_jellyfin": False
        }
    
    def save_settings(self):
        settings_file = Path(CONFIG_PATH) / "settings.json"
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        with open(settings_file, "w") as f:
            json.dump(self.settings, f, indent=2)

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


@app.get("/")
async def index(request: Request):
    """Serve the main web interface."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "media_path": MEDIA_PATH
    })


@app.get("/api/status")
async def get_status():
    """Get current application status."""
    return {
        "is_running": state.is_running,
        "settings": state.settings,
        "media_path": MEDIA_PATH
    }


@app.get("/api/status")
async def get_status():
    """Get current running status for page refresh recovery."""
    return {
        "is_running": state.is_running,
        "action": "scan" if state.is_running else None,
        "websocket_clients": len(state.websocket_clients)
    }


@app.get("/api/debug")
async def debug_info():
    """Debug endpoint to check system status."""
    import shutil
    
    # Check if commands exist
    dovi_convert_path = shutil.which("dovi_convert")
    dovi_tool_path = shutil.which("dovi_tool")
    ffmpeg_path = shutil.which("ffmpeg")
    
    # Check media path
    media_exists = Path(MEDIA_PATH).exists()
    media_contents = []
    if media_exists:
        try:
            media_contents = [str(p.name) for p in Path(MEDIA_PATH).iterdir()][:10]
        except:
            pass
    
    return {
        "dovi_convert": dovi_convert_path,
        "dovi_tool": dovi_tool_path,
        "ffmpeg": ffmpeg_path,
        "media_path": MEDIA_PATH,
        "media_exists": media_exists,
        "media_contents": media_contents,
        "config_path": CONFIG_PATH,
        "settings": state.settings,
        "websocket_clients": len(state.websocket_clients),
        "is_running": state.is_running
    }


@app.get("/api/settings")
async def get_settings():
    """Get current settings."""
    return state.settings


@app.post("/api/settings")
async def update_settings(settings: SettingsUpdate):
    """Update application settings."""
    if settings.scan_path is not None:
        # Validate path exists (only if not using Jellyfin)
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
    
    state.save_settings()
    return state.settings


@app.get("/api/browse")
async def browse_directory(path: str = "/"):
    """Browse directories for path selection."""
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
    """Test Jellyfin connection."""
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
async def start_scan():
    """Start a scan operation."""
    if state.is_running:
        raise HTTPException(status_code=409, detail="A process is already running")
    
    await broadcast_message({"type": "status", "running": True, "action": "scan"})
    
    # Use Jellyfin if enabled
    if state.settings.get("use_jellyfin"):
        jellyfin_url = state.settings.get("jellyfin_url", "")
        jellyfin_key = state.settings.get("jellyfin_api_key", "")
        
        if not jellyfin_url or not jellyfin_key:
            await broadcast_message({"type": "output", "data": "❌ Jellyfin URL and API key are required\n"})
            await broadcast_message({"type": "status", "running": False})
            return {"status": "error", "message": "Jellyfin not configured"}
        
        asyncio.create_task(run_jellyfin_scan())
    else:
        asyncio.create_task(run_scan())
    
    return {"status": "started", "action": "scan"}


@app.post("/api/convert")
async def start_convert():
    """Start a batch conversion."""
    if state.is_running:
        raise HTTPException(status_code=409, detail="A process is already running")
    
    await broadcast_message({"type": "status", "running": True, "action": "convert"})
    asyncio.create_task(run_convert())
    return {"status": "started", "action": "convert"}


@app.post("/api/stop")
async def stop_process():
    """Stop the current running process."""
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
    """WebSocket endpoint for real-time output streaming."""
    try:
        await websocket.accept()
    except Exception as e:
        print(f"WebSocket accept failed: {e}", flush=True)
        return
        
    state.websocket_clients.append(websocket)
    print(f"WebSocket connected. Total clients: {len(state.websocket_clients)}", flush=True)
    
    try:
        # Send initial status
        await websocket.send_json({
            "type": "status",
            "running": state.is_running,
            "settings": state.settings
        })
        
        # Keep connection alive - just wait for disconnect
        # We don't need to receive messages, just keep the connection open
        while True:
            try:
                # Use receive with a long timeout
                data = await asyncio.wait_for(websocket.receive_text(), timeout=60.0)
                # Handle ping from client
                if data == "ping":
                    await websocket.send_text("pong")
            except asyncio.TimeoutError:
                # Send keepalive ping to client
                try:
                    await websocket.send_json({"type": "keepalive"})
                except Exception:
                    break
                    
    except WebSocketDisconnect:
        print("WebSocket client disconnected normally", flush=True)
    except Exception as e:
        print(f"WebSocket error: {type(e).__name__}: {e}", flush=True)
        traceback.print_exc()
    finally:
        if websocket in state.websocket_clients:
            state.websocket_clients.remove(websocket)
        print(f"WebSocket disconnected. Total clients: {len(state.websocket_clients)}", flush=True)


async def broadcast_message(message: dict):
    """Broadcast a message to all connected WebSocket clients."""
    if not state.websocket_clients:
        print(f"No WebSocket clients to broadcast to. Message: {message.get('type', 'unknown')}", flush=True)
        return
        
    print(f"Broadcasting to {len(state.websocket_clients)} client(s): {message.get('type', 'unknown')}", flush=True)
    
    disconnected = []
    for client in state.websocket_clients:
        try:
            await client.send_json(message)
        except Exception as e:
            print(f"Failed to send to client: {e}", flush=True)
            disconnected.append(client)
    
    for client in disconnected:
        if client in state.websocket_clients:
            state.websocket_clients.remove(client)


async def run_jellyfin_scan():
    """Scan Jellyfin library for Dolby Vision files - instant metadata access."""
    state.is_running = True
    state.scan_cancelled = False
    
    url = state.settings.get("jellyfin_url", "")
    api_key = state.settings.get("jellyfin_api_key", "")
    
    await broadcast_message({
        "type": "output",
        "data": f"{'='*60}\n"
    })
    await broadcast_message({
        "type": "output",
        "data": "🔍 JELLYFIN LIBRARY SCAN (Instant)\n"
    })
    await broadcast_message({
        "type": "output",
        "data": f"{'='*60}\n\n"
    })
    await broadcast_message({
        "type": "output",
        "data": f"🌐 Server: {url}\n\n"
    })
    
    dv_profile7_files = []
    dv_profile8_files = []
    hdr10_files = []
    sdr_count = 0
    
    try:
        async with aiohttp.ClientSession() as session:
            headers = {"X-Emby-Token": api_key}
            
            # Build item types filter based on settings
            include_movies = state.settings.get("include_movies", True)
            include_tv = state.settings.get("include_tv_shows", True)
            
            item_types = []
            if include_movies:
                item_types.append("Movie")
            if include_tv:
                item_types.append("Episode")
            
            if not item_types:
                await broadcast_message({
                    "type": "output",
                    "data": "❌ No content types selected. Enable Movies or TV Shows in settings.\n"
                })
                state.is_running = False
                await broadcast_message({"type": "status", "running": False})
                return
            
            type_str = ",".join(item_types)
            type_display = " and ".join(["Movies" if t == "Movie" else "TV Shows" for t in item_types])
            
            await broadcast_message({"type": "output", "data": f"📡 Fetching {type_display} from Jellyfin...\n"})
            
            params = {
                "IncludeItemTypes": type_str,
                "Recursive": "true",
                "Fields": "MediaStreams,Path",
                "Limit": "10000"
            }
            
            async with session.get(f"{url}/Items", headers=headers, params=params) as resp:
                if resp.status != 200:
                    await broadcast_message({
                        "type": "output",
                        "data": f"❌ Failed to fetch items: HTTP {resp.status}\n"
                    })
                    state.is_running = False
                    await broadcast_message({"type": "status", "running": False})
                    return
                
                data = await resp.json()
                items = data.get("Items", [])
            
            total_items = len(items)
            await broadcast_message({
                "type": "output",
                "data": f"📂 Found {total_items} items in library\n\n"
            })
            
            await broadcast_message({
                "type": "output",
                "data": f"{'─'*60}\n"
            })
            await broadcast_message({
                "type": "output",
                "data": "🎬 Analyzing HDR formats...\n"
            })
            await broadcast_message({
                "type": "output",
                "data": f"{'─'*60}\n\n"
            })
            
            for i, item in enumerate(items, 1):
                if state.scan_cancelled:
                    await broadcast_message({
                        "type": "output",
                        "data": "\n⚠️ Scan cancelled by user\n"
                    })
                    break
                
                # Send progress
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
                
                # Check video streams for HDR info
                media_streams = item.get("MediaStreams", [])
                video_stream = next((s for s in media_streams if s.get("Type") == "Video"), None)
                
                if video_stream:
                    video_range = video_stream.get("VideoRange", "")
                    video_range_type = video_stream.get("VideoRangeType", "")
                    hdr_format = video_stream.get("VideoDoViTitle", "") or video_stream.get("Title", "")
                    
                    file_path = item.get("Path", "")
                    file_name = item.get("Name", "Unknown")
                    item_type = item.get("Type", "Unknown")
                    
                    # Check for Dolby Vision
                    is_dv = "DoVi" in video_range_type or "Dolby Vision" in str(hdr_format) or video_stream.get("VideoDoViTitle")
                    
                    if is_dv:
                        # Check profile
                        dovi_title = video_stream.get("VideoDoViTitle", "") or hdr_format
                        
                        if "7" in str(dovi_title) or "dvhe.07" in str(dovi_title).lower():
                            dv_profile7_files.append({
                                "path": file_path,
                                "name": file_name,
                                "hdr": f"Dolby Vision Profile 7",
                                "profile": dovi_title,
                                "action": "Convert to Profile 8.1",
                                "type": item_type,
                                "jellyfin_id": item.get("Id")
                            })
                        else:
                            dv_profile8_files.append({
                                "path": file_path,
                                "name": file_name,
                                "hdr": f"Dolby Vision Profile 8",
                                "profile": dovi_title,
                                "action": "Already compatible",
                                "type": item_type,
                                "jellyfin_id": item.get("Id")
                            })
                    elif "HDR" in video_range or "HDR10" in video_range_type:
                        hdr10_files.append({
                            "path": file_path,
                            "name": file_name,
                            "hdr": "HDR10",
                            "type": item_type
                        })
                    else:
                        sdr_count += 1
            
            # Show results
            await broadcast_message({
                "type": "output",
                "data": f"\n{'='*60}\n"
            })
            await broadcast_message({
                "type": "output",
                "data": "📊 SCAN RESULTS\n"
            })
            await broadcast_message({
                "type": "output",
                "data": f"{'='*60}\n\n"
            })
            
            await broadcast_message({
                "type": "output",
                "data": f"🎯 DV Profile 7 (need conversion): {len(dv_profile7_files)}\n"
            })
            await broadcast_message({
                "type": "output",
                "data": f"✅ DV Profile 8 (compatible):       {len(dv_profile8_files)}\n"
            })
            await broadcast_message({
                "type": "output",
                "data": f"🔶 HDR10:                           {len(hdr10_files)}\n"
            })
            await broadcast_message({
                "type": "output",
                "data": f"⚪ SDR:                             {sdr_count}\n\n"
            })
            
            # Show Profile 7 files
            if dv_profile7_files:
                await broadcast_message({
                    "type": "output",
                    "data": f"{'─'*60}\n"
                })
                await broadcast_message({
                    "type": "output",
                    "data": "🎯 FILES NEEDING CONVERSION:\n"
                })
                await broadcast_message({
                    "type": "output",
                    "data": f"{'─'*60}\n\n"
                })
                
                for f in dv_profile7_files:
                    await broadcast_message({
                        "type": "output",
                        "data": f"  📄 {f['name']}\n"
                    })
                    await broadcast_message({
                        "type": "output",
                        "data": f"     {f['hdr']} ({f['profile']})\n"
                    })
                    await broadcast_message({
                        "type": "output",
                        "data": f"     📁 {f['path']}\n\n"
                    })
            else:
                await broadcast_message({
                    "type": "output",
                    "data": "✅ No files need conversion!\n\n"
                })
            
            # Send results data for UI
            await broadcast_message({
                "type": "results",
                "data": {
                    "profile7": dv_profile7_files,
                    "profile8": dv_profile8_files,
                    "hdr10": hdr10_files,
                    "hdr10_count": len(hdr10_files),
                    "sdr_count": sdr_count,
                    "source": "jellyfin"
                }
            })
            
            await broadcast_message({
                "type": "output",
                "data": f"{'='*60}\n"
            })
            await broadcast_message({
                "type": "output",
                "data": "✅ Jellyfin scan complete\n"
            })
            await broadcast_message({
                "type": "output",
                "data": f"{'='*60}\n"
            })
            
    except aiohttp.ClientError as e:
        await broadcast_message({
            "type": "output",
            "data": f"\n❌ Connection error: {str(e)}\n"
        })
    except Exception as e:
        await broadcast_message({
            "type": "output",
            "data": f"\n❌ Error: {type(e).__name__}: {str(e)}\n"
        })
        traceback.print_exc()
    finally:
        state.is_running = False
        await broadcast_message({"type": "status", "running": False})


async def run_scan():
    """Run fast Dolby Vision scan using mediainfo."""
    state.is_running = True
    state.scan_cancelled = False
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    depth = state.settings.get("scan_depth", 5)
    
    await broadcast_message({
        "type": "output", 
        "data": f"{'='*60}\n"
    })
    await broadcast_message({
        "type": "output", 
        "data": f"🔍 DOLBY VISION SCAN (Fast Mode)\n"
    })
    await broadcast_message({
        "type": "output", 
        "data": f"{'='*60}\n\n"
    })
    await broadcast_message({
        "type": "output", 
        "data": f"📁 Scan path: {scan_path}\n"
    })
    await broadcast_message({
        "type": "output", 
        "data": f"📊 Scan depth: {depth} levels\n"
    })
    
    # Show content type info
    include_movies = state.settings.get("include_movies", True)
    include_tv = state.settings.get("include_tv_shows", True)
    content_types = []
    if include_movies:
        content_types.append("Movies")
    if include_tv:
        content_types.append("TV Shows")
    await broadcast_message({
        "type": "output", 
        "data": f"📺 Content types: {', '.join(content_types) if content_types else 'None'}\n"
    })
    await broadcast_message({
        "type": "output", 
        "data": f"   (File system scan includes all media files)\n\n"
    })
    
    try:
        # Find all MKV files
        await broadcast_message({"type": "output", "data": "🔎 Searching for MKV files...\n\n"})
        
        find_cmd = ["find", scan_path, "-maxdepth", str(depth), "-type", "f", "-name", "*.mkv"]
        find_proc = await asyncio.create_subprocess_exec(
            *find_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await find_proc.communicate()
        
        mkv_files = [f for f in stdout.decode().strip().split('\n') if f]
        
        if not mkv_files:
            await broadcast_message({
                "type": "output", 
                "data": f"⚠️ No MKV files found in {scan_path}\n"
            })
        else:
            await broadcast_message({
                "type": "output", 
                "data": f"📂 Found {len(mkv_files)} MKV file(s)\n\n"
            })
            
            await broadcast_message({
                "type": "output", 
                "data": f"{'─'*60}\n"
            })
            await broadcast_message({
                "type": "output", 
                "data": "🎬 Scanning for Dolby Vision using mediainfo (fast)...\n"
            })
            await broadcast_message({
                "type": "output", 
                "data": f"{'─'*60}\n\n"
            })
            
            # Results storage
            dv_profile7_files = []
            dv_profile8_files = []
            hdr10_files = []
            sdr_files = []
            
            for i, filepath in enumerate(mkv_files, 1):
                # Check if cancelled
                if state.scan_cancelled:
                    await broadcast_message({
                        "type": "output", 
                        "data": "\n⚠️ Scan cancelled by user\n"
                    })
                    await broadcast_message({
                        "type": "progress",
                        "data": {"current": i, "total": len(mkv_files), "status": "cancelled"}
                    })
                    break
                
                filename = Path(filepath).name
                
                # Send progress update
                await broadcast_message({
                    "type": "progress",
                    "data": {
                        "current": i,
                        "total": len(mkv_files),
                        "percent": round((i / len(mkv_files)) * 100),
                        "filename": filename,
                        "status": "scanning"
                    }
                })
                
                # Show in log every 100 files or for small counts
                if i % 100 == 0 or i <= 3 or len(mkv_files) < 50:
                    await broadcast_message({
                        "type": "output", 
                        "data": f"[{i}/{len(mkv_files)}] {filename[:50]}...\n"
                    })
                
                try:
                    # Use mediainfo to get HDR format quickly
                    proc = await asyncio.create_subprocess_exec(
                        "mediainfo", "--Output=Video;%HDR_Format%\\n%HDR_Format_Profile%",
                        filepath,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, _ = await proc.communicate()
                    hdr_info = stdout.decode().strip()
                    
                    if "Dolby Vision" in hdr_info:
                        if "dvhe.07" in hdr_info or "Profile 7" in hdr_info.replace(" ", ""):
                            dv_profile7_files.append({
                                "path": filepath,
                                "name": filename,
                                "hdr": hdr_info.replace('\n', ' '),
                                "action": "Convert to Profile 8.1"
                            })
                        else:
                            dv_profile8_files.append({
                                "path": filepath,
                                "name": filename,
                                "hdr": hdr_info.replace('\n', ' '),
                                "action": "Already compatible"
                            })
                    elif "HDR10" in hdr_info or "SMPTE ST 2086" in hdr_info:
                        hdr10_files.append(filepath)
                    else:
                        sdr_files.append(filepath)
                        
                except Exception as e:
                    pass  # Skip files that can't be read
            
            # Send results to frontend
            await broadcast_message({
                "type": "output", 
                "data": f"\n{'='*60}\n"
            })
            await broadcast_message({
                "type": "output", 
                "data": "📊 SCAN RESULTS\n"
            })
            await broadcast_message({
                "type": "output", 
                "data": f"{'='*60}\n\n"
            })
            
            await broadcast_message({
                "type": "output", 
                "data": f"🎯 DV Profile 7 (need conversion): {len(dv_profile7_files)}\n"
            })
            await broadcast_message({
                "type": "output", 
                "data": f"✅ DV Profile 8 (compatible):       {len(dv_profile8_files)}\n"
            })
            await broadcast_message({
                "type": "output", 
                "data": f"🔶 HDR10:                           {len(hdr10_files)}\n"
            })
            await broadcast_message({
                "type": "output", 
                "data": f"⚪ SDR:                             {len(sdr_files)}\n\n"
            })
            
            # Show Profile 7 files that need conversion
            if dv_profile7_files:
                await broadcast_message({
                    "type": "output", 
                    "data": f"{'─'*60}\n"
                })
                await broadcast_message({
                    "type": "output", 
                    "data": "🎯 FILES NEEDING CONVERSION:\n"
                })
                await broadcast_message({
                    "type": "output", 
                    "data": f"{'─'*60}\n\n"
                })
                
                for f in dv_profile7_files:
                    await broadcast_message({
                        "type": "output", 
                        "data": f"  📄 {f['name']}\n"
                    })
                    await broadcast_message({
                        "type": "output", 
                        "data": f"     HDR: {f['hdr']}\n\n"
                    })
            
            # Send results data for the results pane
            await broadcast_message({
                "type": "results",
                "data": {
                    "profile7": dv_profile7_files,
                    "profile8": dv_profile8_files,
                    "hdr10_count": len(hdr10_files),
                    "sdr_count": len(sdr_files)
                }
            })
        
    except Exception as e:
        await broadcast_message({"type": "output", "data": f"\n❌ Error: {type(e).__name__}: {str(e)}\n"})
    finally:
        await broadcast_message({
            "type": "output", 
            "data": f"\n{'='*60}\n"
        })
        await broadcast_message({
            "type": "output", 
            "data": "✅ Scan complete\n"
        })
        await broadcast_message({
            "type": "output", 
            "data": f"{'='*60}\n"
        })
        state.is_running = False
        state.scan_cancelled = False
        state.current_process = None
        await broadcast_message({"type": "status", "running": False})


async def run_convert():
    """Run the dovi_convert batch conversion."""
    state.is_running = True
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    depth = state.settings.get("scan_depth", 5)
    safe_mode = state.settings.get("safe_mode", False)
    include_simple = state.settings.get("include_simple_fel", False)
    
    await broadcast_message({
        "type": "output",
        "data": f"🎬 Starting batch conversion in: {scan_path}\n"
    })
    
    try:
        cmd = ["dovi_convert", "-batch", str(depth), "-y"]
        
        if safe_mode:
            cmd.append("-safe")
        
        if include_simple:
            cmd.append("-include-simple")
        
        await broadcast_message({"type": "output", "data": f"Running: {' '.join(cmd)}\n\n"})
        await run_command(cmd, cwd=scan_path)
        
        # Auto cleanup if enabled
        if state.settings.get("auto_cleanup", False):
            await broadcast_message({"type": "output", "data": "\n🧹 Running cleanup...\n"})
            cleanup_cmd = ["dovi_convert", "-cleanup", "-r"]
            await run_command(cleanup_cmd, cwd=scan_path)
    except FileNotFoundError as e:
        await broadcast_message({"type": "output", "data": f"\n❌ Command not found: {str(e)}\n"})
    except Exception as e:
        await broadcast_message({"type": "output", "data": f"\n❌ Error: {type(e).__name__}: {str(e)}\n"})
    finally:
        state.is_running = False
        state.current_process = None
        await broadcast_message({"type": "status", "running": False})


async def run_command(cmd: list, cwd: str = None):
    """Run a command and stream output via WebSocket."""
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd
        )
        
        state.current_process = process
        
        output_received = False
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            output_received = True
            text = line.decode('utf-8', errors='replace')
            await broadcast_message({"type": "output", "data": text})
        
        await process.wait()
        
        if not output_received:
            await broadcast_message({
                "type": "output", 
                "data": "(No output received from command)\n"
            })
        
        if process.returncode == 0:
            await broadcast_message({"type": "output", "data": "\n✅ Process completed successfully\n"})
        else:
            await broadcast_message({
                "type": "output", 
                "data": f"\n⚠️ Process exited with code {process.returncode}\n"
            })
    except FileNotFoundError:
        await broadcast_message({
            "type": "output", 
            "data": f"❌ Command not found: {cmd[0]}\n"
        })
    except Exception as e:
        await broadcast_message({
            "type": "output", 
            "data": f"❌ Error running command: {type(e).__name__}: {str(e)}\n"
        })


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)


