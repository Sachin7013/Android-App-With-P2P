# # pusher_webrtc_fixed.py
import asyncio
import json
import os
import time
from dotenv import load_dotenv
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
    VideoStreamTrack,
)
from aiortc.contrib.media import MediaPlayer
from aiortc.contrib.signaling import candidate_from_sdp
import websockets

load_dotenv()

SIGNALING_WS = os.getenv("SIGNALING_WS")
CAM_NAME = os.getenv("CAM_NAME", "camera1")
RTSP_URL_2 = os.getenv("RTSP_URL_2")
VIEWER_ID = os.getenv("VIEWER_ID", "viewer1")

AWS_TURN_IP = os.getenv("AWS_TURN_IP")
AWS_TURN_PORT = os.getenv("AWS_TURN_PORT")
AWS_TURN_USER = os.getenv("AWS_TURN_USER")
AWS_TURN_PASS = os.getenv("AWS_TURN_PASS")

ICE_SERVERS = [RTCIceServer(urls="stun:stun.l.google.com:19302")]
if AWS_TURN_IP and AWS_TURN_PORT and AWS_TURN_USER and AWS_TURN_PASS:
    ICE_SERVERS += [
        RTCIceServer(
            urls=f"turn:{AWS_TURN_IP}:{AWS_TURN_PORT}?transport=udp",
            username=AWS_TURN_USER,
            credential=AWS_TURN_PASS,
        ),
        RTCIceServer(
            urls=f"turn:{AWS_TURN_IP}:{AWS_TURN_PORT}?transport=tcp",
            username=AWS_TURN_USER,
            credential=AWS_TURN_PASS,
        ),
    ]


class ProxyVideoTrack(VideoStreamTrack):
    """
    Simple wrapper track that forwards frames from source_track
    but exposes its own unique id/label so browser mapping is clear.
    """
    def __init__(self, source_track, label):
        super().__init__()
        self.source = source_track
        self.label = label
        # Use label as the ID to ensure uniqueness across tracks
        self._id = label

    @property
    def id(self):
        return self._id

    @property
    def kind(self):
        return getattr(self.source, "kind", "video")

    async def recv(self):
        frame = await self.source.recv()
        return frame


async def check_player_frames(player, label, timeout=3.0):
    """Try to receive a single frame from player.video to ensure the RTSP source is healthy."""
    if not getattr(player, "video", None):
        print(f"[debug] {label}: No video attribute on player")
        return False
    try:
        frame = await asyncio.wait_for(player.video.recv(), timeout=timeout)
        if frame is None:
            print(f"[debug] {label}: recv returned None")
            return False
        print(f"[debug] {label}: got frame pts={getattr(frame, 'pts', '?')} size={getattr(frame,'width','?')}x{getattr(frame,'height','?')}")
        return True
    except asyncio.TimeoutError:
        print(f"[debug] {label}: recv() timed out after {timeout}s")
        return False
    except Exception as e:
        print(f"[debug] {label}: recv() exception: {e}")
        return False


