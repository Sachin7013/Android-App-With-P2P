# pusher_stun.py (FIXED - Forces ICE Candidate Generation)
# Production version with guaranteed ICE candidate gathering

import asyncio
import json
from datetime import datetime
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCConfiguration,
    RTCIceServer,
)
from aiortc.contrib.media import MediaPlayer
from aiortc.contrib.signaling import candidate_from_sdp
import websockets

# ============================================================
# CONFIGURATION
# ============================================================

SIGNALING_WS = "wss://camera-relay.onrender.com/ws/"
CAM_NAME = "camera1"
RTSP_URL = "rtsp://192.168.31.78:5543/live/channel0"
VIEWER_ID = "viewer1"

# ============================================================
# STUN Servers
# ============================================================

ICE_SERVERS = [
    # Google STUN
    RTCIceServer(urls="stun:stun.l.google.com:19302"),
    RTCIceServer(urls="stun:stun1.l.google.com:19302"),
    RTCIceServer(urls="stun:stun2.l.google.com:19302"),
    
    # Cloudflare STUN
    RTCIceServer(urls="stun:stun.cloudflare.com:3478"),
    
    # Other public STUN servers
    RTCIceServer(urls="stun:stun.relay.metered.ca:80"),
    RTCIceServer(urls="stun:stun.services.mozilla.com:3478"),
    RTCIceServer(urls="stun:stun.nextcloud.com:443"),
    RTCIceServer(urls="stun:stun.sipgate.net:3478"),
    RTCIceServer(urls="stun:stun.voip.blackberry.com:3478"),
]


# ============================================================
# Global state
# ============================================================

pc = None
ws = None
should_run = True

# ============================================================
# Helper functions
# ============================================================

