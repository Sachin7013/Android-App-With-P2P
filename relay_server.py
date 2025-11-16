import asyncio
import cv2
import os
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager

# Set FFmpeg timeout to 5 seconds (instead of 30+ seconds)
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|timeout;5000000"

# Your camera RTSP URL - CHANGE THIS TO YOUR ACTUAL CAMERA URL
RTSP_URL = "rtsp://192.168.31.78:5543/live/channel0"

# Store the latest frame
latest_frame = None
frame_lock = asyncio.Lock()
capture_task = None

async def capture_camera():
    """Continuously capture frames from camera in background"""
    global latest_frame
    
    while True:
        try:
            print("🎥 Attempting to connect to camera...")
            
            # Create camera capture with timeout
            cap = cv2.VideoCapture(RTSP_URL, cv2.CAP_FFMPEG)
            
            # Set connection timeout (in milliseconds)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            
            # Check if camera connected successfully
            if not cap.isOpened():
                print("❌ Failed to connect to camera. Retrying in 5 seconds...")
                await asyncio.sleep(5)
                continue
            
            print("✅ Connected to camera! Starting to capture frames...")
            
            frame_count = 0
            while True:
                ret, frame = cap.read()
                
                if not ret:
                    print("⚠️  Lost connection to camera. Reconnecting...")
                    break
                
                # Resize frame to save bandwidth (reduce from 640x480 if too slow)
                frame = cv2.resize(frame, (640, 480))
                
                # Encode to JPEG
                _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                
                async with frame_lock:
                    latest_frame = buffer.tobytes()
                
                frame_count += 1
                if frame_count % 30 == 0:  # Print every 30 frames
                    print(f"📹 Captured {frame_count} frames")
                
                # Small delay to allow other tasks to run
                await asyncio.sleep(0.01)
        
        except Exception as e:
            print(f"❌ Error in capture loop: {e}")
            await asyncio.sleep(5)
        
        finally:
            try:
                cap.release()
            except:
                pass

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events using lifespan"""
    # STARTUP
    global capture_task
    print("🚀 Starting camera relay server...")
    
    # Start camera capture as background task (don't wait for it)
    capture_task = asyncio.create_task(capture_camera())
    
    print("📡 Server is ready!")
    yield
    
    # SHUTDOWN
    print("🛑 Shutting down...")
    if capture_task:
        capture_task.cancel()
        try:
            await capture_task
        except asyncio.CancelledError:
            pass
    print("✅ Shutdown complete")

# Create FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)

@app.get("/stream")
async def stream():
    """Stream video frames as MJPEG"""
    async def frame_generator():
        while True:
            async with frame_lock:
                if latest_frame is not None:
                    yield (b'--frame\r\n'
                           b'Content-Type: image/jpeg\r\n\r\n' + 
                           latest_frame + b'\r\n')
            await asyncio.sleep(0.033)  # ~30 FPS
    
    return StreamingResponse(frame_generator(),
                            media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/")
async def root():
    """Simple HTML page to test stream"""
    return """
    <html>
        <head>
            <title>Camera Stream</title>
            <style>
                body { font-family: Arial; text-align: center; padding: 20px; }
                img { max-width: 100%; border: 2px solid #ccc; }
            </style>
        </head>
        <body>
            <h1>🎥 Camera Stream Live</h1>
            <img src="/stream" width="640" height="480" alt="Loading stream...">
            <p>Status: <span id="status">Loading...</span></p>
            <script>
                document.querySelector('img').addEventListener('load', () => {
                    document.getElementById('status').innerText = '✅ Connected';
                });
                document.querySelector('img').addEventListener('error', () => {
                    document.getElementById('status').innerText = '❌ Connection Lost';
                });
            </script>
        </body>
    </html>
    """

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {
        "status": "ok",
        "camera_frame_available": latest_frame is not None
    }

# For local testing
if __name__ == "__main__":
    import uvicorn
    print("Starting relay server on http://127.0.0.1:8000")
    print("Open http://127.0.0.1:8000/ in your browser")
    uvicorn.run(app, host="0.0.0.0", port=8000)
