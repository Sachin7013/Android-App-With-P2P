import asyncio
import itertools
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
import uvicorn

CAMERAS = ["camera1", "camera2"]
latest_frames = {cam: None for cam in CAMERAS}
frame_locks = {cam: asyncio.Lock() for cam in CAMERAS}
websocket_connections = {cam: None for cam in CAMERAS}
connection_counter = itertools.count(1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Render relay starting...")
    yield
    print("🛑 Shutting down...")


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def stream_frames(cam_name: str):
    while True:
        async with frame_locks[cam_name]:
            if latest_frames[cam_name] is not None:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' +
                       latest_frames[cam_name] + b'\r\n')
        await asyncio.sleep(0.033)  # 30 FPS


@app.get("/stream/{cam_name}")
async def get_stream(cam_name: str):
    print(f"🌐 HTTP request for /stream/{cam_name}")
    if cam_name not in CAMERAS:
        print(f"⚠️ Unknown camera requested: {cam_name}")
        return {"error": "Camera not found"}
    return StreamingResponse(
        stream_frames(cam_name),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


@app.websocket("/ws/{cam_name}")
async def websocket_endpoint(websocket: WebSocket, cam_name: str):
    client = websocket.client
    conn_id = next(connection_counter)
    print(f"🔌 WS#{conn_id} incoming handshake from {client} for {cam_name}")
    if cam_name not in CAMERAS:
        print(f"❌ WS#{conn_id} rejecting unknown camera: {cam_name}")
        await websocket.close(code=1008)
        return
    try:
        await websocket.accept()
        websocket_connections[cam_name] = websocket
        print(
            f"✅ WS#{conn_id} {cam_name} connected from {client}. "
            f"Has_cached_frame={bool(latest_frames[cam_name])}"
        )
    except Exception as exc:
        print(f"🚫 WS#{conn_id} error during accept for {cam_name}: {exc!r}")
        raise
    try:
        frame_counter = 0
        while True:
            data = await websocket.receive_bytes()  # Receive JPEG bytes
            async with frame_locks[cam_name]:
                latest_frames[cam_name] = data
            frame_counter += 1
            if frame_counter % 120 == 0:
                print(f"📥 WS#{conn_id} stored {frame_counter} frames for {cam_name}")
            if websocket.application_state.name != "CONNECTED":
                print(
                    f"⚠️ WS#{conn_id} state -> {websocket.application_state.name} "
                    f"while receiving {cam_name}"
                )
    except WebSocketDisconnect as exc:
        print(f"❌ WS#{conn_id} disconnected for {cam_name}, code={exc.code}")
    except Exception as exc:
        print(f"🔥 WS#{conn_id} unexpected error for {cam_name}: {exc!r}")
        raise
    finally:
        websocket_connections[cam_name] = None
        print(f"🔚 WS#{conn_id} closed for {cam_name}")


@app.get("/")
async def root():
    print("🌐 HTTP request for root")
    return {
        "message": "P2P Camera Relay Ready!",
        "streams": {cam: f"/stream/{cam}" for cam in CAMERAS},
        "known_cameras": CAMERAS,
    }


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)