# pusher_webrtc.py
import asyncio
import json
import os
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCConfiguration,
    RTCIceServer,
)
from aiortc.contrib.media import MediaPlayer
import websockets

# ======= CONFIG =======
SIGNALING_WS = "wss://camera-relay.onrender.com/ws/"   # signaling base
CAM_NAME = "camera1"
RTSP_URL = "rtsp://192.168.31.78:5543/live/channel0"
VIEWER_ID = "viewer1"

# TURN details you provided
TURN_URL = "turn:relay1.expressturn.com:3480"
TURN_USER = "000000002078730066"
TURN_PASS = "dEwJy42Qu8kox+L9Bp1tgkBa0iw="
# ICE servers (STUN + TURN)
ICE_SERVERS = [
    RTCIceServer(urls="stun:stun.l.google.com:19302"),
    RTCIceServer(urls=TURN_URL, username=TURN_USER, credential=TURN_PASS)
]
# ======================

async def run():
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ICE_SERVERS))
    print("[pusher] created peer connection")

    # Debug callbacks
    @pc.on("iceconnectionstatechange")
    def on_iceconnectionstatechange():
        print("[pusher] ICE state ->", pc.iceConnectionState)

    @pc.on("connectionstatechange")
    def on_connectionstatechange():
        print("[pusher] connection state ->", pc.connectionState)

    # Use MediaPlayer to read RTSP and produce a video track
    player = MediaPlayer(RTSP_URL, format="rtsp", options={"rtsp_transport": "tcp", "stimeout": "5000000"})
    if player.video:
        pc.addTrack(player.video)
        print("[pusher] added video track from RTSP")
    else:
        print("[pusher] WARNING: no video track from MediaPlayer (check RTSP)")

    ws_url = SIGNALING_WS + CAM_NAME
    print("[pusher] connecting to signaling", ws_url)

    # Connect to signaling server
    try:
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20, close_timeout=5) as ws:
            print(f"[pusher] signaling connected to {ws_url}")

            # When ICE candidate is found locally, send to signaling
            @pc.on("icecandidate")
            async def on_icecandidate(event):
                candidate = event
                if candidate is None:
                    return
                msg = {"type": "ice", "from": CAM_NAME, "to": VIEWER_ID, "candidate": {
                    "candidate": candidate.to_sdp(), "sdpMid": candidate.sdpMid, "sdpMLineIndex": candidate.sdpMLineIndex
                }}
                await ws.send(json.dumps(msg))
                print("[pusher] sent local ICE candidate")

            # Create offer and send
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            msg = {"type": "offer", "from": CAM_NAME, "to": VIEWER_ID, "sdp": pc.localDescription.sdp}
            await ws.send(json.dumps(msg))
            print("[pusher] sent offer")

            # Listen for messages (answer or remote ICE)
            async for raw in ws:
                try:
                    obj = json.loads(raw)
                except Exception as e:
                    print("[pusher] invalid json from signaling:", e, raw)
                    continue

                typ = obj.get("type")
                if typ == "answer":
                    print("[pusher] received answer")
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=obj["sdp"], type="answer"))
                elif typ == "ice":
                    c = obj.get("candidate")
                    try:
                        cand = RTCIceCandidate(
                            sdpMid=c.get("sdpMid"),
                            sdpMLineIndex=c.get("sdpMLineIndex"),
                            candidate=c.get("candidate")
                        )
                        await pc.addIceCandidate(cand)
                        print("[pusher] added remote ICE")
                    except Exception as e:
                        print("[pusher] Error adding remote ice:", e)
                else:
                    print("[pusher] unknown message type:", typ)

    except Exception as e:
        print("[pusher] signaling connection failed:", e)

if __name__ == "__main__":
    asyncio.run(run())
