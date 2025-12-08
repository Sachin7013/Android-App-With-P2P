# # server_signaling.py
# import asyncio
# from typing import Dict
# from fastapi import FastAPI, WebSocket, WebSocketDisconnect
# import uvicorn
# from fastapi.middleware.cors import CORSMiddleware
# from fastapi.responses import HTMLResponse
# from pathlib import Path

# BASE_DIR = Path(__file__).resolve().parent
# VIEWER_FILE = BASE_DIR / "viewer.html"


# app = FastAPI()
# app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# # Keep track of connected websockets by client id
# clients: Dict[str, WebSocket] = {}

# @app.websocket("/ws/{client_id}")
# async def websocket_endpoint(websocket: WebSocket, client_id: str):
#     await websocket.accept()
#     print(f"[signaling] {client_id} connected")
#     clients[client_id] = websocket
#     try:
#         while True:
#             data = await websocket.receive_text()
#             # data is JSON string from either side; forward based on 'to' field
#             # Expect JSON like: { type: "offer"/"answer"/"ice", from: "camera1", to: "viewer123", sdp: "...", candidate: {...}}
#             print(f"[signaling] recv from {client_id}: {data[:200]}")
#             # parse and forward
#             import json
#             try:
#                 obj = json.loads(data)
#                 target = obj.get("to")
#                 if target and target in clients:
#                     await clients[target].send_text(data)
#                     print(f"[signaling] forwarded from {obj.get('from')} to {target}")
#                 else:
#                     print(f"[signaling] target {target} not connected yet — ignoring")
#             except Exception as exc:
#                 print("JSON/forward error:", exc)
#     except WebSocketDisconnect:
#         print(f"[signaling] {client_id} disconnected")
#     finally:
#         if client_id in clients:
#             del clients[client_id]

# @app.get("/")
# async def root():
#     return {"message":"Signaling server running"}


# @app.get("/viewer", response_class=HTMLResponse)
# async def serve_viewer():
#     if not VIEWER_FILE.exists():
#         return HTMLResponse("viewer.html not found", status_code=404)
#     return VIEWER_FILE.read_text(encoding="utf-8")

# if __name__ == "__main__":
#     uvicorn.run("server_signaling:app", host="0.0.0.0", port=8000, log_level="info")

# ============================================================================================

import asyncio
import json
import logging
import os
from typing import Dict, Set
from dataclasses import dataclass, field
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pathlib import Path
import uvicorn
from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer
from aiortc.contrib.signaling import candidate_from_sdp
from dotenv import load_dotenv

load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] [SFU] %(levelname)s: %(message)s'
)
logger = logging.getLogger(__name__)

# FastAPI app
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Configuration from .env
TURN_IP = os.getenv("AWS_TURN_IP")
TURN_PORT = os.getenv("AWS_TURN_PORT")
TURN_USER = os.getenv("AWS_TURN_USER")
TURN_PASS = os.getenv("AWS_TURN_PASS")

# ICE Servers - TURN FIRST for priority
ICE_SERVERS = [
    RTCIceServer(
        urls=f"turn:{TURN_IP}:{TURN_PORT}?transport=udp",
        username=TURN_USER,
        credential=TURN_PASS
    ),
    RTCIceServer(
        urls=f"turn:{TURN_IP}:{TURN_PORT}?transport=tcp",
        username=TURN_USER,
        credential=TURN_PASS
    ),
    RTCIceServer(urls="stun:stun.l.google.com:19302"),
    RTCIceServer(urls="stun:stun1.l.google.com:19302"),
]

logger.info(f"✅ TURN configured: {TURN_IP}:{TURN_PORT}")

# Data models
@dataclass
class ClientPeer:
    client_id: str
    websocket: WebSocket
    pc: RTCPeerConnection = None
    is_camera: bool = False
    subscribed_cameras: Set[str] = field(default_factory=set)

