import os
import cv2
import torch
import numpy as np
from pathlib import Path
from datetime import datetime


class YoloV5Detector:
    def __init__(self, weights_path: str, conf: float = 0.5, device: str = "cpu"):
        self.device = torch.device(device)
        print(f"[detector] Loading YOLOv5 from {weights_path}")
        self.model = torch.hub.load("ultralytics/yolov5", "custom", path=weights_path, force_reload=False)
        self.model.to(self.device)
        self.model.eval()
        self.model.conf = conf
        self.names = self.model.names
        self.fall_detected = False
        self.detection_count = 0
        
        self.frames_dir = Path("detected_frames")
        self.frames_dir.mkdir(exist_ok=True)
        print(f"[detector] ✅ Model loaded. Classes: {self.names}")
        print(f"[detector] Fall frames will be saved to: {self.frames_dir.absolute()}")

    def annotate(self, bgr: np.ndarray) -> tuple:
        if bgr is None or bgr.size == 0:
            return bgr, False
        try:
            h, w = bgr.shape[:2]
            output = bgr.copy()
            results = self.model(bgr)
            
            det = results.xyxy[0].cpu().numpy() if results.xyxy[0] is not None else np.array([])
            fall_detected = False
            
            if len(det) > 0:
                self.detection_count += 1
                print(f"[detector] 🚨 FALL DETECTED! Count: {self.detection_count}")
                fall_detected = True
                
                for idx, row in enumerate(det):
                    if len(row) < 6:
                        continue
                    x1, y1, x2, y2, conf, cls_id = row[:6]
                    x1, y1, x2, y2 = int(max(0, x1)), int(max(0, y1)), int(min(w, x2)), int(min(h, y2))
                    
                    cv2.rectangle(output, (x1, y1), (x2, y2), (0, 0, 255), 4)
                    label = f"{self.names[int(cls_id)]} {conf:.2f}"
                    (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 1.0, 2)
                    cv2.rectangle(output, (x1, y1 - text_h - 12), (x1 + text_w + 8, y1), (0, 0, 255), -1)
                    cv2.putText(output, label, (x1 + 4, y1 - 6), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
                
                cv2.putText(output, "FALL DETECTED!", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 2.0, (0, 0, 255), 4)
                
                try:
                    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                    filename = self.frames_dir / f"fall_{self.detection_count}_{timestamp}.jpg"
                    cv2.imwrite(str(filename), output)
                    print(f"[detector] 💾 Frame saved: {filename}")
                except Exception as e:
                    print(f"[detector] ⚠️ Failed to save frame: {e}")
            
            self.fall_detected = fall_detected
            return output, fall_detected
        except Exception as e:
            print(f"[detector] ❌ Error in annotate: {e}")
            import traceback
            traceback.print_exc()
            return bgr, False


def load_detector_from_env():
    weights = os.getenv("YOLOV5_WEIGHTS")
    if not weights:
        return None
    conf = float(os.getenv("DETECTION_CONF", "0.5"))
    device = os.getenv("DETECTION_DEVICE", "cpu")
    return YoloV5Detector(weights, conf=conf, device=device)

