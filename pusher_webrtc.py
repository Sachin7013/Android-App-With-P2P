# pusher_webrtc.py
import asyncio, json
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCConfiguration,
    RTCIceServer,
)
from aiortc.contrib.media import MediaPlayer
from aiortc.contrib.signaling import candidate_from_sdp
import websockets

# CONFIG
SIGNALING_WS = "wss://camera-relay.onrender.com/ws/"
CAM_NAME = "camera1"
RTSP_URL = "rtsp://192.168.31.78:5543/live/channel0"
VIEWER_ID = "viewer1"

# TURN config
TURN_HOST = "relay1.expressturn.com"
TURN_USER = "000000002078730066"
TURN_PASS = "dEwJy42Qu8kox+L9Bp1tgkBa0iw="

# ICE servers: STUN + several TURN forms (udp/tcp/turns)
ICE_SERVERS = [
    RTCIceServer(urls="stun:stun.l.google.com:19302"),
    RTCIceServer(urls=f"turn:{TURN_HOST}:3480?transport=udp", username=TURN_USER, credential=TURN_PASS),
    RTCIceServer(urls=f"turn:{TURN_HOST}:3480?transport=tcp", username=TURN_USER, credential=TURN_PASS),
    RTCIceServer(urls=f"turn:{TURN_HOST}:3478?transport=tcp", username=TURN_USER, credential=TURN_PASS),
    RTCIceServer(urls=f"turns:{TURN_HOST}:5349", username=TURN_USER, credential=TURN_PASS),
    RTCIceServer(urls=f"turns:{TURN_HOST}:443", username=TURN_USER, credential=TURN_PASS),
]

async def run():
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ICE_SERVERS))
    print("[pusher] created peer connection")

    @pc.on("iceconnectionstatechange")
    def ice_state():
        print("[pusher] ICE state ->", pc.iceConnectionState)

    @pc.on("connectionstatechange")
    def conn_state():
        print("[pusher] connection state ->", pc.connectionState)

    @pc.on("icegatheringstatechange")
    def gather_state():
        print("[pusher] ICE gathering state ->", pc.iceGatheringState)

    # Read RTSP
    player = MediaPlayer(RTSP_URL, format="rtsp", options={"rtsp_transport": "tcp", "stimeout": "5000000"})
    if player.video:
        pc.addTrack(player.video)
        print("[pusher] added video track from RTSP")
    else:
        print("[pusher] WARNING: no video track from MediaPlayer")

    ws_url = SIGNALING_WS + CAM_NAME
    print("[pusher] connecting to signaling", ws_url)

    try:
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
            print("[pusher] signaling connected")

            @pc.on("icecandidate")
            async def on_local_ice(candidate):
                if candidate is None:
                    print("[pusher] local ICE gathering finished")
                    return
                msg = {"type":"ice", "from": CAM_NAME, "to": VIEWER_ID, "candidate": {
                    "candidate": candidate.to_sdp(), "sdpMid": candidate.sdpMid, "sdpMLineIndex": candidate.sdpMLineIndex
                }}
                await ws.send(json.dumps(msg))
                print("[pusher] sent local ICE candidate (to signaling)")

            # offer
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            await ws.send(json.dumps({"type":"offer","from":CAM_NAME,"to":VIEWER_ID,"sdp":pc.localDescription.sdp}))
            print("[pusher] sent offer")

            # receive messages
            async for raw in ws:
                try:
                    obj = json.loads(raw)
                except Exception as e:
                    print("[pusher] invalid json", e, raw); continue

                typ = obj.get("type")
                if typ == "answer":
                    print("[pusher] received answer")
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=obj["sdp"], type="answer"))
                elif typ == "ice":
                    c = obj.get("candidate") or {}
                    cand_str = c.get("candidate")
                    if not cand_str:
                        await pc.addIceCandidate(None)
                        print("[pusher] remote ICE completed")
                        continue
                    try:
                        cand = candidate_from_sdp(cand_str)
                        cand.sdpMid = c.get("sdpMid")
                        cand.sdpMLineIndex = c.get("sdpMLineIndex")
                        await pc.addIceCandidate(cand)
                        print("[pusher] added remote ICE candidate")
                    except Exception as e:
                        print("[pusher] failed add remote ice:", e, c)
                else:
                    print("[pusher] unknown message", typ)
    except Exception as e:
        print("[pusher] signaling error:", e)

if __name__ == "__main__":
    asyncio.run(run())