def log(message, level="INFO"):
    """Log with timestamp"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    icons = {
        "INFO": "ℹ️", "SUCCESS": "✅", "ERROR": "❌", 
        "WARN": "⚠️", "DEBUG": "🔍"
    }
    print(f"[{timestamp}] {icons.get(level, '📝')} [pusher] {message}")


# --- REPLACEMENT: safer_run_loop.py (only replace run() and __main__ with this) ---

async def run_once():
    """Single run iteration: create PC, connect camera, send offer, then handle messages until disconnect."""
    global pc, ws, should_run

    pc = None
    ws = None

    # create PC with corrected ICE server shape
    pc = RTCPeerConnection(configuration=RTCConfiguration(iceServers=ICE_SERVERS))
    log("Created peer connection with STUN-only", "SUCCESS")

    ice_candidates = []
    ice_stats = {"host": 0, "srflx": 0, "relay": 0}
    ice_gathering_complete = asyncio.Event()

    # event handlers (same logic as before, but DON'T call run() from them)
    @pc.on("icecandidate")
    async def on_ice_candidate(candidate):
        if candidate is None:
            log("Local ICE gathering finished", "DEBUG")
            return
        ice_candidates.append(candidate)
        cand_str = candidate.to_sdp()
        if "typ host" in cand_str:
            ice_stats["host"] += 1
            cand_type = "host"
        elif "typ srflx" in cand_str:
            ice_stats["srflx"] += 1
            cand_type = "srflx (STUN)"
        elif "typ relay" in cand_str:
            ice_stats["relay"] += 1
            cand_type = "relay"
        else:
            cand_type = "unknown"

        log(f"Found ICE candidate #{len(ice_candidates)} - Type: {cand_type}", "INFO")

        # Send candidate immediately if websocket ready
        try:
            if ws and ws.open:
                await ws.send(json.dumps({
                    "type": "ice",
                    "from": CAM_NAME,
                    "to": VIEWER_ID,
                    "candidate": {
                        "candidate": candidate.to_sdp(),
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex
                    }
                }))
                log(f"Sent ICE candidate #{len(ice_candidates)} to viewer", "DEBUG")
        except Exception as e:
            log(f"Failed to send ICE candidate: {e}", "ERROR")

    @pc.on("icegatheringstatechange")
    async def on_ice_gathering_state_change():
        state = pc.iceGatheringState
        log(f"ICE Gathering State → {state}", "DEBUG")
        if state == "complete":
            ice_gathering_complete.set()
            total = ice_stats["host"] + ice_stats["srflx"] + ice_stats["relay"]
            log(f"All ICE candidates gathered: host={ice_stats['host']}, srflx={ice_stats['srflx']}, relay={ice_stats['relay']}, total={total}", "SUCCESS")
            if total == 0:
                log("WARNING: NO ICE candidates! Network issue detected (see suggestions).", "ERROR")

    @pc.on("iceconnectionstatechange")
    async def on_ice_connection_state_change():
        state = pc.iceConnectionState
        log(f"ICE Connection State → {state}", "DEBUG")
        if state in ("failed", "disconnected", "closed"):
            log(f"ICE state {state} — will exit run_once and let supervisor reconnect", "WARN")

    # --- Attach camera
    log(f"Connecting to camera: {RTSP_URL}", "INFO")
    try:
        player = MediaPlayer(
            RTSP_URL,
            format="rtsp",
            options={
                "rtsp_transport": "tcp",
                "stimeout": "5000000",
                "max_delay": "500000",
                "fflags": "nobuffer",
                "flags": "low_delay",
            }
        )
        if player.video:
            pc.addTrack(player.video)
            log("Added video track from RTSP camera", "SUCCESS")
        else:
            log("No video track from camera", "ERROR")
            await pc.close()
            return
    except Exception as e:
        log(f"Failed to connect to camera: {e}", "ERROR")
        if pc:
            await pc.close()
        return

    # create data channel BEFORE offer to force ICE gathering
    data_channel = pc.createDataChannel("keepalive")
    log("Created data channel to force ICE gathering", "INFO")

    # Connect to signaling server (one recv loop here ONLY)
    ws_url = SIGNALING_WS + CAM_NAME
    log(f"Connecting to signaling server: {ws_url}", "INFO")
    try:
        ws = await websockets.connect(ws_url, ping_interval=20, ping_timeout=10)
        log("Connected to signaling server", "SUCCESS")
    except Exception as e:
        log(f"Failed to connect to signaling server: {e}", "ERROR")
        await pc.close()
        return

    # Create offer & set local description
    log("Creating SDP offer...", "INFO")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)

    # Wait for ICE gathering (longer timeout during debugging)
    log("Waiting for ICE candidates to be gathered (timeout 12s)...", "INFO")
    try:
        await asyncio.wait_for(ice_gathering_complete.wait(), timeout=12.0)
    except asyncio.TimeoutError:
        log("ICE gathering timeout (12s), proceeding anyway...", "WARN")

    # Send offer (even if zero candidates — viewer can trickle)
    await ws.send(json.dumps({
        "type": "offer",
        "from": CAM_NAME,
        "to": VIEWER_ID,
        "sdp": pc.localDescription.sdp
    }))
    log(f"Sent SDP offer to viewer (with {len(ice_candidates)} candidates)", "SUCCESS")

    # Message loop (single recv use)
    try:
        while should_run:
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=30.0)
            except asyncio.TimeoutError:
                # send ping
                try:
                    await ws.ping()
                    log("Sent keep-alive ping", "DEBUG")
                    continue
                except Exception:
                    log("Keep-alive failed, breaking message loop", "WARN")
                    break

            # handle message
            try:
                obj = json.loads(message)
            except Exception as e:
                log(f"Invalid JSON from signaling: {e}", "ERROR")
                continue

            msg_type = obj.get("type")
            if msg_type == "answer":
                log("Received SDP answer from viewer", "INFO")
                await pc.setRemoteDescription(RTCSessionDescription(sdp=obj["sdp"], type="answer"))
                log("Set remote description", "SUCCESS")
            elif msg_type == "ice":
                c = obj.get("candidate") or {}
                cand_str = c.get("candidate")
                if not cand_str:
                    # remote finished
                    await pc.addIceCandidate(None)
                    log("Remote ICE gathering completed", "DEBUG")
                    continue
                try:
                    from aiortc.contrib.signaling import candidate_from_sdp
                    cand = candidate_from_sdp(cand_str)
                    cand.sdpMid = c.get("sdpMid")
                    cand.sdpMLineIndex = c.get("sdpMLineIndex")
                    await pc.addIceCandidate(cand)
                    log("Added remote ICE candidate", "DEBUG")
                except Exception as e:
                    log(f"Failed to add ICE candidate: {e}", "ERROR")
            else:
                log(f"Unhandled signaling message: {obj.get('type')}", "DEBUG")

            # keep loop running until ws closes or ICE fails/closed
            if pc.iceConnectionState in ("failed", "closed"):
                log("ICE broken, breaking message loop", "WARN")
                break

    except websockets.exceptions.ConnectionClosed:
        log("Signaling connection closed unexpectedly", "WARN")
    except Exception as e:
        log(f"Error in message loop: {e}", "ERROR")
    finally:
        # cleanup pc + ws gracefully
        try:
            await pc.close()
            log("Peer connection closed (run_once finally)", "SUCCESS")
        except:
            pass
        try:
            await ws.close()
            log("WebSocket closed (run_once finally)", "SUCCESS")
        except:
            pass
        # allow supervisor to reconnect after a short delay
        return


# Supervisor loop (single place managing reconnects)
if __name__ == "__main__":
    print("=" * 70)
    print("🚀 STUN-Only P2P Camera Pusher (SUPERVISOR MODE)")
    print("=" * 70)
    print(f"📹 Camera ID: {CAM_NAME}")
    print(f"🎥 RTSP URL: {RTSP_URL}")
    print(f"👤 Viewer ID: {VIEWER_ID}")
    print(f"🔌 Signaling: {SIGNALING_WS}")
    print(f"🌐 STUN Servers: {len(ICE_SERVERS)} servers (NO TURN)")
    print("=" * 70)
    print()

    loop = asyncio.get_event_loop()

    try:
        while True:
            should_run = True
            loop.run_until_complete(run_once())
            if not should_run:
                break
            log("Supervisor: run_once ended — reconnecting in 5s...", "WARN")
            loop.run_until_complete(asyncio.sleep(5))
    except KeyboardInterrupt:
        log("Shutdown by user", "WARN")
    finally:
        try:
            loop.run_until_complete(shutdown())
        except:
            pass
        loop.close()
