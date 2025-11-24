import asyncio
import json
import os
from dotenv import load_dotenv
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


# ========================================
# CONFIGURATION - Change these values
# ========================================

# Load .env values
load_dotenv()

# Signaling Server (Your Render server)
SIGNALING_WS = os.getenv("SIGNALING_WS")

# Camera Configuration
CAM_NAME = os.getenv("CAM_NAME")
RTSP_URL_1 = os.getenv("RTSP_URL_1")
RTSP_URL_2 = os.getenv("RTSP_URL_2")
VIEWER_ID = os.getenv("VIEWER_ID")

# YOUR AWS TURN Server Configuration
AWS_TURN_IP = os.getenv("AWS_TURN_IP")
AWS_TURN_PORT = os.getenv("AWS_TURN_PORT")
AWS_TURN_USER = os.getenv("AWS_TURN_USER")
AWS_TURN_PASS = os.getenv("AWS_TURN_PASS")


# ========================================
# ICE Servers Configuration
# ========================================
ICE_SERVERS = [
    # Google's public STUN server
    RTCIceServer(urls="stun:stun.l.google.com:19302"),
    
    # YOUR AWS TURN server - UDP (best for video)
    RTCIceServer(
        urls=f"turn:{AWS_TURN_IP}:{AWS_TURN_PORT}?transport=udp",
        username=AWS_TURN_USER,
        credential=AWS_TURN_PASS
    ),
    
    # YOUR AWS TURN server - TCP fallback
    RTCIceServer(
        urls=f"turn:{AWS_TURN_IP}:{AWS_TURN_PORT}?transport=tcp",
        username=AWS_TURN_USER,
        credential=AWS_TURN_PASS
    ),
]


