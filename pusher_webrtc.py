# pusher_stun.py
# Simplified STUN-only P2P pusher for IP camera streaming

import asyncio
import json
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
# CONFIGURATION - Change these values
# ============================================================

# Signaling server WebSocket URL
# Option 1: Cloud server (for remote friend access)
SIGNALING_WS = "wss://camera-relay.onrender.com/ws/"

# Option 2: Local server (only works on same network)
# SIGNALING_WS = "ws://localhost:8000/ws/"

# Camera identifier
CAM_NAME = "camera1"

# Your IP camera RTSP URL
RTSP_URL = "rtsp://192.168.31.78:5543/live/channel0"

# Viewer identifier (must match viewer.html)
VIEWER_ID = "viewer1"

# ============================================================
# STUN-ONLY Configuration (Method 3: Aggressive)
# ============================================================

# Multiple STUN servers for better NAT traversal
# NO TURN servers - pure P2P only
ICE_SERVERS = [
    RTCIceServer(urls="stun:stun.l.google.com:19302"),
    RTCIceServer(urls="stun:stun1.l.google.com:19302"),
    RTCIceServer(urls="stun:stun2.l.google.com:19302"),
    RTCIceServer(urls="stun:stun3.l.google.com:19302"),
    RTCIceServer(urls="stun:stun4.l.google.com:19302"),
]

# ============================================================
# Main pusher function
# ============================================================

async def run():
    """
    Main function that:
    1. Connects to IP camera via RTSP
    2. Creates WebRTC peer connection with STUN-only
    3. Connects to signaling server
    4. Exchanges SDP and ICE candidates
    5. Establishes direct P2P connection
    """
    
    # Create peer connection with STUN-only configuration
    pc = RTCPeerConnection(
        configuration=RTCConfiguration(
            iceServers=ICE_SERVERS,
            # Aggressive ICE gathering for better NAT traversal
            iceTransportPolicy="all",  # Try all candidates
        )
    )
    print("✅ [pusher] Created peer connection with STUN-only")
    
    # Event handlers for connection state monitoring
    @pc.on("iceconnectionstatechange")
    def ice_state():
        state = pc.iceConnectionState
        print(f"🔄 [pusher] ICE Connection State → {state}")
        
        if state == "connected":
            print("🎉 [pusher] Direct P2P connection established!")
        elif state == "failed":
            print("❌ [pusher] P2P connection FAILED - likely NAT incompatibility")
            print("💡 [pusher] Suggestion: Check if both sides have Symmetric NAT")
        elif state == "disconnected":
            print("⚠️ [pusher] Connection disconnected")
    
    @pc.on("connectionstatechange")
    def conn_state():
        print(f"🔄 [pusher] Connection State → {pc.connectionState}")
    
    @pc.on("icegatheringstatechange")
    def gather_state():
        state = pc.iceGatheringState
        print(f"🔍 [pusher] ICE Gathering State → {state}")
        
        if state == "complete":
            print("✅ [pusher] All ICE candidates gathered")
    
    # Connect to IP camera via RTSP
    print(f"📹 [pusher] Connecting to camera: {RTSP_URL}")
    
    try:
        player = MediaPlayer(
            RTSP_URL,
            format="rtsp",
            options={
                "rtsp_transport": "tcp",  # Use TCP for RTSP (more reliable)
                "stimeout": "5000000",    # Socket timeout
            }
        )
        
        if player.video:
            pc.addTrack(player.video)
            print("✅ [pusher] Added video track from RTSP camera")
        else:
            print("❌ [pusher] ERROR: No video track from camera")
            return
            
    except Exception as e:
        print(f"❌ [pusher] ERROR connecting to camera: {e}")
        return
    
    # Connect to signaling server
    ws_url = SIGNALING_WS + CAM_NAME
    print(f"🔌 [pusher] Connecting to signaling server: {ws_url}")
    
    try:
        async with websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5
        ) as ws:
            print("✅ [pusher] Connected to signaling server")
            
            # ICE candidate counter
            ice_count = [0]  # Use list for mutable counter in nested function
            
            # Handle local ICE candidates
            @pc.on("icecandidate")
            async def on_local_ice(candidate):
                if candidate is None:
                    print("✅ [pusher] Local ICE gathering finished")
                    return
                
                ice_count[0] += 1
                print(f"🧩 [pusher] Found local ICE candidate #{ice_count[0]}")
                
                # Send candidate to viewer via signaling server
                msg = {
                    "type": "ice",
                    "from": CAM_NAME,
                    "to": VIEWER_ID,
                    "candidate": {
                        "candidate": candidate.to_sdp(),
                        "sdpMid": candidate.sdpMid,
                        "sdpMLineIndex": candidate.sdpMLineIndex
                    }
                }
                await ws.send(json.dumps(msg))
                print(f"📤 [pusher] Sent ICE candidate #{ice_count[0]} to viewer")
            
            # Create and send SDP offer
            print("📝 [pusher] Creating SDP offer...")
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            
            await ws.send(json.dumps({
                "type": "offer",
                "from": CAM_NAME,
                "to": VIEWER_ID,
                "sdp": pc.localDescription.sdp
            }))
            print("📤 [pusher] Sent SDP offer to viewer")
            
            # Receive messages from signaling server
            print("👂 [pusher] Listening for messages...")
            
            async for raw in ws:
                try:
                    obj = json.loads(raw)
                except Exception as e:
                    print(f"❌ [pusher] Invalid JSON: {e}")
                    continue
                
                msg_type = obj.get("type")
                
                # Handle SDP answer from viewer
                if msg_type == "answer":
                    print("📥 [pusher] Received SDP answer from viewer")
                    await pc.setRemoteDescription(
                        RTCSessionDescription(sdp=obj["sdp"], type="answer")
                    )
                    print("✅ [pusher] Set remote description")
                
                # Handle ICE candidate from viewer
                elif msg_type == "ice":
                    c = obj.get("candidate") or {}
                    cand_str = c.get("candidate")
                    
                    if not cand_str:
                        await pc.addIceCandidate(None)
                        print("✅ [pusher] Remote ICE gathering completed")
                        continue
                    
                    try:
                        # Parse and add remote ICE candidate
                        cand = candidate_from_sdp(cand_str)
                        cand.sdpMid = c.get("sdpMid")
                        cand.sdpMLineIndex = c.get("sdpMLineIndex")
                        
                        await pc.addIceCandidate(cand)
                        print("🧩 [pusher] Added remote ICE candidate")
                        
                    except Exception as e:
                        print(f"❌ [pusher] Failed to add remote ICE: {e}")
                
                else:
                    print(f"⚠️ [pusher] Unknown message type: {msg_type}")
    
    except Exception as e:
        print(f"❌ [pusher] Signaling error: {e}")
        print("💡 [pusher] Check if signaling server is running and accessible")


if __name__ == "__main__":
    print("=" * 60)
    print("🚀 STUN-Only P2P Camera Pusher")
    print("=" * 60)
    print(f"📹 Camera: {CAM_NAME}")
    print(f"🎥 RTSP: {RTSP_URL}")
    print(f"👤 Viewer: {VIEWER_ID}")
    print(f"🔌 Signaling: {SIGNALING_WS}")
    print(f"🌐 ICE Servers: {len(ICE_SERVERS)} STUN servers (NO TURN)")
    print("=" * 60)
    print()
    
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n👋 [pusher] Shutdown by user")
    except Exception as e:
        print(f"\n❌ [pusher] Fatal error: {e}")
