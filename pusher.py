import asyncio
import cv2
import websockets  # For WS client

CAMERAS = {
    "camera1": "rtsp://192.168.31.78:5543/live/channel0",
    # "camera2": "rtsp://192.168.31.XX:5543/live/channel0"  # Update XX with real IP/port
}

# FIXED: wss:// + /ws/ path
RENDER_WS_BASE = "wss://camera-relay.onrender.com/ws/"

async def push_camera(cam_name):
    rtsp_url = CAMERAS[cam_name]
    ws_url = RENDER_WS_BASE + cam_name  # Now: wss://.../ws/camera1
    while True:
        cap = None
        try:
            print(f"🎥 Connecting to local {cam_name} (RTSP)...")
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                print(f"❌ Local {cam_name} failed (check RTSP URL/Wi-Fi). Retry 5s...")
                await asyncio.sleep(5)
                continue
            print(f"✅ Local {cam_name} captured! Pushing to Render WS: {ws_url}")
            async with websockets.connect(ws_url) as websocket:
                frame_count = 0
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        print(f"⚠️ Lost local {cam_name}. Reconnect...")
                        break
                    frame = cv2.resize(frame, (640, 480))  # Smaller for bandwidth
                    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])  # 70% for less data
                    await websocket.send(buffer.tobytes())  # Send JPEG bytes
                    frame_count += 1
                    if frame_count % 30 == 0:
                        print(f"📹 {cam_name}: Pushed {frame_count} frames to Render")
                    await asyncio.sleep(0.033)  # 30 FPS
        except Exception as e:
            print(f"❌ {cam_name} error (WS or capture): {e}")
            await asyncio.sleep(5)
        finally:
            if cap:
                cap.release()

async def main():
    # Push both (or start with one: tasks = [push_camera("camera1")])
    tasks = [push_camera(cam) for cam in CAMERAS]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    print("🚀 Starting home pusher – ensure cameras on Wi-Fi!")
    asyncio.run(main())