async def run():
    """Main function to run the camera pusher with 2 cameras"""
    
    # Create peer connection with YOUR TURN configuration
    pc = RTCPeerConnection(
        configuration=RTCConfiguration(iceServers=ICE_SERVERS)
    )
    print(f"[pusher] Peer connection created with AWS TURN server: {AWS_TURN_IP}")


    # ========================================
    # Connection State Monitoring
    # ========================================
    
    @pc.on("iceconnectionstatechange")
    def on_ice_state():
        """Monitor ICE connection state"""
        state = pc.iceConnectionState
        print(f"[pusher] ICE connection state: {state}")
        
        if state == "connected":
            print("[pusher] ✅ ICE connected successfully!")
        elif state == "failed":
            print("[pusher] ❌ ICE connection failed")
        elif state == "disconnected":
            print("[pusher] ⚠️  ICE disconnected")


    @pc.on("connectionstatechange")
    def on_conn_state():
        """Monitor overall connection state"""
        state = pc.connectionState
        print(f"[pusher] Connection state: {state}")
        
        if state == "connected":
            print("[pusher] ✅ Peer connection established!")
        elif state == "failed":
            print("[pusher] ❌ Peer connection failed")


    @pc.on("icegatheringstatechange")
    def on_gather_state():
        """Monitor ICE gathering state"""
        state = pc.iceGatheringState
        print(f"[pusher] ICE gathering state: {state}")
        
        if state == "complete":
            print("[pusher] ✅ ICE gathering completed")


    # ========================================
    # Add Video Tracks from BOTH RTSP Cameras
    # ========================================
    
    players = []  # Store both players to keep them alive
    
    # Camera 1
    print(f"[pusher] Connecting to RTSP Camera 1: {RTSP_URL_1}")
    try:
        player1 = MediaPlayer(
            RTSP_URL_1,
            format="rtsp",
            options={
                "rtsp_transport": "tcp",
                "stimeout": "5000000"
            }
        )
        
        if player1.video:
            # Add track with stream ID for identification
            pc.addTrack(player1.video)
            players.append(player1)
            print("[pusher] ✅ Camera 1 video track added")
        else:
            print("[pusher] ⚠️  WARNING: No video track from Camera 1")
            
    except Exception as e:
        print(f"[pusher] ❌ Error connecting to Camera 1: {e}")


    # Camera 2
    print(f"[pusher] Connecting to RTSP Camera 2: {RTSP_URL_2}")
    try:
        player2 = MediaPlayer(
            RTSP_URL_2,
            format="rtsp",
            options={
                "rtsp_transport": "tcp",
                "stimeout": "5000000"
            }
        )
        
        if player2.video:
            # Add track with stream ID for identification
            pc.addTrack(player2.video)
            players.append(player2)
            print("[pusher] ✅ Camera 2 video track added")
        else:
            print("[pusher] ⚠️  WARNING: No video track from Camera 2")
            
    except Exception as e:
        print(f"[pusher] ❌ Error connecting to Camera 2: {e}")


    # Check if at least one camera connected
    if len(players) == 0:
        print("[pusher] ❌ No cameras connected. Exiting.")
        return


    print(f"[pusher] ✅ Total cameras connected: {len(players)}")


    # ========================================
    # Connect to Signaling Server
    # ========================================
    
    ws_url = SIGNALING_WS + CAM_NAME
    print(f"[pusher] Connecting to signaling server: {ws_url}")


    try:
        async with websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5
        ) as ws:
            
            print("[pusher] ✅ Signaling server connected")


            # ========================================
            # Handle Local ICE Candidates
            # ========================================
            
            @pc.on("icecandidate")
            async def on_local_ice(candidate):
                """Send local ICE candidates to viewer via signaling"""
                
                if candidate is None:
                    print("[pusher] ✅ Local ICE gathering finished")
                    return
                
                # Check if this is a relay candidate (using TURN)
                if "relay" in candidate.to_sdp():
                    print(f"[pusher] 🔄 Using TURN relay: {AWS_TURN_IP}")
                
                # Send ICE candidate to signaling server
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
                print("[pusher] Sent ICE candidate to signaling server")


            # ========================================
            # Create and Send Offer
            # ========================================
            
            print("[pusher] Creating SDP offer...")
            offer = await pc.createOffer()
            await pc.setLocalDescription(offer)
            
            # Send offer to signaling server
            offer_msg = {
                "type": "offer",
                "from": CAM_NAME,
                "to": VIEWER_ID,
                "sdp": pc.localDescription.sdp
            }
            await ws.send(json.dumps(offer_msg))
            print("[pusher] ✅ Offer sent to viewer (with 2 video tracks)")


            # ========================================
            # Receive Messages from Signaling Server
            # ========================================
            
            async for raw_message in ws:
                try:
                    message = json.loads(raw_message)
                except json.JSONDecodeError as e:
                    print(f"[pusher] ⚠️  Invalid JSON: {e}")
                    continue


                msg_type = message.get("type")


                # Handle answer from viewer
                if msg_type == "answer":
                    print("[pusher] Received answer from viewer")
                    
                    answer = RTCSessionDescription(
                        sdp=message["sdp"],
                        type="answer"
                    )
                    await pc.setRemoteDescription(answer)
                    print("[pusher] ✅ Remote description set")


                # Handle ICE candidate from viewer
                elif msg_type == "ice":
                    candidate_data = message.get("candidate") or {}
                    candidate_str = candidate_data.get("candidate")
                    
                    if not candidate_str:
                        await pc.addIceCandidate(None)
                        print("[pusher] ✅ Remote ICE gathering completed")
                        continue
                    
                    try:
                        candidate = candidate_from_sdp(candidate_str)
                        candidate.sdpMid = candidate_data.get("sdpMid")
                        candidate.sdpMLineIndex = candidate_data.get("sdpMLineIndex")
                        
                        await pc.addIceCandidate(candidate)
                        print("[pusher] Added remote ICE candidate")
                        
                    except Exception as e:
                        print(f"[pusher] ⚠️  Failed to add remote ICE candidate: {e}")


                else:
                    print(f"[pusher] ⚠️  Unknown message type: {msg_type}")


    except websockets.exceptions.WebSocketException as e:
        print(f"[pusher] ❌ WebSocket error: {e}")
    except Exception as e:
        print(f"[pusher] ❌ Signaling error: {e}")


# ========================================
# Main Entry Point
# ========================================

if __name__ == "__main__":
    print("="*60)
    print("Multi-Camera Pusher - Using YOUR AWS TURN Server")
    print(f"TURN Server: {AWS_TURN_IP}:{AWS_TURN_PORT}")
    print(f"Camera Name: {CAM_NAME}")
    print(f"Camera 1: {RTSP_URL_1}")
    print(f"Camera 2: {RTSP_URL_2}")
    print("="*60)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\n[pusher] Stopped by user")
    except Exception as e:
        print(f"[pusher] ❌ Fatal error: {e}")