async def run():
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ICE_SERVERS))
    print(f"[pusher] PeerConnection created. TURN: {AWS_TURN_IP}:{AWS_TURN_PORT if AWS_TURN_IP else ''}")

    @pc.on("iceconnectionstatechange")
    def on_ice_state():
        print("[pusher] ICE state:", pc.iceConnectionState)

    @pc.on("connectionstatechange")
    def on_conn_state():
        print("[pusher] Connection state:", pc.connectionState)
        if pc.connectionState == "failed":
            print("[pusher] ⚠️ Connection failed, will attempt to recover")
        elif pc.connectionState == "disconnected":
            print("[pusher] ⚠️ Connection disconnected, waiting for reconnection")

    @pc.on("icegatheringstatechange")
    def on_gather_state():
        print("[pusher] ICE gathering state:", pc.iceGatheringState)

    players = []

    async def create_player(rtsp_url, label):
        try:
            print(f"[pusher] Creating MediaPlayer for {label}: {rtsp_url}")
            player = MediaPlayer(rtsp_url, format="rtsp",
                                 options={"rtsp_transport":"tcp", "stimeout":"5000000"})
            await asyncio.sleep(0.5)  # allow ffmpeg to spin up
            ok = await check_player_frames(player, label, timeout=3.0)
            if not ok:
                # try one more short retry
                print(f"[pusher] {label}: retrying frame check")
                await asyncio.sleep(1.0)
                ok = await check_player_frames(player, label, timeout=3.0)
            if not ok:
                print(f"[pusher] ⚠️ {label}: no frames detected (RTSP may be wrong or camera offline)")
            else:
                print(f"[pusher] ✅ {label}: frames detected")
            players.append((label, player))
            return (label, player, ok)
        except Exception as e:
            print(f"[pusher] ❌ Error creating player for {label}: {e}")
            return (label, None, False)

    # create player
    info2 = await create_player(RTSP_URL_2, "cam2")

    # If no players, exit
    if info2[1] is None:
        print("[pusher] ❌ No players created, exiting")
        await pc.close()
        return

    # Add separate transceivers: one per camera. This forces separate m=video lines in SDP.
    async def add_transceiver_for(player_tuple):
        label, player = player_tuple
        if player is None:
            print(f"[pusher] Skipping {label}: player is None")
            return False
        try:
            proxied = ProxyVideoTrack(player.video, label)
            # Preferred approach: add a separate transceiver for each track.
            # We attempt to attach proxied directly to addTransceiver if supported.
            try:
                transceiver = pc.addTransceiver(proxied, direction="sendonly")
                # Some aiortc versions return a transceiver; the sender will be created.
                print(f"[pusher] Added transceiver for {label}. transceiver={transceiver}")
            except TypeError:
                # Fallback: add transceiver by kind, then replace sender.track
                transceiver = pc.addTransceiver(kind="video", direction="sendonly")
                sender = transceiver.sender
                try:
                    # replace_track may be available; try it.
                    await sender.replace_track(proxied)
                    print(f"[pusher] Replaced transceiver sender.track for {label}")
                except Exception:
                    # fallback to addTrack (less ideal)
                    sender = pc.addTrack(proxied)
                    print(f"[pusher] Fallback: used addTrack for {label}; sender={sender}")
            # Log sender info if possible
            try:
                s = transceiver.sender
                print(f"[pusher] sender for {label}: id={getattr(s,'id',None)} track={getattr(s,'track',None)}")
            except Exception:
                pass
            return True
        except Exception as e:
            print(f"[pusher] ❌ Error adding transceiver for {label}: {e}")
            return False

    # Add transceivers for each created player
    added2 = await add_transceiver_for((info2[0], info2[1]))

    print(f"[pusher] Completed adding transceivers: cam2_added={added2}")

    # Connect signaling
    ws_url = SIGNALING_WS.rstrip("/") + "/" + CAM_NAME
    print("[pusher] Connecting to signaling server:", ws_url)

    try:
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=10, close_timeout=5) as ws:
            print("[pusher] ✅ Signaling connected")

            @pc.on("icecandidate")
            async def on_local_ice(candidate):
                try:
                    if candidate is None:
                        print("[pusher] ✅ Local ICE gathering finished")
                        await ws.send(json.dumps({"type":"ice","from":CAM_NAME,"to":VIEWER_ID,"candidate":{}}))
                        return
                    if "relay" in candidate.to_sdp():
                        print("[pusher] 🔄 Sending TURN candidate")
                    msg = {
                        "type":"ice",
                        "from": CAM_NAME,
                        "to": VIEWER_ID,
                        "candidate": {
                            "candidate": candidate.to_sdp(),
                            "sdpMid": candidate.sdpMid,
                            "sdpMLineIndex": candidate.sdpMLineIndex
                        }
                    }
                    await ws.send(json.dumps(msg))
                    print("[pusher] Sent ICE candidate")
                except Exception as e:
                    print("[pusher] ❌ Error sending ICE candidate:", e)

            # create offer
            print("[pusher] Creating SDP offer...")
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            print("[pusher] Local description (offer) set. Printing short SDP for debug:")
            sdp = pc.localDescription.sdp
            print("----- SDP START (first 1600 chars) -----")
            print(sdp[:1600])
            print("----- SDP END -----")

            # send offer
            offer_msg = {"type":"offer","from": CAM_NAME, "to": VIEWER_ID, "sdp": pc.localDescription.sdp}
            await ws.send(json.dumps(offer_msg))
            print("[pusher] ✅ Offer sent to viewer")

            # Keep track of connection state
            connection_established = False
            last_activity = time.time()

            # handle incoming messages with keep-alive
            try:
                async for raw in ws:
                    last_activity = time.time()
                    try:
                        message = json.loads(raw)
                    except Exception as e:
                        print("[pusher] ⚠️ Invalid JSON:", e)
                        continue

                    typ = message.get("type")
                    if typ == "answer":
                        print("[pusher] Received answer: setting remote description")
                        try:
                            answer = RTCSessionDescription(sdp=message["sdp"], type="answer")
                            await pc.setRemoteDescription(answer)
                            print("[pusher] ✅ Remote description set")
                            connection_established = True
                        except Exception as e:
                            print("[pusher] ❌ setRemoteDescription failed:", e)
                    elif typ == "ice":
                        try:
                            candidate_data = message.get("candidate") or {}
                            candidate_str = candidate_data.get("candidate")
                            if not candidate_str:
                                await pc.addIceCandidate(None)
                                print("[pusher] ✅ Remote ICE end (added None)")
                                continue
                            candidate = candidate_from_sdp(candidate_str)
                            candidate.sdpMid = candidate_data.get("sdpMid")
                            candidate.sdpMLineIndex = candidate_data.get("sdpMLineIndex")
                            await pc.addIceCandidate(candidate)
                            print("[pusher] Added remote ICE candidate")
                        except Exception as e:
                            print("[pusher] ⚠️ Failed to add remote ICE:", e)
                    else:
                        print("[pusher] ⚠️ Unknown message type:", typ)
            except asyncio.CancelledError:
                print("[pusher] Message handling cancelled")
                raise
            finally:
                # Keep connection alive indefinitely
                if connection_established:
                    print("[pusher] ✅ Connection established, maintaining stream indefinitely...")
                    try:
                        # Keep the connection open - send heartbeat periodically
                        while pc.connectionState not in ["closed", "failed"]:
                            await asyncio.sleep(20)  # Check every 20 seconds
                            
                            # Send keep-alive ping to maintain signaling connection
                            try:
                                if ws and not ws.closed:
                                    await ws.send(json.dumps({
                                        "type": "ping",
                                        "from": CAM_NAME,
                                        "to": VIEWER_ID
                                    }))
                            except Exception as e:
                                print(f"[pusher] Keep-alive ping failed: {e}")
                                break
                    except Exception as e:
                        print(f"[pusher] Keep-alive loop error: {e}")

    except Exception as e:
        print("[pusher] ❌ Signaling/WS exception:", e)
    finally:
        print("[pusher] Closing peer connection")
        await pc.close()


if __name__ == "__main__":
    print("="*60)
    print("Multi-Camera Pusher FIXED")
    print(f"CAM_NAME: {CAM_NAME}")
    print(f"RTSP 2: {RTSP_URL_2}")
    print("="*60)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[pusher] Stopped by user")
    except Exception as e:
        print("[pusher] Fatal:", e)

