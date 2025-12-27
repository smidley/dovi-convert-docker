"""
DoVi Convert Web Interface
A FastAPI application providing a web UI for the dovi_convert script.
"""

import asyncio
import os
import json
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
        self.current_process: Optional[subprocess.Popen] = None
        self.websocket_clients: list[WebSocket] = []
        self.scan_path = MEDIA_PATH
        self.settings = self.load_settings()
    
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
    if state.current_process:
        state.current_process.terminate()
        await broadcast_message({"type": "output", "data": "\n⚠️ Process terminated by user\n"})
        await broadcast_message({"type": "status", "running": False})
        state.is_running = False
        return {"status": "stopped"}
    raise HTTPException(status_code=404, detail="No process running")


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time output streaming."""
    await websocket.accept()
    state.websocket_clients.append(websocket)
    print(f"WebSocket connected. Total clients: {len(state.websocket_clients)}", flush=True)
    
    try:
        # Send initial status
        await websocket.send_json({
            "type": "status",
            "running": state.is_running,
            "settings": state.settings
        })
        
        # Simple message loop - just receive and handle
        while True:
            message = await websocket.receive()
            
            # Check message type
            if message["type"] == "websocket.disconnect":
                break
            elif message["type"] == "websocket.receive":
                data = message.get("text", "")
                if data == "ping":
                    await websocket.send_text("pong")
                    
    except WebSocketDisconnect:
        print("WebSocket client disconnected normally", flush=True)
    except Exception as e:
        print(f"WebSocket error: {type(e).__name__}: {e}", flush=True)
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
    """Run the dovi_convert scan operation with detailed output."""
    state.is_running = True
    scan_path = state.settings.get("scan_path", MEDIA_PATH)
    depth = state.settings.get("scan_depth", 5)
    
    await broadcast_message({
        "type": "output", 
        "data": f"{'='*60}\n"
    })
    await broadcast_message({
        "type": "output", 
        "data": f"🔍 DOLBY VISION SCAN\n"
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
        # First, let's find all MKV files and show progress
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
            await broadcast_message({
                "type": "output", 
                "data": f"   Make sure your media path is correctly mapped.\n\n"
            })
        else:
            await broadcast_message({
                "type": "output", 
                "data": f"📂 Found {len(mkv_files)} MKV file(s)\n\n"
            })
            
            # Show first few files
            for i, f in enumerate(mkv_files[:10]):
                filename = Path(f).name
                await broadcast_message({
                    "type": "output", 
                    "data": f"   • {filename}\n"
                })
            
            if len(mkv_files) > 10:
                await broadcast_message({
                    "type": "output", 
                    "data": f"   ... and {len(mkv_files) - 10} more\n"
                })
            
            await broadcast_message({"type": "output", "data": "\n"})
        
        # Check if dovi_convert exists
        await broadcast_message({"type": "output", "data": "🔧 Checking tools...\n"})
        
        import shutil
        dovi_convert_path = shutil.which("dovi_convert")
        dovi_tool_path = shutil.which("dovi_tool")
        mediainfo_path = shutil.which("mediainfo")
        
        await broadcast_message({
            "type": "output", 
            "data": f"   • dovi_convert: {'✅ Found' if dovi_convert_path else '❌ Not found'}\n"
        })
        await broadcast_message({
            "type": "output", 
            "data": f"   • dovi_tool: {'✅ Found' if dovi_tool_path else '❌ Not found'}\n"
        })
        await broadcast_message({
            "type": "output", 
            "data": f"   • mediainfo: {'✅ Found' if mediainfo_path else '❌ Not found'}\n\n"
        })
        
        if not dovi_convert_path:
            await broadcast_message({
                "type": "output", 
                "data": "⬇️ Downloading dovi_convert script...\n"
            })
            download_cmd = [
                "wget", "-q", 
                "https://raw.githubusercontent.com/cryptochrome/dovi_convert/main/dovi_convert.sh",
                "-O", "/usr/local/bin/dovi_convert"
            ]
            download_proc = await asyncio.create_subprocess_exec(*download_cmd)
            await download_proc.wait()
            
            chmod_proc = await asyncio.create_subprocess_exec("chmod", "+x", "/usr/local/bin/dovi_convert")
            await chmod_proc.wait()
            
            await broadcast_message({"type": "output", "data": "✅ Downloaded dovi_convert\n\n"})
        
        # Now run the actual scan
        if mkv_files:
            await broadcast_message({
                "type": "output", 
                "data": f"{'─'*60}\n"
            })
            await broadcast_message({
                "type": "output", 
                "data": "🎬 Scanning for Dolby Vision Profile 7 files...\n"
            })
            await broadcast_message({
                "type": "output", 
                "data": f"{'─'*60}\n\n"
            })
            
            cmd = ["dovi_convert", "-scan", str(depth)]
            await run_command(cmd, cwd=scan_path)
        
    except FileNotFoundError as e:
        await broadcast_message({"type": "output", "data": f"\n❌ Command not found: {str(e)}\n"})
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


