"""
DoVi Convert Web Interface
A FastAPI application providing a web UI for the dovi_convert script.
"""

import asyncio
import os
import json
import traceback
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
            "scan_depth": 5
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
        # Validate path exists
        if not Path(settings.scan_path).exists():
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


@app.post("/api/scan")
async def start_scan():
    """Start a scan operation."""
    if state.is_running:
        raise HTTPException(status_code=409, detail="A process is already running")
    
    await broadcast_message({"type": "status", "running": True, "action": "scan"})
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
        "data": f"📊 Scan depth: {depth} levels\n\n"
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


