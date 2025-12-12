import os
import cv2
import numpy as np
import mediapipe as mp
from pathlib import Path
from datetime import datetime
from typing import Tuple, List, Dict


class MediaPipePoseDetector:
    def __init__(self, min_detection_confidence: float = 0.7, min_tracking_confidence: float = 0.5, device: str = "cpu"):
        self.min_detection_confidence = min_detection_confidence
        self.min_tracking_confidence = min_tracking_confidence
        self.device = device
        
        self.mp_pose = mp.solutions.pose
        self.mp_drawing = mp.solutions.drawing_utils
        self.mp_drawing_styles = mp.solutions.drawing_styles
        
        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            model_complexity=1,
            smooth_landmarks=True,
            enable_segmentation=False,
            smooth_segmentation=False,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence
        )
        
        self.frames_dir = Path("pose_frames")
        self.frames_dir.mkdir(exist_ok=True)
        self.pose_count = 0
        self.last_pose_data = None
        
        print(f"[pose_detector] ✅ MediaPipe Pose initialized (confidence: {min_detection_confidence})")
        print(f"[pose_detector] Pose frames will be saved to: {self.frames_dir.absolute()}")
    
    def _get_keypoint_coords(self, landmarks, h: int, w: int) -> Dict[str, Tuple[int, int]]:
        """Extract keypoint coordinates from landmarks."""
        keypoints = {}
        keypoint_names = [
            "nose", "left_eye_inner", "left_eye", "left_eye_outer",
            "right_eye_inner", "right_eye", "right_eye_outer",
            "left_ear", "right_ear",
            "mouth_left", "mouth_right",
            "left_shoulder", "right_shoulder",
            "left_elbow", "right_elbow",
            "left_wrist", "right_wrist",
            "left_pinky", "right_pinky",
            "left_index", "right_index",
            "left_thumb", "right_thumb",
            "left_hip", "right_hip",
            "left_knee", "right_knee",
            "left_ankle", "right_ankle",
            "left_heel", "right_heel",
            "left_foot_index", "right_foot_index"
        ]
        
        for idx, landmark in enumerate(landmarks):
            if idx < len(keypoint_names):
                x = int(landmark.x * w)
                y = int(landmark.y * h)
                keypoints[keypoint_names[idx]] = (x, y, landmark.visibility)
        
        return keypoints
    
    def _classify_person_state(self, keypoints: Dict) -> Tuple[str, float]:
        """
        Classify person state: 'fallen', 'sitting', 'standing', or 'walking'
        
        Returns:
            (state, confidence) - state is one of the above, confidence is 0.0-1.0
        """
        try:
            left_hip = keypoints.get("left_hip", (0, 0, 0))
            right_hip = keypoints.get("right_hip", (0, 0, 0))
            left_knee = keypoints.get("left_knee", (0, 0, 0))
            right_knee = keypoints.get("right_knee", (0, 0, 0))
            left_ankle = keypoints.get("left_ankle", (0, 0, 0))
            right_ankle = keypoints.get("right_ankle", (0, 0, 0))
            left_shoulder = keypoints.get("left_shoulder", (0, 0, 0))
            right_shoulder = keypoints.get("right_shoulder", (0, 0, 0))
            nose = keypoints.get("nose", (0, 0, 0))
            
            min_visibility = 0.5
            if all(kp[2] < min_visibility for kp in [left_hip, right_hip, left_knee, right_knee]):
                return "unknown", 0.0
            
            hip_y = (left_hip[1] + right_hip[1]) / 2
            shoulder_y = (left_shoulder[1] + right_shoulder[1]) / 2
            knee_y = (left_knee[1] + right_knee[1]) / 2
            ankle_y = (left_ankle[1] + right_ankle[1]) / 2
            nose_y = nose[1]
            
            hip_knee_dist = abs(hip_y - knee_y)
            knee_ankle_dist = abs(knee_y - ankle_y)
            shoulder_hip_dist = abs(shoulder_y - hip_y)
            
            if hip_knee_dist < 30 and knee_ankle_dist < 30:
                if nose_y > hip_y:
                    return "fallen", 0.95
            
            if hip_knee_dist < 50 and knee_ankle_dist < 50:
                if shoulder_hip_dist > 100:
                    return "sitting", 0.85
            
            if hip_knee_dist > 80 and knee_ankle_dist > 80:
                if shoulder_hip_dist > 150:
                    return "standing", 0.90
            
            if hip_knee_dist > 80 and knee_ankle_dist > 80:
                if shoulder_hip_dist > 150:
                    return "walking", 0.85
            
            return "standing", 0.70
        except Exception as e:
            print(f"[pose_detector] ⚠️ Error in state classification: {e}")
            return "unknown", 0.0
    
    def _is_person_fallen(self, keypoints: Dict) -> bool:
        """Detect if person is in a fallen position based on keypoint analysis."""
        state, _ = self._classify_person_state(keypoints)
        return state == "fallen"
    
    def _draw_pose_landmarks(self, image: np.ndarray, keypoints: Dict, h: int, w: int) -> np.ndarray:
        """Draw pose landmarks on image."""
        output = image.copy()
        
        connections = [
            ("left_shoulder", "right_shoulder"),
            ("left_shoulder", "left_elbow"),
            ("left_elbow", "left_wrist"),
            ("right_shoulder", "right_elbow"),
            ("right_elbow", "right_wrist"),
            ("left_shoulder", "left_hip"),
            ("right_shoulder", "right_hip"),
            ("left_hip", "right_hip"),
            ("left_hip", "left_knee"),
            ("left_knee", "left_ankle"),
            ("right_hip", "right_knee"),
            ("right_knee", "right_ankle"),
        ]
        
        for start, end in connections:
            if start in keypoints and end in keypoints:
                start_pos = keypoints[start][:2]
                end_pos = keypoints[end][:2]
                if start_pos[0] > 0 and end_pos[0] > 0:
                    cv2.line(output, start_pos, end_pos, (0, 255, 0), 2)
        
        for name, (x, y, visibility) in keypoints.items():
            if visibility > 0.5 and x > 0 and y > 0:
                color = (0, 255, 0) if visibility > 0.7 else (0, 165, 255)
                cv2.circle(output, (x, y), 4, color, -1)
        
        return output
    
    def _draw_status_box(self, image: np.ndarray, state: str, confidence: float, h: int, w: int) -> np.ndarray:
        """Draw colored status box with state and confidence."""
        output = image.copy()
        
        state_colors = {
            "fallen": (0, 0, 255),      # Red
            "sitting": (0, 165, 255),   # Orange
            "standing": (0, 255, 0),    # Green
            "walking": (0, 255, 255),   # Yellow
            "unknown": (128, 128, 128)  # Gray
        }
        
        state_emojis = {
            "fallen": "🚨 FALLEN",
            "sitting": "🪑 SITTING",
            "standing": "🧍 STANDING",
            "walking": "🚶 WALKING",
            "unknown": "❓ UNKNOWN"
        }
        
        color = state_colors.get(state, (128, 128, 128))
        emoji_text = state_emojis.get(state, "UNKNOWN")
        status_text = f"{emoji_text} ({confidence:.0%})"
        
        box_height = 80
        cv2.rectangle(output, (10, 10), (w-10, box_height), color, -1)
        cv2.rectangle(output, (10, 10), (w-10, box_height), (255, 255, 255), 3)
        
        cv2.putText(output, status_text, (30, 55), 
                   cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
        
        return output
    
    def annotate(self, bgr: np.ndarray) -> Tuple[np.ndarray, bool, Dict]:
        """
        Process frame with pose detection and state classification.
        
        Returns:
            (annotated_frame, is_fallen, pose_data)
        """
        if bgr is None or bgr.size == 0:
            return bgr, False, {}
        
        try:
            h, w = bgr.shape[:2]
            output = bgr.copy()
            
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            results = self.pose.process(rgb)
            
            is_fallen = False
            pose_data = {
                "detected": False,
                "keypoints": {},
                "state": "unknown",
                "confidence": 0.0,
                "fallen": False
            }
            
            if results.landmarks:
                keypoints = self._get_keypoint_coords(results.landmarks, h, w)
                pose_data["detected"] = True
                pose_data["keypoints"] = keypoints
                
                output = self._draw_pose_landmarks(output, keypoints, h, w)
                
                state, confidence = self._classify_person_state(keypoints)
                pose_data["state"] = state
                pose_data["confidence"] = confidence
                
                is_fallen = (state == "fallen")
                pose_data["fallen"] = is_fallen
                
                output = self._draw_status_box(output, state, confidence, h, w)
                
                if is_fallen:
                    self.pose_count += 1
                    print(f"[pose_detector] 🚨 PERSON FALLEN! Count: {self.pose_count}")
                    
                    try:
                        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
                        filename = self.frames_dir / f"fallen_{self.pose_count}_{timestamp}.jpg"
                        cv2.imwrite(str(filename), output)
                        print(f"[pose_detector] 💾 Frame saved: {filename}")
                    except Exception as e:
                        print(f"[pose_detector] ⚠️ Failed to save frame: {e}")
                else:
                    state_logs = {
                        "standing": "[pose_detector] 🧍 Person standing",
                        "sitting": "[pose_detector] 🪑 Person sitting",
                        "walking": "[pose_detector] 🚶 Person walking",
                        "unknown": "[pose_detector] ❓ Unknown state"
                    }
                    if state in state_logs:
                        print(f"{state_logs[state]} (confidence: {confidence:.0%})")
                
                self.last_pose_data = pose_data
            else:
                output = self._draw_status_box(output, "unknown", 0.0, h, w)
            
            return output, is_fallen, pose_data
        
        except Exception as e:
            print(f"[pose_detector] ❌ Error in annotate: {e}")
            import traceback
            traceback.print_exc()
            return bgr, False, {}
    
    def close(self):
        """Clean up resources."""
        if self.pose:
            self.pose.close()
        print("[pose_detector] ✅ Pose detector closed")


def load_pose_detector_from_env():
    """Load pose detector from environment variables."""
    enable_pose = os.getenv("ENABLE_POSE_DETECTION", "0").strip().lower() in ("1", "true", "yes", "on")
    
    if not enable_pose:
        return None
    
    try:
        min_detection_conf = float(os.getenv("POSE_DETECTION_CONF", "0.7"))
        min_tracking_conf = float(os.getenv("POSE_TRACKING_CONF", "0.5"))
        device = os.getenv("POSE_DEVICE", "cpu")
        
        detector = MediaPipePoseDetector(
            min_detection_confidence=min_detection_conf,
            min_tracking_confidence=min_tracking_conf,
            device=device
        )
        return detector
    except Exception as e:
        print(f"[pose_detector] ❌ Error loading pose detector: {e}")
        return None
