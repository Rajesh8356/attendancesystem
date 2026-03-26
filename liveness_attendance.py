# liveness_attendance.py
import cv2
import face_recognition
import numpy as np
import pickle
import os
from datetime import datetime, time as datetime_time, timedelta
import time
import pandas as pd
import mediapipe as mp
from scipy.spatial import distance as dist
import threading
import queue
import json
from flask import current_app
from models import db, Attendance, AttendanceSession, Student, Camera

class LivenessAttendanceFaceRecognition:
    def __init__(self, database_path="face_database.pkl", line_position=0.5):
        self.database_path = database_path
        self.known_face_encodings = []
        self.known_face_ids = []
        self.known_face_names = []  # Store names for display
        self.known_face_student_ids = []  # Store database student IDs
        self.known_face_images = []
        self.line_position = line_position
        self.tracked_faces = {}
        self.next_face_id = 0
        self.current_detected_faces = []
        self.display_duration = 3
        self.last_display_time = {}
        
        # Entry and Exit tracking
        self.entry_times = {}  # student_id -> entry_time
        self.exit_times = {}   # student_id -> exit_time
        self.session_data = []  # Store complete session data with entry/exit
        
        # Dual camera indices
        self.entry_camera_id = None
        self.exit_camera_id = None
        self.entry_camera_index = None
        self.exit_camera_index = None
        self.running = False
        
        # Liveness detection variables
        self.liveness_threshold = 0.3
        self.blink_counter = {}
        self.liveness_passed = set()
        self.liveness_attempts = {}
        
        # Initialize liveness detectors
        self.init_liveness_detectors()
        
        # Attendance tracking
        self.attendance_start_time = None
        self.attendance_end_time = None
        self.attendance_active = False
        self.attendance_marked = set()  # Set of student names who are marked present
        self.attendance_marked_ids = set()  # Set of student IDs who are marked present
        self.attendance_log = []
        
        # Active session
        self.active_session_id = None
        self.active_session_name = None
        
        # Performance optimization
        self.process_this_frame = True
        
        # Load database
        self.load_database()
        
        # Create directories
        for directory in ["registered_faces", "attendance_records", "liveness_fails"]:
            if not os.path.exists(directory):
                os.makedirs(directory)
    
    def init_liveness_detectors(self):
        """Initialize liveness detection models"""
        try:
            self.mp_face_mesh = mp.solutions.face_mesh
            self.face_mesh = self.mp_face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=5,
                min_detection_confidence=0.5,
                min_tracking_confidence=0.5
            )
            
            # Eye landmark indices
            self.LEFT_EYE_INDICES = [33, 133, 157, 158, 159, 160, 161, 173]
            self.RIGHT_EYE_INDICES = [362, 263, 387, 386, 385, 384, 398, 466]
            
            # Blink detection parameters
            self.EYE_AR_THRESH = 0.25
            self.EYE_AR_CONSEC_FRAMES = 2
            
            print("✅ Liveness detectors initialized successfully")
        except Exception as e:
            print(f"⚠️  Could not initialize liveness detectors: {e}")
            self.face_mesh = None
    
    def load_database(self):
        """Load existing face database"""
        if os.path.exists(self.database_path):
            try:
                with open(self.database_path, 'rb') as f:
                    data = pickle.load(f)
                    self.known_face_encodings = data.get('encodings', [])
                    self.known_face_ids = data.get('ids', [])
                    self.known_face_names = data.get('names', [])
                    self.known_face_student_ids = data.get('student_ids', [])
                    self.known_face_images = data.get('images', [])
                print(f"Loaded {len(self.known_face_ids)} faces from database")
            except Exception as e:
                print(f"Database file is corrupted: {e}. Starting fresh.")
        else:
            print("No existing database found. Will create new one.")
    
    def save_database(self):
        """Save face database to file"""
        data = {
            'encodings': self.known_face_encodings,
            'ids': self.known_face_ids,
            'names': self.known_face_names,
            'student_ids': self.known_face_student_ids,
            'images': self.known_face_images
        }
        with open(self.database_path, 'wb') as f:
            pickle.dump(data, f)
        print(f"Database saved with {len(self.known_face_ids)} faces")
    
    def load_faces_from_db(self, app):
        """Load faces from Flask database"""
        with app.app_context():
            students = Student.query.filter_by(is_active=True).all()
            
            for student in students:
                if student.face_encodings:
                    try:
                        encodings = json.loads(student.face_encodings)
                        if encodings and len(encodings) > 0:
                            # Convert to numpy array
                            encoding_array = np.array(encodings[0], dtype=np.float64)
                            self.known_face_encodings.append(encoding_array)
                            self.known_face_ids.append(student.id)
                            self.known_face_names.append(student.user.full_name if student.user else f"Student_{student.id}")
                            self.known_face_student_ids.append(student.id)
                            
                            # Load image if exists
                            if student.face_images:
                                images = json.loads(student.face_images)
                                if images and len(images) > 0:
                                    img = cv2.imread(images[0])
                                    if img is not None:
                                        img = cv2.resize(img, (100, 100))
                                        self.known_face_images.append(img)
                                    else:
                                        self.known_face_images.append(np.zeros((100, 100, 3), dtype=np.uint8))
                                else:
                                    self.known_face_images.append(np.zeros((100, 100, 3), dtype=np.uint8))
                            else:
                                self.known_face_images.append(np.zeros((100, 100, 3), dtype=np.uint8))
                    except Exception as e:
                        print(f"Error loading face for student {student.id}: {e}")
            
            print(f"✅ Loaded {len(self.known_face_ids)} faces from database")
            self.save_database()
    
    def set_active_session(self, session_id, session_name, start_time, end_time):
        """Set the active attendance session"""
        self.active_session_id = session_id
        self.active_session_name = session_name
        self.attendance_start_time = start_time
        self.attendance_end_time = end_time
        print(f"✅ Active session set: {session_name} ({start_time} - {end_time})")
    
    def check_attendance_time(self):
        """Check if current time is within attendance window"""
        if not self.attendance_start_time or not self.attendance_end_time:
            return False
        
        current_time = datetime.now().time()
        
        # Handle time windows that cross midnight
        if self.attendance_start_time <= self.attendance_end_time:
            return self.attendance_start_time <= current_time <= self.attendance_end_time
        else:
            # Window crosses midnight (e.g., 22:00 to 02:00)
            return current_time >= self.attendance_start_time or current_time <= self.attendance_end_time
    
    def eye_aspect_ratio(self, eye_landmarks):
        """Calculate eye aspect ratio (EAR)"""
        A = dist.euclidean(eye_landmarks[1], eye_landmarks[5])
        B = dist.euclidean(eye_landmarks[2], eye_landmarks[4])
        C = dist.euclidean(eye_landmarks[0], eye_landmarks[3])
        ear = (A + B) / (2.0 * C)
        return ear
    
    def check_liveness(self, frame, face_location, student_id):
        """Check if the face is live (not a photo/video)"""
        if student_id not in self.blink_counter:
            self.blink_counter[student_id] = 0
            self.liveness_attempts[student_id] = 0
        
        is_live = False
        liveness_score = 0
        liveness_method = "none"
        
        # Method 1: Blink detection using MediaPipe
        if self.face_mesh is not None:
            try:
                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                results = self.face_mesh.process(rgb_frame)
                
                if results.multi_face_landmarks:
                    for face_landmarks in results.multi_face_landmarks:
                        h, w = frame.shape[:2]
                        
                        # Get left eye landmarks
                        left_eye_points = []
                        for idx in self.LEFT_EYE_INDICES:
                            landmark = face_landmarks.landmark[idx]
                            x, y = int(landmark.x * w), int(landmark.y * h)
                            left_eye_points.append((x, y))
                        
                        # Get right eye landmarks
                        right_eye_points = []
                        for idx in self.RIGHT_EYE_INDICES:
                            landmark = face_landmarks.landmark[idx]
                            x, y = int(landmark.x * w), int(landmark.y * h)
                            right_eye_points.append((x, y))
                        
                        # Calculate EAR
                        left_ear = self.eye_aspect_ratio(left_eye_points)
                        right_ear = self.eye_aspect_ratio(right_eye_points)
                        ear = (left_ear + right_ear) / 2.0
                        
                        # Check for blink
                        if ear < self.EYE_AR_THRESH:
                            self.blink_counter[student_id] += 1
                        else:
                            if self.blink_counter[student_id] >= self.EYE_AR_CONSEC_FRAMES:
                                liveness_score = min(1.0, self.blink_counter[student_id] / 10)
                                is_live = True
                                liveness_method = "blink"
                            self.blink_counter[student_id] = 0
                        
                        break
            except Exception as e:
                pass
        
        # Method 2: Motion detection
        if not is_live and student_id in self.tracked_faces:
            prev_location = self.tracked_faces[student_id].get('location')
            if prev_location:
                top, right, bottom, left = face_location
                p_top, p_right, p_bottom, p_left = prev_location
                
                movement = abs(top - p_top) + abs(bottom - p_bottom)
                if movement > 5:
                    liveness_score = min(1.0, movement / 20)
                    is_live = True
                    liveness_method = "motion"
        
        # Method 3: Texture analysis
        if not is_live:
            try:
                top, right, bottom, left = face_location
                face_roi = frame[top:bottom, left:right]
                gray_face = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
                laplacian_var = cv2.Laplacian(gray_face, cv2.CV_64F).var()
                
                if laplacian_var > 50:
                    liveness_score = min(1.0, laplacian_var / 200)
                    is_live = True
                    liveness_method = "texture"
            except:
                pass
        
        # Update attempts
        self.liveness_attempts[student_id] = self.liveness_attempts.get(student_id, 0) + 1
        
        # Save suspicious frames
        if self.liveness_attempts[student_id] > 30 and not is_live:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"liveness_fails/suspicious_{timestamp}.jpg"
            cv2.imwrite(filename, frame)
        
        return is_live, liveness_score, liveness_method
    
    def mark_attendance_in_db(self, student_id, student_name, status, liveness_score, camera_id):
        """Mark attendance in Flask database"""
        try:
            from app import db, socketio
            from models import Attendance
            
            # Check if already marked today
            today = datetime.now().date()
            existing = Attendance.query.filter(
                Attendance.student_id == student_id,
                func.date(Attendance.timestamp) == today,
                Attendance.status == status
            ).first()
            
            if not existing:
                attendance = Attendance(
                    student_id=student_id,
                    status=status,
                    confidence=liveness_score * 100,
                    camera_id=camera_id,
                    verified=True,
                    timestamp=datetime.now()
                )
                db.session.add(attendance)
                db.session.commit()
                
                # Emit real-time update
                socketio.emit('attendance_update', {
                    'student_id': student_id,
                    'student_name': student_name,
                    'status': status,
                    'timestamp': datetime.now().isoformat(),
                    'confidence': liveness_score * 100,
                    'camera_id': camera_id,
                    'crossing_detected': True,
                    'liveness_score': liveness_score
                })
                
                print(f"✅ DATABASE: {student_name} {status} at {datetime.now().strftime('%H:%M:%S')}")
                return True
            else:
                print(f"⚠️ Already marked: {student_name} {status} today")
                return False
                
        except Exception as e:
            print(f"Error marking attendance in DB: {e}")
            return False
    
    def process_camera_feed(self, camera_id, camera_type, camera_index, frame_queue):
        """Process a single camera feed for dual setup"""
        cap = cv2.VideoCapture(camera_index)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        
        frame_height = 480
        line_y = int(frame_height * self.line_position)
        
        while self.running:
            ret, frame = cap.read()
            if not ret:
                continue
            
            current_time = datetime.now()
            frame_height, frame_width = frame.shape[:2]
            line_y = int(frame_height * self.line_position)
            
            # Draw the separation line
            cv2.line(frame, (0, line_y), (frame_width, line_y), (0, 255, 255), 3)
            
            # Label the zones
            cv2.putText(frame, f"{camera_type} - DETECTION ZONE", (10, line_y - 15), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 255), 2)
            
            if self.process_this_frame:
                small = cv2.resize(frame, (0, 0), fx=0.25, fy=0.25)
                rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
                
                locations = face_recognition.face_locations(rgb, model='hog')
                encodings = face_recognition.face_encodings(rgb, locations)
                
                for i, (loc, enc) in enumerate(zip(locations, encodings)):
                    top, right, bottom, left = [x*4 for x in loc]
                    
                    # Check if face is below the line
                    face_center_y = (top + bottom) // 2
                    if face_center_y < line_y:
                        continue  # Skip faces above the line
                    
                    # Identify face
                    name = "Unknown"
                    student_id = None
                    face_idx = -1
                    confidence = 0
                    
                    if self.known_face_encodings:
                        matches = face_recognition.compare_faces(self.known_face_encodings, enc, tolerance=0.6)
                        distances = face_recognition.face_distance(self.known_face_encodings, enc)
                        
                        if len(distances) > 0:
                            best = np.argmin(distances)
                            if matches[best]:
                                name = self.known_face_names[best]
                                student_id = self.known_face_student_ids[best]
                                face_idx = best
                                confidence = 1 - distances[best]
                                
                                # Check liveness
                                is_live, liveness_score, liveness_method = self.check_liveness(frame, (top, right, bottom, left), student_id)
                                
                                # Store tracking info
                                self.tracked_faces[student_id] = {
                                    'location': (top, right, bottom, left),
                                    'last_seen': current_time
                                }
                                
                                if is_live or student_id in self.liveness_passed:
                                    self.liveness_passed.add(student_id)
                                    
                                    # Check if within attendance time
                                    within_time = self.check_attendance_time()
                                    
                                    # Record entry/exit
                                    if camera_type == "ENTRY":
                                        if student_id not in self.entry_times and within_time:
                                            self.entry_times[student_id] = current_time
                                            print(f"✅ ENTRY: {name} at {current_time.strftime('%H:%M:%S')} (Liveness: {liveness_score:.2f})")
                                            
                                            # Mark attendance in database
                                            self.mark_attendance_in_db(student_id, name, 'in', liveness_score, camera_id)
                                            
                                            self.attendance_marked.add(name)
                                            self.attendance_marked_ids.add(student_id)
                                    
                                    elif camera_type == "EXIT":
                                        if student_id in self.entry_times and student_id not in self.exit_times and within_time:
                                            self.exit_times[student_id] = current_time
                                            # Calculate duration
                                            entry = self.entry_times[student_id]
                                            duration = current_time - entry
                                            hours = duration.total_seconds() / 3600
                                            
                                            print(f"✅ EXIT: {name} at {current_time.strftime('%H:%M:%S')} (Duration: {hours:.2f} hours)")
                                            
                                            # Mark exit in database
                                            self.mark_attendance_in_db(student_id, name, 'out', liveness_score, camera_id)
                                            
                                            # Store complete session with entry/exit times
                                            self.session_data.append({
                                                'ID': student_id,
                                                'Name': name,
                                                'Attendance': 'PRESENT',
                                                'Entry Time': entry.strftime('%H:%M:%S'),
                                                'Exit Time': current_time.strftime('%H:%M:%S'),
                                                'Date': current_time.strftime('%Y-%m-%d'),
                                                'Total Hours': round(hours, 2),
                                                'Liveness Score': f"{liveness_score:.2f}",
                                                'Liveness Passed': 'YES'
                                            })
                    
                    # Draw box with appropriate color
                    if name != "Unknown":
                        if student_id in self.exit_times:
                            color = (128, 128, 128)  # Gray for exited
                        elif student_id in self.entry_times:
                            color = (0, 255, 0)  # Green for in class
                        elif student_id in self.liveness_passed:
                            color = (255, 255, 0)  # Yellow for live
                        else:
                            color = (0, 165, 255)  # Orange for detected
                    else:
                        color = (0, 0, 255)  # Red for unknown
                    
                    cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                    
                    # Add label
                    if name != "Unknown":
                        if student_id in self.exit_times:
                            status = "EXITED"
                        elif student_id in self.entry_times:
                            status = "IN CLASS"
                        elif student_id in self.liveness_passed:
                            status = "LIVE"
                        else:
                            status = "BLINK"
                        
                        label = f"{camera_type}: {name} ({status})"
                    else:
                        label = f"{camera_type}: Unknown"
                    
                    cv2.putText(frame, label, (left, top-10), 
                               cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 2)
            
            self.process_this_frame = not self.process_this_frame
            
            # Add info to frame
            cv2.putText(frame, f"Time: {current_time.strftime('%H:%M:%S')}", (10, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            
            if self.active_session_name:
                cv2.putText(frame, f"Session: {self.active_session_name}", (10, 55), 
                           cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
            
            within_time = self.check_attendance_time()
            time_color = (0, 255, 0) if within_time else (0, 0, 255)
            time_status = "WITHIN TIME" if within_time else "OUTSIDE TIME"
            cv2.putText(frame, time_status, (10, 80), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, time_color, 2)
            
            in_class = len([e for e in self.entry_times.keys() if e not in self.exit_times])
            cv2.putText(frame, f"In Class: {in_class}", (frame_width - 200, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
            
            # Add frame to queue
            frame_queue.put((camera_type, frame))
    
    def run_dual_cameras(self, entry_camera_id, exit_camera_id, entry_index, exit_index):
        """Run both cameras simultaneously"""
        print("\n" + "="*60)
        print(f"DUAL CAMERA ATTENDANCE SYSTEM - Session: {self.active_session_name}")
        print("="*60)
        
        self.entry_camera_id = entry_camera_id
        self.exit_camera_id = exit_camera_id
        self.entry_camera_index = entry_index
        self.exit_camera_index = exit_index
        
        if len(self.known_face_ids) == 0:
            print("\n⚠️ No faces in database! Please register first.")
            return
        
        print("\n🚀 Starting both cameras...")
        print(f"ENTRY camera (Index {entry_index}): Records when students enter")
        print(f"EXIT camera (Index {exit_index}): Records when students leave")
        print("\nPress 'q' in any window to stop")
        print("Press '+' to move detection line UP")
        print("Press '-' to move detection line DOWN")
        
        self.running = True
        frame_queue = queue.Queue()
        
        # Start camera threads
        entry_thread = threading.Thread(target=self.process_camera_feed, 
                                      args=(entry_camera_id, "ENTRY", entry_index, frame_queue))
        exit_thread = threading.Thread(target=self.process_camera_feed, 
                                     args=(exit_camera_id, "EXIT", exit_index, frame_queue))
        
        entry_thread.start()
        exit_thread.start()
        
        # Display windows
        while self.running:
            try:
                camera_type, frame = frame_queue.get(timeout=1)
                
                window_name = f"{camera_type} CAMERA"
                cv2.imshow(window_name, frame)
                
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    self.running = False
                    break
                elif key == ord('+') or key == ord('='):
                    self.line_position = min(0.9, self.line_position + 0.05)
                    print(f"Line moved UP to {int(self.line_position * 100)}%")
                elif key == ord('-') or key == ord('_'):
                    self.line_position = max(0.1, self.line_position - 0.05)
                    print(f"Line moved DOWN to {int(self.line_position * 100)}%")
                    
            except queue.Empty:
                continue
        
        # Cleanup
        self.running = False
        entry_thread.join(timeout=2)
        exit_thread.join(timeout=2)
        cv2.destroyAllWindows()
        
        # Generate report and save
        self.generate_absent_report()
        self.save_to_excel()
    
    def generate_absent_report(self):
        """Generate report for absentees"""
        print("\n" + "="*50)
        print("ATTENDANCE SUMMARY")
        print("="*50)
        
        if self.attendance_start_time and self.attendance_end_time:
            print(f"Time Window: {self.attendance_start_time.strftime('%H:%M')} - {self.attendance_end_time.strftime('%H:%M')}")
        
        if self.active_session_name:
            print(f"Session: {self.active_session_name}")
        
        print(f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("-"*50)
        
        all_faces = set(self.known_face_names)
        present_faces = self.attendance_marked
        absent_faces = all_faces - present_faces
        
        print(f"\n✅ PRESENT ({len(present_faces)}):")
        if present_faces:
            for name in sorted(present_faces):
                # Check if face has entry/exit data
                found = False
                for session in self.session_data:
                    if session['Name'] == name:
                        print(f"  - {name} (Entry: {session['Entry Time']}, Exit: {session['Exit Time']}, Hours: {session['Total Hours']})")
                        found = True
                        break
                if not found:
                    # Find in single camera log
                    for record in self.attendance_log:
                        if record['Name'] == name and record['Attendance'] == 'PRESENT':
                            print(f"  - {name} at {record['Time Detected']}")
                            break
                    else:
                        print(f"  - {name}")
        else:
            print("  No one was present")
        
        print(f"\n❌ ABSENT ({len(absent_faces)}):")
        if absent_faces:
            for name in sorted(absent_faces):
                print(f"  - {name}")
        else:
            print("  Everyone was present! 🎉")
        
        # Show currently in class from dual camera
        still_in_class = []
        for student_id in self.entry_times.keys():
            if student_id not in self.exit_times:
                # Find name for this student_id
                if student_id in self.known_face_student_ids:
                    idx = self.known_face_student_ids.index(student_id)
                    if idx < len(self.known_face_names):
                        still_in_class.append(self.known_face_names[idx])
        
        if still_in_class:
            print(f"\n⚠️ Still in class: {', '.join(still_in_class)}")
        
        print("-"*50)
    
    def save_to_excel(self):
        """Save attendance records to Excel file"""
        if not self.attendance_log and not self.session_data:
            print("No attendance records to save!")
            return
        
        # Combine single camera attendance log with dual camera session data
        all_records = []
        
        # Add single camera records
        for record in self.attendance_log:
            all_records.append(record)
        
        # Add dual camera session data with entry/exit times
        for session in self.session_data:
            all_records.append(session)
        
        if not all_records:
            print("No records to save!")
            return
        
        df = pd.DataFrame(all_records)
        
        # Save with timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        session_name = self.active_session_name.replace(' ', '_') if self.active_session_name else 'attendance'
        filename = f"attendance_records/{session_name}_{timestamp}.xlsx"
        df.to_excel(filename, index=False)
        print(f"✅ Attendance saved to: {filename}")
        
        # Save daily backup
        today = datetime.now().strftime('%Y%m%d')
        daily_filename = f"attendance_records/daily_{today}.xlsx"
        
        # Check if daily file exists and append or create new
        if os.path.exists(daily_filename):
            existing_df = pd.read_excel(daily_filename)
            combined_df = pd.concat([existing_df, df], ignore_index=True)
            combined_df.to_excel(daily_filename, index=False)
        else:
            df.to_excel(daily_filename, index=False)
        
        print(f"✅ Daily backup saved to: {daily_filename}")