# Global state
clients: Dict[str, ClientPeer] = {}
camera_pcs: Dict[str, RTCPeerConnection] = {}


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    """Main WebSocket endpoint for cameras and viewers"""
    await websocket.accept()
    
    is_camera = client_id.startswith("camera")
    role = "🎥 Camera" if is_camera else "👁️ Viewer"
    
    logger.info(f"✅ {role} '{client_id}' connected")
    
    # Create peer connection
    config = RTCConfiguration(iceServers=ICE_SERVERS)
    pc = RTCPeerConnection(configuration=config)
    
    client = ClientPeer(
        client_id=client_id,
        websocket=websocket,
        pc=pc,
        is_camera=is_camera
    )
    
    clients[client_id] = client
    
    try:
        # ============ ICE Candidate Handler ============
        @pc.on("icecandidate")
        async def on_ice(candidate):
            try:
                if candidate is None:
                    await websocket.send_text(json.dumps({"type": "ice-complete", "from": "sfu"}))
                    logger.info(f"✅ ICE complete for {client_id}")
                    return
                
                # Only send TURN relay candidates
                if "relay" in candidate.to_sdp():
                    msg = {
                        "type": "ice",
                        "from": "sfu",
                        "candidate": {
                            "candidate": candidate.to_sdp(),
                            "sdpMid": candidate.sdpMid,
                            "sdpMLineIndex": candidate.sdpMLineIndex
                        }
                    }
                    await websocket.send_text(json.dumps(msg))
            except Exception as e:
                logger.error(f"❌ ICE error: {e}")
        
        # ============ Connection State Handler ============
        @pc.on("connectionstatechange")
        def on_conn_state():
            state = pc.connectionState
            logger.info(f"🔗 {client_id} state: {state}")
        
        # ============ Remote Track Handler (for cameras) ============
        @pc.on("track")
        async def on_track(track):
            """When camera sends video track"""
            logger.info(f"📹 {client_id} sending track: {track.kind}")
            
            if is_camera:
                await forward_track_to_viewers(client_id, track)
        
        # ============ Message Handler ============
        while True:
            try:
                raw = await websocket.receive_text()
                msg = json.loads(raw)
            except Exception as e:
                logger.error(f"❌ Receive error: {e}")
                break
            
            msg_type = msg.get("type")
            
            if msg_type == "offer":
                await handle_offer(client_id, pc, msg, websocket, is_camera)
            
            elif msg_type == "answer":
                try:
                    answer = RTCSessionDescription(sdp=msg.get("sdp"), type="answer")
                    await pc.setRemoteDescription(answer)
                    logger.info(f"✅ Answer set for {client_id}")
                except Exception as e:
                    logger.error(f"❌ Answer error: {e}")
            
            elif msg_type == "ice":
                try:
                    cand_data = msg.get("candidate", {})
                    if cand_data.get("candidate"):
                        cand = candidate_from_sdp(cand_data["candidate"])
                        cand.sdpMid = cand_data.get("sdpMid")
                        cand.sdpMLineIndex = cand_data.get("sdpMLineIndex")
                        await pc.addIceCandidate(cand)
                except Exception as e:
                    logger.error(f"⚠️ ICE add error: {e}")
            
            elif msg_type == "subscribe":
                cameras = msg.get("cameras", [])
                client.subscribed_cameras.update(cameras)
                logger.info(f"📺 {client_id} subscribed to: {cameras}")
    
    except WebSocketDisconnect:
        logger.info(f"🔌 {client_id} disconnected")
    except Exception as e:
        logger.error(f"❌ Error: {e}")
    
    finally:
        await pc.close()
        if client_id in clients:
            del clients[client_id]
        if is_camera and client_id in camera_pcs:
            del camera_pcs[client_id]
        
        logger.info(f"📊 Active clients: {len(clients)}")


async def handle_offer(client_id: str, pc: RTCPeerConnection, msg: dict, ws: WebSocket, is_camera: bool):
    """Handle SDP offer from client"""
    try:
        sdp = msg.get("sdp")
        if not sdp:
            logger.error(f"❌ No SDP in offer from {client_id}")
            return
        
        offer = RTCSessionDescription(sdp=sdp, type="offer")
        await pc.setRemoteDescription(offer)
        
        if is_camera:
            camera_pcs[client_id] = pc
            logger.info(f"📥 Camera {client_id} offer received")
        else:
            logger.info(f"📥 Viewer {client_id} offer received")
        
        # Create answer
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        
        response = {
            "type": "answer",
            "from": "sfu",
            "sdp": pc.localDescription.sdp
        }
        
        await ws.send_text(json.dumps(response))
        logger.info(f"✅ Answer sent to {client_id}")
    
    except Exception as e:
        logger.error(f"❌ Offer handling error: {e}")


async def forward_track_to_viewers(camera_id: str, track):
    """Forward camera track to all connected viewers"""
    logger.info(f"🔄 Forwarding {camera_id} track to viewers...")
    
    forwarded_count = 0
    
    for viewer_id, viewer in clients.items():
        # Skip if this is a camera
        if viewer.is_camera:
            continue
        
        try:
            await viewer.pc.addTrack(track)
            forwarded_count += 1
            logger.info(f"✅ Forwarding {camera_id} to {viewer_id}")
        except Exception as e:
            logger.error(f"❌ Failed to forward to {viewer_id}: {e}")
    
    logger.info(f"📊 {camera_id} forwarded to {forwarded_count} viewers")


@app.get("/")
async def health():
    """Health check endpoint"""
    cameras = sum(1 for c in clients.values() if c.is_camera)
    viewers = sum(1 for c in clients.values() if not c.is_camera)
    
    return {
        "status": "ok",
        "sfu": "active",
        "cameras": cameras,
        "viewers": viewers,
        "total_clients": len(clients)
    }


@app.get("/status")
async def status():
    """Detailed status endpoint"""
    return {
        "cameras": {
            cid: {"connection": client.pc.connectionState if client.pc else "none"}
            for cid, client in clients.items() if client.is_camera
        },
        "viewers": {
            cid: {
                "connection": client.pc.connectionState if client.pc else "none",
                "subscribed": list(client.subscribed_cameras)
            }
            for cid, client in clients.items() if not client.is_camera
        }
    }


@app.get("/viewer", response_class=HTMLResponse)
async def serve_viewer():
    """Serve viewer HTML if exists"""
    viewer_file = Path(__file__).resolve().parent / "viewer.html"
    if not viewer_file.exists():
        return HTMLResponse("viewer.html not found", status_code=404)
    return viewer_file.read_text(encoding="utf-8")


if __name__ == "__main__":
    logger.info("=" * 70)
    logger.info("🎬 SFU (SELECTIVE FORWARDING UNIT) SERVER")
    logger.info(f"TURN: {TURN_IP}:{TURN_PORT}")
    logger.info("=" * 70)
    
    uvicorn.run("sfu_relay_server:app", host="0.0.0.0", port=8000, log_level="info")