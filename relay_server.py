from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import cv2
import asyncio

app = FastAPI()

# Your camera's RTSP URL (will come from phone)
RTSP_URL = "rtsp://username:password@192.168.1.100:554/stream"

# Store the latest frame
latest_frame = None
frame_lock = asyncio.Lock()

async def capture_camera():
    """Continuously capture frames from camera"""
    global latest_frame
    
    cap = cv2.VideoCapture(RTSP_URL)
    
    while True:
        ret, frame = cap.read()
        if ret:
            # Resize to save bandwidth
            frame = cv2.resize(frame, (640, 480))
            
            # Encode to JPEG
            _, buffer = cv2.imencode('.jpg', frame)
            
            async with frame_lock:
                latest_frame = buffer.tobytes()
        
        await asyncio.sleep(0.033)  # ~30 FPS

@app.on_event("startup")
async def startup_event():
    """Start camera capture on server startup"""
    asyncio.create_task(capture_camera())

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
            await asyncio.sleep(0.033)
    
    return StreamingResponse(frame_generator(),
                            media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/")
async def root():
    """Simple HTML page to test stream"""
    return """
    <html>
        <body>
            <h1>Camera Stream</h1>
            <img src="/stream" width="640" height="480">
        </body>
    </html>
    """

# For deployment (Render needs this)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
