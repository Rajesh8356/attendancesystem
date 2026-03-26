# camera_manager.py - Crystal clear quality with smooth performance

import cv2
import threading
import queue
import time
import logging
from datetime import datetime
import urllib.parse
import numpy as np
from sqlalchemy import func

logger = logging.getLogger(__name__)

class CameraManager:
    """Manages multiple IP cameras for the attendance system"""
    
    def __init__(self, app=None):
        self.cameras = {}
        self.camera_threads = {}
        self.detection_threads = {}
        self.frame_queues = {}
        self.rtsp_urls = {}
        self.is_running = True
        self.lock = threading.Lock()
        self.app = app
        self.last_valid_frame = {}
        self.face_cache = {}
        
        # Detection tracking
        self.tracked_faces = {}
        self.crossed_students = {}
        self.line_positions = {}
        self.detection_data = {}
        self.crossing_history = {}
        
        # Session tracking
        self.active_session = None
        self.session_start_time = None
        self.session_end_time = None
        self.marked_absent = False
        
        # Known faces
        self.known_face_encodings = []
        self.known_face_names = []
        self.known_face_students = {}
        self.known_face_images = {}
        
        logger.info("Camera manager initialized")
        
    def load_known_faces(self):
        """Load known faces from database"""
        if not self.app:
            return
            
        with self.app.app_context():
            try:
                from models import Student
                import face_recognition
                import json
                import os
                
                self.known_face_encodings = []
                self.known_face_names = []
                self.known_face_students = {}
                self.known_face_images = {}
                
                students = Student.query.filter_by(is_active=True).all()
                
                for student in students:
                    if student.face_encodings:
                        try:
                            encodings = json.loads(student.face_encodings)
                            if encodings and len(encodings) > 0:
                                if isinstance(encodings[0], list):
                                    encoding_array = np.array(encodings[0], dtype=np.float64)
                                    self.known_face_encodings.append(encoding_array)
                                    self.known_face_names.append(f"{student.id}_{student.user.full_name if student.user else 'Unknown'}")
                                    self.known_face_students[student.id] = student
                                    
                                    # Load face image for display
                                    if student.face_images:
                                        images = json.loads(student.face_images)
                                        if images and len(images) > 0:
                                            img_path = os.path.join(self.app.config['UPLOAD_FOLDER'], images[0])
                                            if os.path.exists(img_path):
                                                img = cv2.imread(img_path)
                                                if img is not None:
                                                    self.known_face_images[student.id] = img
                        except Exception as e:
                            logger.error(f"Error parsing face encoding: {str(e)}")
                
                logger.info(f"Loaded {len(self.known_face_names)} known faces")
                
            except Exception as e:
                logger.error(f"Error loading known faces: {str(e)}")
    
    def set_active_session(self, session_id, start_time, end_time):
        self.active_session = session_id
        self.session_start_time = start_time
        self.session_end_time = end_time
        self.marked_absent = False
    
    def clear_session(self):
        self.active_session = None
        self.session_start_time = None
        self.session_end_time = None
        self.marked_absent = False
    
    def is_within_session(self):
        if not self.active_session or not self.session_start_time or not self.session_end_time:
            return False
        now = datetime.now()
        current_time = now.time()
        return self.session_start_time <= current_time <= self.session_end_time
    
    def add_camera(self, camera_id, name, ip_address, username=None, password=None, port=554, stream_path='/stream1'):
        """Add a new IP camera"""
        with self.lock:
            if username and password:
                encoded_username = urllib.parse.quote(username, safe='')
                encoded_password = urllib.parse.quote(password, safe='')
                rtsp_url = f"rtsp://{encoded_username}:{encoded_password}@{ip_address}:{port}{stream_path}"
            else:
                rtsp_url = f"rtsp://{ip_address}:{port}{stream_path}"
            
            self.rtsp_urls[camera_id] = rtsp_url
            self.last_valid_frame[camera_id] = None
            
            self.cameras[camera_id] = {
                'id': camera_id,
                'name': name,
                'ip': ip_address,
                'status': 'disconnected',
                'last_seen': None,
                'fps': 0,
                'detection_count': 0,
                'crossed_today': 0,
                'direction': 'both'
            }
            
            self.tracked_faces[camera_id] = {}
            self.crossed_students[camera_id] = {}
            self.crossing_history[camera_id] = []
            self.line_positions[camera_id] = 240
            self.detection_data[camera_id] = []
            
            self.frame_queues[camera_id] = queue.Queue(maxsize=2)
            
            print(f"✅ Camera {name} added")
            return True
    
    def set_line_position(self, camera_id, line_y):
        with self.lock:
            self.line_positions[camera_id] = line_y
            return True
    
    def connect_camera(self, camera_id):
        """Connect to a specific camera"""
        with self.lock:
            if camera_id not in self.rtsp_urls:
                logger.error(f"Camera {camera_id} not found")
                return False
        
        def camera_worker(cam_id):
            """Worker thread for camera capture only"""
            retry_count = 0
            max_retries = 5
            frame_count = 0
            fps_time = time.time()
            consecutive_failures = 0
            last_frame = None
            
            while self.is_running:
                with self.lock:
                    if cam_id not in self.cameras or cam_id not in self.rtsp_urls:
                        break
                    url = self.rtsp_urls[cam_id]
                
                try:
                    # Open video capture with FFmpeg backend
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    cap.set(cv2.CAP_PROP_FPS, 30)
                    
                    if not cap.isOpened():
                        with self.lock:
                            if cam_id in self.cameras:
                                self.cameras[cam_id]['status'] = 'disconnected'
                        retry_count += 1
                        if retry_count >= max_retries:
                            break
                        time.sleep(2)
                        continue
                    
                    retry_count = 0
                    consecutive_failures = 0
                    with self.lock:
                        if cam_id in self.cameras:
                            self.cameras[cam_id]['status'] = 'connected'
                            self.cameras[cam_id]['last_seen'] = datetime.now()
                    
                    frame_count = 0
                    fps_time = time.time()
                    
                    while self.is_running:
                        with self.lock:
                            if cam_id not in self.cameras:
                                cap.release()
                                return
                        
                        # Read frame directly
                        ret, frame = cap.read()
                        
                        if not ret:
                            consecutive_failures += 1
                            if consecutive_failures > 10:
                                logger.warning(f"Camera {cam_id} lost connection")
                                break
                            time.sleep(0.05)
                            continue
                        
                        consecutive_failures = 0
                        
                        # Resize to consistent size
                        frame = cv2.resize(frame, (640, 480))
                        last_frame = frame.copy()
                        
                        # Store frame
                        with self.lock:
                            if cam_id in self.frame_queues:
                                while not self.frame_queues[cam_id].empty():
                                    try:
                                        self.frame_queues[cam_id].get_nowait()
                                    except queue.Empty:
                                        break
                                self.frame_queues[cam_id].put(frame.copy())
                                self.last_valid_frame[cam_id] = frame.copy()
                        
                        frame_count += 1
                        if time.time() - fps_time >= 1.0:
                            with self.lock:
                                if cam_id in self.cameras:
                                    self.cameras[cam_id]['fps'] = frame_count
                            frame_count = 0
                            fps_time = time.time()
                        
                        with self.lock:
                            if cam_id in self.cameras:
                                self.cameras[cam_id]['last_seen'] = datetime.now()
                        
                        time.sleep(0.033)  # ~30 FPS max
                    
                    cap.release()
                    
                except Exception as e:
                    logger.error(f"Error in camera {cam_id}: {str(e)}")
                    retry_count += 1
                    if retry_count >= max_retries:
                        break
                    time.sleep(2)
            
            with self.lock:
                if cam_id in self.cameras:
                    self.cameras[cam_id]['status'] = 'offline'
        
        # Load faces
        if len(self.known_face_names) == 0 and self.app:
            self.load_known_faces()
        
        # Start camera thread
        thread = threading.Thread(target=camera_worker, args=(camera_id,), daemon=True)
        thread.start()
        
        with self.lock:
            self.camera_threads[camera_id] = thread
        
        return True
        
        def detection_worker(cam_id):
            """Separate thread for face detection (runs slower but doesn't affect video)"""
            process_this_frame = True
            frame_skip_count = 0
            DETECTION_INTERVAL = 3  # Process every 3rd frame for detection
            
            while self.is_running:
                with self.lock:
                    if cam_id not in self.cameras:
                        break
                    line_y = self.line_positions.get(cam_id, 240)
                    if cam_id in self.frame_queues and not self.frame_queues[cam_id].empty():
                        frame = self.frame_queues[cam_id].get()
                    else:
                        time.sleep(0.01)
                        continue
                
                if frame is None:
                    continue
                
                # Only run detection every few frames to save CPU
                frame_skip_count += 1
                if frame_skip_count < DETECTION_INTERVAL:
                    continue
                frame_skip_count = 0
                
                try:
                    import face_recognition
                    
                    # Convert to RGB for face_recognition
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    
                    # For detection, we resize to 0.5x for speed
                    # This doesn't affect the displayed video quality
                    detection_frame = cv2.resize(rgb_frame, (0, 0), fx=0.5, fy=0.5)
                    face_locations = face_recognition.face_locations(detection_frame)
                    face_encodings = face_recognition.face_encodings(detection_frame, face_locations)
                    
                    current_faces = {}
                    detections = []
                    
                    for i, (top, right, bottom, left) in enumerate(face_locations):
                        # Scale back to original size
                        top = int(top * 2)
                        right = int(right * 2)
                        bottom = int(bottom * 2)
                        left = int(left * 2)
                        
                        face_center_y = (top + bottom) // 2
                        
                        if i < len(face_encodings) and len(self.known_face_encodings) > 0:
                            matches = face_recognition.compare_faces(
                                self.known_face_encodings, 
                                face_encodings[i], 
                                tolerance=0.6
                            )
                            
                            if True in matches:
                                match_idx = matches.index(True)
                                student_key = self.known_face_names[match_idx]
                                student_id, student_name = student_key.split('_', 1)
                                student_id = int(student_id)
                                
                                face_id = f"{student_id}_{cam_id}"
                                current_faces[face_id] = {
                                    'student_id': student_id,
                                    'student_name': student_name,
                                    'y_pos': face_center_y,
                                    'last_seen': time.time()
                                }
                                
                                detections.append({
                                    'rectangle': {
                                        'left': left,
                                        'top': top,
                                        'width': right - left,
                                        'height': bottom - top
                                    },
                                    'student': {
                                        'id': student_id,
                                        'name': student_name
                                    },
                                    'confidence': 95.0,
                                    'center_y': face_center_y
                                })
                                
                                # Check line crossing
                                if face_id in self.tracked_faces.get(cam_id, {}):
                                    prev_y = self.tracked_faces[cam_id][face_id]['y_pos']
                                    prev_time = self.tracked_faces[cam_id][face_id].get('last_seen', 0)
                                    time_diff = time.time() - prev_time
                                    
                                    if prev_y < line_y and face_center_y >= line_y and time_diff > 3:
                                        if self.is_within_session():
                                            if student_id not in self.crossed_students.get(cam_id, {}):
                                                self.mark_attendance(cam_id, student_id, student_name, 'in')
                                                self.crossed_students[cam_id][student_id] = datetime.now()
                                                with self.lock:
                                                    if cam_id in self.cameras:
                                                        self.cameras[cam_id]['crossed_today'] = len(self.crossed_students[cam_id])
                                    
                                    elif prev_y > line_y and face_center_y <= line_y and time_diff > 3:
                                        if self.is_within_session():
                                            if student_id not in self.crossed_students.get(cam_id, {}):
                                                self.mark_attendance(cam_id, student_id, student_name, 'out')
                                                self.crossed_students[cam_id][student_id] = datetime.now()
                                                with self.lock:
                                                    if cam_id in self.cameras:
                                                        self.cameras[cam_id]['crossed_today'] = len(self.crossed_students[cam_id])
                    
                    # Update tracked faces
                    self.tracked_faces[cam_id] = current_faces
                    
                    current_time = time.time()
                    self.tracked_faces[cam_id] = {
                        face_id: face_data 
                        for face_id, face_data in current_faces.items() 
                        if current_time - face_data.get('last_seen', 0) < 2
                    }
                    
                    with self.lock:
                        self.detection_data[cam_id] = detections
                        self.last_valid_frame[cam_id] = frame.copy()
                    
                except Exception as e:
                    logger.error(f"Detection error: {str(e)}")
        
        # Load faces
        if len(self.known_face_names) == 0 and self.app:
            self.load_known_faces()
        
        # Start threads
        thread = threading.Thread(target=camera_worker, args=(camera_id,), daemon=True)
        thread.start()
        detection_thread = threading.Thread(target=detection_worker, args=(camera_id,), daemon=True)
        detection_thread.start()
        
        with self.lock:
            self.camera_threads[camera_id] = thread
            self.detection_threads[camera_id] = detection_thread
        
        return True
    
    def mark_attendance(self, camera_id, student_id, student_name, direction):
        """Mark attendance"""
        try:
            from app import db, socketio
            from models import Attendance
            
            if not self.app:
                return False
            
            with self.app.app_context():
                now = datetime.now()
                
                existing = Attendance.query.filter(
                    Attendance.student_id == student_id,
                    func.date(Attendance.timestamp) == now.date(),
                    Attendance.status == direction
                ).first()
                
                if not existing:
                    attendance = Attendance(
                        student_id=student_id,
                        status=direction,
                        confidence=95.0,
                        camera_id=camera_id,
                        verified=True
                    )
                    db.session.add(attendance)
                    db.session.commit()
                    
                    socketio.emit('attendance_update', {
                        'student_id': student_id,
                        'student_name': student_name,
                        'status': direction,
                        'timestamp': now.isoformat(),
                        'camera_id': camera_id
                    })
                    
                    print(f"✅ ATTENDANCE: {student_name} {direction}")
                    return True
                return False
                    
        except Exception as e:
            logger.error(f"Error marking attendance: {str(e)}")
            return False
    
    def get_frame(self, camera_id):
        """Get the latest frame"""
        with self.lock:
            if camera_id in self.frame_queues and not self.frame_queues[camera_id].empty():
                return self.frame_queues[camera_id].get()
        return None
    
    # In camera_manager.py, fix the get_frame_jpeg function to return proper frames

    def get_frame_jpeg(self, camera_id):
        """Get frame as JPEG with detection boxes"""
        frame = self.get_frame(camera_id)
        
        # If no frame, try to get from last valid frame
        if frame is None:
            with self.lock:
                frame = self.last_valid_frame.get(camera_id)
            if frame is None:
                # Return a blank frame with text instead of None
                blank = np.zeros((360, 480, 3), dtype=np.uint8)
                cv2.putText(blank, "Waiting for camera...", (120, 180), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (100, 100, 100), 2)
                ret, jpeg = cv2.imencode('.jpg', blank)
                if ret:
                    return jpeg.tobytes()
                return None
        
        with self.lock:
            detections = self.detection_data.get(camera_id, [])
            crossed_count = len(self.crossed_students.get(camera_id, {}))
            fps = self.cameras.get(camera_id, {}).get('fps', 0)
            line_y = self.line_positions.get(camera_id, 240)
        
        # Draw detection line
        cv2.line(frame, (0, line_y), (frame.shape[1], line_y), (0, 255, 255), 3)
        cv2.putText(frame, "DETECTION LINE", (10, line_y - 10), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
        
        # Draw detection boxes
        for detection in detections:
            rect = detection['rectangle']
            student = detection['student']
            center_y = detection.get('center_y', 0)
            
            # Color based on position relative to line
            if center_y < line_y:
                color = (255, 255, 0)
                status = 'above'
            else:
                color = (0, 255, 0)
                status = 'active'
            
            # Draw rectangle
            cv2.rectangle(frame, 
                        (rect['left'], rect['top']), 
                        (rect['left'] + rect['width'], rect['top'] + rect['height']), 
                        color, 2)
            
            # Draw label
            label = f"{student['name']} ({status})"
            cv2.putText(frame, label, 
                    (rect['left'] + 5, rect['top'] - 8), 
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        
        # Add session info
        if self.is_within_session():
            session_color = (0, 255, 0)
            session_text = "SESSION ACTIVE"
        else:
            session_color = (0, 0, 255)
            session_text = "SESSION INACTIVE"
        
        cv2.putText(frame, session_text, (10, 30), 
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, session_color, 2)
        
        # Add info overlay
        cv2.putText(frame, f"TIME: {datetime.now().strftime('%H:%M:%S')}", 
                (10, frame.shape[0] - 35), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(frame, f"CROSSED: {crossed_count} | DETECTIONS: {len(detections)} | FPS: {fps}", 
                (10, frame.shape[0] - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        
        # Compress JPEG
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
        ret, jpeg = cv2.imencode('.jpg', frame, encode_param)
        if ret:
            return jpeg.tobytes()
        return None
        
    def get_face_image(self, student_id):
        """Get face image for student"""
        with self.lock:
            if student_id in self.known_face_images:
                img = self.known_face_images[student_id]
                if img is not None:
                    ret, jpeg = cv2.imencode('.jpg', img)
                    if ret:
                        return jpeg.tobytes()
        return None
    
    def get_camera_info(self, camera_id):
        with self.lock:
            if camera_id in self.cameras:
                info = self.cameras[camera_id].copy()
                info['crossed_today'] = len(self.crossed_students.get(camera_id, {}))
                return info
        return None
    
    def get_all_cameras(self):
        with self.lock:
            cameras = []
            for cam_id, cam in self.cameras.items():
                cam_info = cam.copy()
                cam_info['crossed_today'] = len(self.crossed_students.get(cam_id, {}))
                cameras.append(cam_info)
            return cameras
    
    def remove_camera(self, camera_id):
        with self.lock:
            if camera_id in self.cameras:
                del self.cameras[camera_id]
                if camera_id in self.frame_queues:
                    del self.frame_queues[camera_id]
                if camera_id in self.rtsp_urls:
                    del self.rtsp_urls[camera_id]
                if camera_id in self.tracked_faces:
                    del self.tracked_faces[camera_id]
                if camera_id in self.crossed_students:
                    del self.crossed_students[camera_id]
                if camera_id in self.last_valid_frame:
                    del self.last_valid_frame[camera_id]
                return True
        return False
    
    def shutdown(self):
        self.is_running = False
        with self.lock:
            threads = list(self.camera_threads.values())
        for thread in threads:
            thread.join(timeout=2)