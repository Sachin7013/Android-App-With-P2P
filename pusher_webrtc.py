# pusher_webrtc.py
import asyncio
import json
import os
from aiortc import RTCPeerConnection, RTCSessionDescription, MediaStreamTrack, RTCIceCandidate
from aiortc.contrib.media import MediaPlayer
import websockets

# CONFIG
SIGNALING_WS = "wss://camera-relay.onrender.com/ws/"  # replace with wss://camera-relay.onrender.com/ws/ when deployed
CAM_NAME = "camera1"
RTSP_URL = "rtsp://192.168.31.78:5543/live/channel0"

# ICE servers (start with google STUN, add TURN later)
ICE_SERVERS = [{"urls": "stun:stun.l.google.com:19302"}]

async def run():
    pc = RTCPeerConnection(configuration={"iceServers": ICE_SERVERS})
    print("[pusher] created peer connection")

    # Use MediaPlayer to read RTSP and produce a video track
    player = MediaPlayer(RTSP_URL, format="rtsp", options={"rtsp_transport":"tcp", "stimeout":"5000000"})
    if player.video:
        pc.addTrack(player.video)
        print("[pusher] added video track from RTSP")

    # Connect to signaling
    ws_url = SIGNALING_WS + CAM_NAME
    async with websockets.connect(ws_url) as ws:
        print(f"[pusher] signaling connected to {ws_url}")

        # When ICE candidate is found locally, send to signaling
        @pc.on("icecandidate")
        async def on_icecandidate(event):
            candidate = event
            if candidate is None:
                return
            msg = {"type":"ice", "from": CAM_NAME, "to": "viewer1", "candidate": {
                "candidate": candidate.to_sdp(), "sdpMid": candidate.sdpMid, "sdpMLineIndex": candidate.sdpMLineIndex
            }}
            await ws.send(json.dumps(msg))
            print("[pusher] sent local ICE candidate")

        # Create offer
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        msg = {"type":"offer", "from": CAM_NAME, "to": "viewer1", "sdp": pc.localDescription.sdp}
        await ws.send(json.dumps(msg))
        print("[pusher] sent offer")

        # Listen for messages (answer or remote ICE)
        async for raw in ws:
            obj = json.loads(raw)
            typ = obj.get("type")
            if typ == "answer":
                print("[pusher] received answer")
                await pc.setRemoteDescription(RTCSessionDescription(sdp=obj["sdp"], type="answer"))
            elif typ == "ice":
                # Convert candidate string into RTCIceCandidate or directly add as sdpMid etc
                c = obj.get("candidate")
                try:
                    # aiortc can take RTCIceCandidate(dict) if dict matches shape in browsers
                    cand = RTCIceCandidate(
                        sdpMid=c.get("sdpMid"),
                        sdpMLineIndex=c.get("sdpMLineIndex"),
                        candidate=c.get("candidate")
                    )
                    await pc.addIceCandidate(cand)
                    print("[pusher] added remote ICE")
                except Exception as e:
                    print("Error adding remote ice:", e)

if __name__ == "__main__":
    asyncio.run(run())
