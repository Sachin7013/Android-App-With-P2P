import asyncio
import cv2
import websockets
import json

CAMERAS = {
    "camera1": "rtsp://192.168.31.78:5543/live/channel0",
}

RENDER_WS_BASE = "wss://camera-relay.onrender.com"  # Your Render URL

async def push_camera(cam_name):
    rtsp_url = CAMERAS[cam_name]
    ws_url = RENDER_WS_BASE + cam_name
    while True:
        try:
            print(f"🎥 Connecting to {cam_name}...")
            cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            if not cap.isOpened():
                print(f"❌ {cam_name} failed. Retry 5s...")
                await asyncio.sleep(5)
                continue
            print(f"✅ {cam_name} connected, pushing to {ws_url}")
            async with websockets.connect(ws_url) as websocket:
                frame_count = 0
                while True:
                    ret, frame = cap.read()
                    if not ret:
                        print(f"⚠️ {cam_name} lost. Reconnect...")
                        break
                    frame = cv2.resize(frame, (640, 480))
                    _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    await websocket.send(buffer.tobytes())
                    frame_count += 1
                    if frame_count % 30 == 0:
                        print(f"📹 {cam_name}: {frame_count} frames pushed")
                    await asyncio.sleep(0.033)  # 30 FPS
        except Exception as e:
            print(f"❌ {cam_name} error: {e}")
            await asyncio.sleep(5)
        finally:
            cap.release()

async def main():
    # Push both cameras
    tasks = [push_camera(cam) for cam in CAMERAS]
    await asyncio.gather(*tasks)

if __name__ == "__main__":
    asyncio.run(main())