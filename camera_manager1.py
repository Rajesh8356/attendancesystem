# camera_manager.py - Removed detection line drawing (camera has its own line)

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
    """Manages multiple IP cameras for the attendance system with smooth RTSP streaming"""
    
    def __init__(self, app=None):
        self.cameras = {}
        self.camera_threads = {}
        self.frame_queues = {}
        self.rtsp_urls = {}
        self.is_running = True
        self.lock = threading.Lock()
        self.fps_target = 30
        self.app = app
        self.last_valid_frame = {}
        
        # Line detection tracking
        self.tracked_faces = {}
        self.crossed_students = {}
        self.line_positions = {}  # Store line position for detection logic
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
        self.known_face_ids = {}
        
        logger.info("Camera manager initialized")
        
    def load_known_faces(self):
        """Load known faces from database"""
        if not self.app:
            logger.error("No app context available to load faces")
            return
            
        with self.app.app_context():
            try:
                from models import Student
                import face_recognition
                
                self.known_face_encodings = []
                self.known_face_names = []
                
                students = Student.query.filter_by(is_active=True).all()
                
                for student in students:
                    if student.face_encodings:
                        try:
                            import json
                            encodings = json.loads(student.face_encodings)
                            if encodings and len(encodings) > 0:
                                if isinstance(encodings[0], list):
                                    encoding_array = np.array(encodings[0], dtype=np.float64)
                                    self.known_face_encodings.append(encoding_array)
                                    self.known_face_names.append(f"{student.id}_{student.user.full_name if student.user else 'Unknown'}")
                        except Exception as e:
                            logger.error(f"Error parsing face encoding: {str(e)}")
                
                logger.info(f"Loaded {len(self.known_face_names)} known faces")
                
            except Exception as e:
                logger.error(f"Error loading known faces: {str(e)}")
    
    def set_active_session(self, session_id, start_time, end_time):
        """Set the active attendance session"""
        self.active_session = session_id
        self.session_start_time = start_time
        self.session_end_time = end_time
        self.marked_absent = False
        logger.info(f"Active session set: {session_id}")
    
    def clear_session(self):
        """Clear the active session"""
        self.active_session = None
        self.session_start_time = None
        self.session_end_time = None
        self.marked_absent = False
        logger.info("Active session cleared")
    
    def is_within_session(self):
        """Check if current time is within active session"""
        if not self.active_session or not self.session_start_time or not self.session_end_time:
            return False
        
        now = datetime.now()
        current_time = now.time()
        
        if self.session_start_time <= current_time <= self.session_end_time:
            return True
        return False
    
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
            
            # Initialize tracking
            self.tracked_faces[camera_id] = {}
            self.crossed_students[camera_id] = {}
            self.crossing_history[camera_id] = []
            self.line_positions[camera_id] = 240  # Store for detection logic
            self.detection_data[camera_id] = []
            
            self.frame_queues[camera_id] = queue.Queue(maxsize=2)
            
            print(f"✅ Camera {name} added")
            return True
    
    def set_line_position(self, camera_id, line_y):
        """Set the detection line position for line crossing logic"""
        with self.lock:
            self.line_positions[camera_id] = line_y
            return True
    
    def connect_camera(self, camera_id):
        """Connect to a specific camera using smooth RTSP streaming"""
        with self.lock:
            if camera_id not in self.rtsp_urls:
                logger.error(f"Camera {camera_id} not found")
                return False
        
        def camera_worker(cam_id):
            """Worker thread for camera capture"""
            retry_count = 0
            max_retries = 3
            frame_count = 0
            fps_time = time.time()
            process_this_frame = True
            consecutive_failures = 0
            
            while self.is_running:
                with self.lock:
                    if cam_id not in self.cameras or cam_id not in self.rtsp_urls:
                        break
                    url = self.rtsp_urls[cam_id]
                    line_y = self.line_positions.get(cam_id, 240)
                
                try:
                    # Force FFmpeg backend for better RTSP
                    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
                    
                    # Reduce buffer to avoid lag
                    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    
                    # Set resolution
                    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                    
                    if not cap.isOpened():
                        logger.error(f"Failed to connect to camera {cam_id}")
                        with self.lock:
                            if cam_id in self.cameras:
                                self.cameras[cam_id]['status'] = 'disconnected'
                        
                        retry_count += 1
                        if retry_count >= max_retries:
                            break
                        time.sleep(2)
                        continue
                    
                    # Connection successful
                    retry_count = 0
                    consecutive_failures = 0
                    with self.lock:
                        if cam_id in self.cameras:
                            self.cameras[cam_id]['status'] = 'connected'
                            self.cameras[cam_id]['last_seen'] = datetime.now()
                    
                    frame_count = 0
                    fps_time = time.time()
                    current_faces = {}
                    
                    # Load faces
                    if len(self.known_face_names) == 0 and self.app:
                        self.load_known_faces()
                    
                    while self.is_running:
                        with self.lock:
                            if cam_id not in self.cameras:
                                cap.release()
                                return
                            line_y = self.line_positions.get(cam_id, 240)
                        
                        # Grab latest frame (skip old buffered frames)
                        cap.grab()
                        ret, frame = cap.read()
                        
                        if not ret:
                            consecutive_failures += 1
                            if consecutive_failures > 5:
                                logger.warning(f"Frame drop from camera {cam_id}, reconnecting...")
                                break
                            continue
                        
                        consecutive_failures = 0
                        
                        # Ensure consistent frame size
                        frame = cv2.resize(frame, (640, 480))
                        
                        # Store last valid frame
                        with self.lock:
                            self.last_valid_frame[cam_id] = frame.copy()
                        
                        # Process every other frame for face detection
                        if process_this_frame:
                            try:
                                import face_recognition
                                
                                # Resize for faster processing
                                small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
                                rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
                                
                                # Find faces
                                face_locations = face_recognition.face_locations(rgb_small_frame)
                                face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
                                
                                current_faces = {}
                                detections = []
                                
                                for i, (top, right, bottom, left) in enumerate(face_locations):
                                    # Scale back to original size
                                    top *= 2
                                    right *= 2
                                    bottom *= 2
                                    left *= 2
                                    
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
                                            
                                            # Check for line crossing using stored line position
                                            if face_id in self.tracked_faces.get(cam_id, {}):
                                                prev_y = self.tracked_faces[cam_id][face_id]['y_pos']
                                                prev_time = self.tracked_faces[cam_id][face_id].get('last_seen', 0)
                                                time_diff = time.time() - prev_time
                                                
                                                if prev_y < line_y and face_center_y >= line_y and time_diff > 3:
                                                    if self.is_within_session():
                                                        if student_id not in self.crossed_students.get(cam_id, {}):
                                                            self.mark_attendance(cam_id, student_id, student_name, 'in')
                                                            self.crossed_students[cam_id][student_id] = datetime.now()
                                                            self.crossing_history[cam_id].append({
                                                                'student_id': student_id,
                                                                'student_name': student_name,
                                                                'timestamp': datetime.now(),
                                                                'direction': 'in'
                                                            })
                                                            with self.lock:
                                                                if cam_id in self.cameras:
                                                                    self.cameras[cam_id]['crossed_today'] = len(self.crossed_students[cam_id])
                                                
                                                elif prev_y > line_y and face_center_y <= line_y and time_diff > 3:
                                                    if self.is_within_session():
                                                        if student_id not in self.crossed_students.get(cam_id, {}):
                                                            self.mark_attendance(cam_id, student_id, student_name, 'out')
                                                            self.crossed_students[cam_id][student_id] = datetime.now()
                                                            self.crossing_history[cam_id].append({
                                                                'student_id': student_id,
                                                                'student_name': student_name,
                                                                'timestamp': datetime.now(),
                                                                'direction': 'out'
                                                            })
                                                            with self.lock:
                                                                if cam_id in self.cameras:
                                                                    self.cameras[cam_id]['crossed_today'] = len(self.crossed_students[cam_id])
                                    
                            except Exception as e:
                                logger.error(f"Face detection error: {str(e)}")
                            
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
                        
                        process_this_frame = not process_this_frame
                        
                        # Update FPS
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
                        
                        # Clear queue and add latest frame
                        with self.lock:
                            if cam_id in self.frame_queues:
                                while not self.frame_queues[cam_id].empty():
                                    try:
                                        self.frame_queues[cam_id].get_nowait()
                                    except queue.Empty:
                                        break
                                self.frame_queues[cam_id].put(frame)
                    
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
        
        # Start camera thread
        thread = threading.Thread(target=camera_worker, args=(camera_id,), daemon=True)
        thread.start()
        
        with self.lock:
            self.camera_threads[camera_id] = thread
        
        return True
    
    def mark_attendance(self, camera_id, student_id, student_name, direction):
        """Mark attendance"""
        try:
            from app import db, socketio
            from models import Attendance
            from datetime import datetime
            
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
                        'camera_id': camera_id,
                        'confidence': 95.0
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
    
    def get_frame_jpeg(self, camera_id):
        """Get frame as JPEG with detection boxes only (no detection line drawn)"""
        frame = self.get_frame(camera_id)
        
        # If no frame, return last valid frame
        if frame is None:
            with self.lock:
                frame = self.last_valid_frame.get(camera_id)
            if frame is None:
                return None
        
        with self.lock:
            detections = self.detection_data.get(camera_id, [])
            crossed_count = len(self.crossed_students.get(camera_id, {}))
            fps = self.cameras.get(camera_id, {}).get('fps', 0)
        
        # Draw detection boxes only (no detection line - camera has its own)
        for detection in detections:
            rect = detection['rectangle']
            student = detection['student']
            
            # Green box for recognized faces
            color = (0, 255, 0)
            
            cv2.rectangle(frame, 
                         (rect['left'], rect['top']), 
                         (rect['left'] + rect['width'], rect['top'] + rect['height']), 
                         color, 2)
            
            # Draw label with student name
            label = f"{student['name']}"
            cv2.putText(frame, label, 
                       (rect['left'] + 5, rect['top'] - 10), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
        
        # Add session info overlay
        if self.is_within_session():
            session_color = (0, 255, 0)
            session_text = "SESSION ACTIVE"
        else:
            session_color = (0, 0, 255)
            session_text = "NO ACTIVE SESSION"
        
        cv2.putText(frame, session_text, (10, 30), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, session_color, 2)
        
        # Add info overlay
        overlay = frame.copy()
        cv2.rectangle(overlay, (10, frame.shape[0] - 70), (280, frame.shape[0] - 10), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)
        
        cv2.putText(frame, f"TIME: {datetime.now().strftime('%H:%M:%S')}", 
                   (20, frame.shape[0] - 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(frame, f"CROSSED: {crossed_count} | DETECTIONS: {len(detections)} | FPS: {fps}", 
                   (20, frame.shape[0] - 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        
        # Compress JPEG
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 70]
        ret, jpeg = cv2.imencode('.jpg', frame, encode_param)
        if ret:
            return jpeg.tobytes()
        return None
    
    def get_camera_info(self, camera_id):
        """Get camera info"""
        with self.lock:
            if camera_id in self.cameras:
                info = self.cameras[camera_id].copy()
                info['crossed_today'] = len(self.crossed_students.get(camera_id, {}))
                return info
        return None
    
    def get_all_cameras(self):
        """Get all cameras info"""
        with self.lock:
            cameras = []
            for cam_id, cam in self.cameras.items():
                cam_info = cam.copy()
                cam_info['crossed_today'] = len(self.crossed_students.get(cam_id, {}))
                cameras.append(cam_info)
            return cameras
    
    def remove_camera(self, camera_id):
        """Remove camera"""
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
        """Shutdown all cameras"""
        self.is_running = False
        with self.lock:
            threads = list(self.camera_threads.values())
        for thread in threads:
            thread.join(timeout=2)