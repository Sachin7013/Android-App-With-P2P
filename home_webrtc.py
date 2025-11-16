import asyncio
import cv2
from aiortc import RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.contrib.media import MediaPlayer
import socketio
import json

# Signaling URL
SIGNALING_URL = "https://camera-relay.onrender.com"  # Update

CAMERAS = {
    "camera1": "rtsp://192.168.31.78:5543/live/channel0",
    "camera2": "rtsp://192.168.31.XX:5543/live/channel0"
}

sio = socketio.Client()

class CameraTrack(VideoStreamTrack):
    def __init__(self, cap):
        super().__init__()
        self.cap = cap

    async def recv(self):
        ret, frame = self.cap.read()
        if ret:
            frame = cv2.resize(frame, (640, 480))
            return frame  # aiortc handles encoding

@sio.event
def connect():
    print("✅ Home connected to signaling")

@sio.event
def new_viewer(data):
    print("📱 New viewer! Starting stream...")
    # Create offer for WebRTC
    global pc
    pc = RTCPeerConnection()
    cap = cv2.VideoCapture(CAMERAS['camera1'])  # For camera1; loop for multi
    track = CameraTrack(cap)
    pc.addTrack(track)

    @pc.on("icecandidate")
    def on_ice(data):
        sio.emit('ice_candidate', {'candidate': data.candidate, 'camera': 'camera1', 'home': True})

    async def create_offer():
        await pc.setLocalDescription(await pc.createOffer())
        sio.emit('offer', {'sdp': pc.localDescription.sdp, 'type': pc.localDescription.type, 'camera': 'camera1'})

    asyncio.create_task(create_offer())

@sio.event
def answer(data):
    future = pc.setRemoteDescription(RTCSessionDescription(sdp=data['sdp'], type=data['type']))
    asyncio.run(future)

@sio.event
def ice_candidate(data):
    pc.addIceCandidate(data['candidate'])

async def main(cam_name):
    sio.connect(SIGNALING_URL)
    sio.emit('register_home', {'camera': cam_name})
    await sio.wait()

if __name__ == "__main__":
    asyncio.run(main('camera1'))  # Run per camera or loop