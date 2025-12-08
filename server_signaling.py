# # server_signaling.py
import asyncio
import json
import logging
from typing import Dict, Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pathlib import Path
import uvicorn

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [SIGNALING] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Serve viewer.html
BASE_DIR = Path(__file__).resolve().parent
VIEWER_FILE = BASE_DIR / "viewer.html"

# Global state - track clients and their subscriptions
class ClientState:
    def __init__(self, client_id: str, websocket: WebSocket):
        self.client_id = client_id
        self.websocket = websocket
        self.is_camera = client_id.startswith("camera")
        self.subscribed_cameras: Set[str] = set()
        self.last_offer: str = None
        self.last_answer: str = None

clients: Dict[str, ClientState] = {}
cached_offers: Dict[str, dict] = {}  # Store offers from cameras for late-joining viewers

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """Enhanced signaling with multi-viewer support and connection stability"""
    await websocket.accept()
    
    is_camera = client_id.startswith("camera")
    role = "🎥 Camera" if is_camera else "👁️ Viewer"
    
    logger.info(f"✅ {role} '{client_id}' connected")
    client = ClientState(client_id, websocket)
    clients[client_id] = client
    
    # If this is a viewer, send cached offers from all cameras
    if not is_camera:
        for camera_id, offer_msg in cached_offers.items():
            try:
                await websocket.send_text(json.dumps(offer_msg))
                logger.info(f"📨 Sent cached offer from '{camera_id}' to viewer '{client_id}'")
            except Exception as e:
                logger.error(f"❌ Failed to send cached offer to {client_id}: {e}")
    
    try:
        while True:
            try:
                data = await websocket.receive_text()
                msg = json.loads(data)
            except Exception as e:
                logger.error(f"❌ Receive error from {client_id}: {e}")
                break
            
            msg_type = msg.get("type")
            target = msg.get("to")
            
            # ===== OFFER: Camera sends offer to viewer(s) =====
            if msg_type == "offer":
                if is_camera:
                    # Cache offer for late-joining viewers
                    client.last_offer = msg.get("sdp")
                    cached_offers[client_id] = msg  # Store the full message
                    logger.info(f"📨 Camera '{client_id}' sent offer (cached)")
                    
                    # Always broadcast to ALL connected viewers (ignore 'to' field for offers)
                    viewer_count = 0
                    for viewer_id, viewer in clients.items():
                        if not viewer.is_camera:
                            try:
                                await viewer.websocket.send_text(data)
                                viewer_count += 1
                            except Exception as e:
                                logger.error(f"❌ Failed to send offer to {viewer_id}: {e}")
                    logger.info(f"📤 Broadcast offer to {viewer_count} viewer(s)")
            
            # ===== ANSWER: Viewer sends answer back to camera =====
            elif msg_type == "answer":
                if not is_camera:
                    # Store answer
                    client.last_answer = msg.get("sdp")
                    logger.info(f"📨 Viewer '{client_id}' sent answer")
                    
                    # Forward to camera
                    if target and target in clients:
                        try:
                            await clients[target].websocket.send_text(data)
                            logger.info(f"📤 Forwarded answer to camera '{target}'")
                        except Exception as e:
                            logger.error(f"❌ Failed to forward answer: {e}")
            
            # ===== ICE: Forward ICE candidates =====
            elif msg_type == "ice":
                if is_camera:
                    # Camera broadcasts ICE candidates to all viewers
                    for viewer_id, viewer in clients.items():
                        if not viewer.is_camera:
                            try:
                                await viewer.websocket.send_text(data)
                            except Exception as e:
                                logger.error(f"❌ Failed to send ICE to {viewer_id}: {e}")
                elif target and target in clients:
                    # Viewer sends ICE to specific camera
                    try:
                        await clients[target].websocket.send_text(data)
                    except Exception as e:
                        logger.error(f"❌ Failed to forward ICE to {target}: {e}")
            
            # ===== ICE-COMPLETE: Signal ICE gathering done =====
            elif msg_type == "ice-complete":
                if is_camera:
                    # Camera broadcasts ICE-complete to all viewers
                    for viewer_id, viewer in clients.items():
                        if not viewer.is_camera:
                            try:
                                await viewer.websocket.send_text(data)
                            except Exception as e:
                                logger.error(f"❌ Failed to send ICE-complete to {viewer_id}: {e}")
                elif target and target in clients:
                    # Viewer sends ICE-complete to camera
                    try:
                        await clients[target].websocket.send_text(data)
                    except Exception as e:
                        logger.error(f"❌ Failed to forward ICE-complete to {target}: {e}")
            
            # ===== PING: Keep-alive heartbeat =====
            elif msg_type == "ping":
                try:
                    pong_msg = {"type": "pong", "from": "signaling"}
                    await websocket.send_text(json.dumps(pong_msg))
                except Exception as e:
                    logger.error(f"❌ Ping/pong error: {e}")
            
            else:
                logger.debug(f"Unknown message type: {msg_type}")
    
    except WebSocketDisconnect:
        logger.info(f"🔌 {role} '{client_id}' disconnected")
    except Exception as e:
        logger.error(f"❌ Error in {client_id}: {e}")
    
    finally:
        if client_id in clients:
            del clients[client_id]
        logger.info(f"📊 Active clients: {len(clients)}")

@app.get("/")
async def root():
    return {"message":"Signaling server running"}

@app.get("/viewer", response_class=HTMLResponse)
async def serve_viewer():
    if not VIEWER_FILE.exists():
        return HTMLResponse("viewer.html not found", status_code=404)
    return VIEWER_FILE.read_text(encoding="utf-8")

if __name__ == "__main__":
    uvicorn.run("server_signaling:app", host="0.0.0.0", port=8000, log_level="info")

# ============================================================================================