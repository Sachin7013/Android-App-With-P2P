# server_signaling.py
import asyncio
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
import uvicorn
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VIEWER_FILE = BASE_DIR / "viewer.html"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Keep track of connected websockets by client id
clients: Dict[str, WebSocket] = {}

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str):
    await websocket.accept()
    print(f"[signaling] {client_id} connected")
    clients[client_id] = websocket
    try:
        while True:
            data = await websocket.receive_text()
            # data is JSON string from either side; forward based on 'to' field
            # Expect JSON like: { type: "offer"/"answer"/"ice", from: "camera1", to: "viewer123", sdp: "...", candidate: {...}}
            print(f"[signaling] recv from {client_id}: {data[:200]}")
            # parse and forward
            import json
            try:
                obj = json.loads(data)
                target = obj.get("to")
                if target and target in clients:
                    await clients[target].send_text(data)
                    print(f"[signaling] forwarded from {obj.get('from')} to {target}")
                else:
                    print(f"[signaling] target {target} not connected yet — ignoring")
            except Exception as exc:
                print("JSON/forward error:", exc)
    except WebSocketDisconnect:
        print(f"[signaling] {client_id} disconnected")
    finally:
        if client_id in clients:
            del clients[client_id]

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
