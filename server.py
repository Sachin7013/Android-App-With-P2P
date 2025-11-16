import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager
import uvicorn
import websockets  # For WS handling

CAMERAS = ["camera1", "camera2"]
latest_frames = {cam: None for cam in CAMERAS}
frame_locks = {cam: asyncio.Lock() for cam in CAMERAS}
websocket_connections = {cam: None for cam in CAMERAS}

async def stream_frames(cam_name: str):
    while True:
        async with frame_locks[cam_name]:
            if latest_frames[cam_name] is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' +
                       latest_frames[cam_name] + b'\r\n')
        await asyncio.sleep(0.033)  # 30 FPS

app = FastAPI()

@app.get("/stream/{cam_name}")
async def get_stream(cam_name: str):
    if cam_name not in CAMERAS:
        return {"error": "Camera not found"}
    return StreamingResponse(stream_frames(cam_name),
                             media_type="multipart/x-mixed-replace; boundary=frame")

@app.websocket("/ws/{cam_name}")
async def websocket_endpoint(websocket: WebSocket, cam_name: str):
    if cam_name not in CAMERAS:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    websocket_connections[cam_name] = websocket
    print(f"✅ {cam_name} WS connected from home!")
    try:
        while True:
            data = await websocket.receive_bytes()  # Receive JPEG bytes
            async with frame_locks[cam_name]:
                latest_frames[cam_name] = data
    except WebSocketDisconnect:
        print(f"❌ {cam_name} WS disconnected")
    finally:
        websocket_connections[cam_name] = None

@app.get("/")
async def root():
    return {
        "message": "P2P Camera Relay Ready!",
        "streams": {cam: f"/stream/{cam}" for cam in CAMERAS}
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Render relay starting...")
    yield
    print("🛑 Shutting down...")

app = FastAPI(lifespan=lifespan)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)