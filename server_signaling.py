# server_signaling.py
# Simple WebSocket signaling server for coordinating P2P connections

import json
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pathlib import Path
import uvicorn

# Setup paths
BASE_DIR = Path(__file__).resolve().parent
VIEWER_FILE = BASE_DIR / "viewer.html"

# Create FastAPI app
app = FastAPI(title="STUN P2P Signaling Server")

# Enable CORS for browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Track connected clients
clients: Dict[str, WebSocket] = {}


@app.get("/")
async def root():
    """Health check endpoint"""
    return {
        "message": "STUN-Only P2P Signaling Server",
        "status": "running",
        "connected_clients": len(clients),
        "clients": list(clients.keys())
    }


@app.get("/viewer", response_class=HTMLResponse)
async def serve_viewer():
    """Serve the viewer HTML page"""
    if not VIEWER_FILE.exists():
        return HTMLResponse("viewer.html not found", status_code=404)
    return VIEWER_FILE.read_text(encoding="utf-8")


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """
    WebSocket endpoint for signaling
    Accepts connections from both camera pusher and browser viewer
    Forwards messages between them
    """
    await websocket.accept()
    print(f"✅ [signaling] Client connected: {client_id}")
    print(f"📊 [signaling] Total clients: {len(clients) + 1}")
    
    clients[client_id] = websocket
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            
            try:
                obj = json.loads(data)
                msg_type = obj.get("type")
                from_id = obj.get("from")
                to_id = obj.get("to")
                
                print(f"📨 [signaling] {msg_type} from {from_id} → {to_id}")
                
                # Forward message to target client
                if to_id and to_id in clients:
                    await clients[to_id].send_text(data)
                    print(f"✅ [signaling] Forwarded to {to_id}")
                else:
                    print(f"⚠️ [signaling] Target {to_id} not connected yet")
                    
            except json.JSONDecodeError as e:
                print(f"❌ [signaling] Invalid JSON: {e}")
            except Exception as e:
                print(f"❌ [signaling] Error forwarding: {e}")
    
    except WebSocketDisconnect:
        print(f"👋 [signaling] Client disconnected: {client_id}")
    
    finally:
        # Clean up disconnected client
        if client_id in clients:
            del clients[client_id]
        print(f"📊 [signaling] Total clients: {len(clients)}")


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 Starting STUN-Only P2P Signaling Server")
    print("=" * 60)
    print("📍 URL: http://0.0.0.0:8000")
    print("🌐 Viewer: http://0.0.0.0:8000/viewer")
    print("🔌 WebSocket: ws://0.0.0.0:8000/ws/{client_id}")
    print("=" * 60)
    print()
    
    uvicorn.run(
        "server_signaling:app",
        host="0.0.0.0",
        port=8000,
        log_level="info"
    )
