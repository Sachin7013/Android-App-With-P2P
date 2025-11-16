from fastapi import FastAPI
import socketio
import asyncio
from contextlib import asynccontextmanager
import uvicorn

sio = socketio.AsyncServer(async_mode='asgi', cors_allowed_origins='*')
app = FastAPI()
sio_app = socketio.ASGIApp(sio, app)

# Rooms for cameras
CAMERAS = ["camera1", "camera2"]
peers = {cam: None for cam in CAMERAS}  # Track connected peers

@sio.event
async def connect(sid, environ):
    print(f"✅ Peer connected: {sid}")

@sio.event
async def join_camera(sid, data):
    cam_name = data['camera']
    if cam_name in CAMERAS:
        await sio.enter_room(sid, cam_name)
        peers[cam_name] = sid
        print(f"📱 App joined {cam_name}")
        # Notify home peer if online
        if peers[cam_name + '_home']:
            await sio.emit('new_viewer', {'sid': sid}, room=peers[cam_name + '_home'])

@sio.event
async def register_home(sid, data):
    cam_name = data['camera']
    if cam_name in CAMERAS:
        peers[cam_name + '_home'] = sid
        await sio.enter_room(sid, cam_name)
        print(f"🏠 Home registered {cam_name}")

@sio.event
async def offer(sid, data):  # WebRTC offer from home to app
    await sio.emit('offer', data, room=peers[data['camera']])  # To app

@sio.event
async def answer(sid, data):  # Answer from app to home
    await sio.emit('answer', data, room=peers[data['camera'] + '_home'])

@sio.event
async def ice_candidate(sid, data):
    target_room = peers[data['camera'] + '_home'] if 'home' in data else peers[data['camera']]
    await sio.emit('ice_candidate', data, room=target_room)

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("🚀 Signaling server ready!")
    yield

app = FastAPI(lifespan=lifespan)
app.mount("/", sio_app)  # Mount Socket.io

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)