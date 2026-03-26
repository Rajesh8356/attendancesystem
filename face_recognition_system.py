import cv2
import numpy as np
import requests
import json
import base64
import time
import logging
import os
from datetime import datetime

logger = logging.getLogger(__name__)

class FaceRecognitionAPI:
    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = "https://api-us.faceplusplus.com/facepp/v3/"
        self.faceset_token = None
        self.faceset_outer_id = "institute_attendance_system"
        
        # Initialize face detector for local tracking
        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        
        # Face trackers for smooth tracking
        self.face_trackers = {}
        self.tracker_id_counter = 0
        
        # Cache for recognized faces
        self.recognition_cache = {}
        self.cache_ttl = 300  # 5 minutes
        
        # Initialize faceset
        self._init_faceset()
    
    def _init_faceset(self):
        """Initialize or get existing faceset"""
        try:
            # Check if faceset exists
            response = self._api_request(
                'faceset/getdetail',
                {'outer_id': self.faceset_outer_id}
            )
            
            if response and 'faceset_token' in response:
                self.faceset_token = response['faceset_token']
                logger.info(f"Faceset found: {self.faceset_token}")
                return True
            
            # Create new faceset
            response = self._api_request(
                'faceset/create',
                {
                    'outer_id': self.faceset_outer_id,
                    'display_name': 'Institute Attendance System',
                    'tags': 'attendance,institute'
                }
            )
            
            if response and 'faceset_token' in response:
                self.faceset_token = response['faceset_token']
                logger.info(f"Faceset created: {self.faceset_token}")
                return True
            
        except Exception as e:
            logger.error(f"Error initializing faceset: {str(e)}")
        
        return False
    
    def _api_request(self, endpoint, data=None, files=None):
        """Make API request with retry logic"""
        url = self.base_url + endpoint
        data = data or {}
        data.update({
            'api_key': self.api_key,
            'api_secret': self.api_secret
        })
        
        max_retries = 3
        for attempt in range(max_retries):
            try:
                if files:
                    response = requests.post(url, data=data, files=files, timeout=10)
                else:
                    response = requests.post(url, data=data, timeout=10)
                
                if response.status_code == 200:
                    result = response.json()
                    if 'error_message' in result:
                        logger.error(f"API Error: {result['error_message']}")
                        if attempt < max_retries - 1:
                            time.sleep(1)
                            continue
                    return result
                else:
                    logger.error(f"HTTP Error {response.status_code}: {response.text}")
                    
            except Exception as e:
                logger.error(f"Request failed (attempt {attempt+1}): {str(e)}")
                if attempt < max_retries - 1:
                    time.sleep(1)
        
        return None
    
    def detect_faces(self, image_path):
        """Detect faces in an image"""
        try:
            with open(image_path, 'rb') as f:
                files = {'image_file': f}
                result = self._api_request(
                    'detect',
                    {
                        'return_attributes': 'gender,age,headpose,facequality,blur,smiling,eyestatus,emotion',
                        'return_landmark': 1
                    },
                    files
                )
                
                if result and 'faces' in result:
                    return result['faces']
                
        except Exception as e:
            logger.error(f"Error detecting faces: {str(e)}")
        
        return []
    
    def register_face(self, image_path, student_id):
        """Register a face for a student"""
        try:
            # Detect face
            faces = self.detect_faces(image_path)
            
            if not faces:
                logger.error("No face detected in image")
                return None
            
            # Check face quality
            face = faces[0]
            attributes = face.get('attributes', {})
            
            facequality = attributes.get('facequality', {}).get('value', 0)
            if facequality < 70:
                logger.warning(f"Low face quality: {facequality}")
            
            # Get face token
            face_token = face['face_token']
            
            # Add face to faceset
            result = self._api_request(
                'faceset/addface',
                {
                    'faceset_token': self.faceset_token,
                    'face_tokens': face_token
                }
            )
            
            if result:
                logger.info(f"Face registered for student {student_id}")
                return {
                    'face_token': face_token,
                    'face_attributes': face.get('attributes', {}),
                    'face_rectangle': face.get('face_rectangle', {}),
                    'quality': facequality
                }
            
        except Exception as e:
            logger.error(f"Error registering face: {str(e)}")
        
        return None
    
    def search_face(self, face_token):
        """Search for a face in the faceset"""
        try:
            # Check cache first
            if face_token in self.recognition_cache:
                cache_entry = self.recognition_cache[face_token]
                if time.time() - cache_entry['timestamp'] < self.cache_ttl:
                    return cache_entry['result']
            
            result = self._api_request(
                'search',
                {
                    'face_token': face_token,
                    'faceset_token': self.faceset_token
                }
            )
            
            if result and 'results' in result and len(result['results']) > 0:
                best_match = result['results'][0]
                
                # Cache the result
                self.recognition_cache[face_token] = {
                    'result': best_match,
                    'timestamp': time.time()
                }
                
                return best_match
            
        except Exception as e:
            logger.error(f"Error searching face: {str(e)}")
        
        return None
    
    def compare_faces(self, face_token1, face_token2):
        """Compare two faces"""
        try:
            result = self._api_request(
                'compare',
                {
                    'face_token1': face_token1,
                    'face_token2': face_token2
                }
            )
            
            if result and 'confidence' in result:
                return result['confidence']
            
        except Exception as e:
            logger.error(f"Error comparing faces: {str(e)}")
        
        return 0
    
    def process_frame(self, image_data):
        """Process a frame for real-time recognition"""
        try:
            # Decode image
            if ',' in image_data:
                image_data = image_data.split(',')[1]
            
            image_bytes = base64.b64decode(image_data)
            nparr = np.frombuffer(image_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if frame is None:
                return {'success': False, 'error': 'Invalid image data'}
            
            # Convert to grayscale for local detection
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Detect faces locally
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(80, 80)
            )
            
            # Update trackers
            current_trackers = {}
            for (x, y, w, h) in faces:
                matched = False
                
                # Check existing trackers
                for tid, tracker in self.face_trackers.items():
                    pos = tracker.get_position()
                    tx, ty, tw, th = int(pos.left()), int(pos.top()), int(pos.width()), int(pos.height())
                    
                    # Calculate overlap
                    overlap_x = max(0, min(x + w, tx + tw) - max(x, tx))
                    overlap_y = max(0, min(y + h, ty + th) - max(y, ty))
                    overlap_area = overlap_x * overlap_y
                    
                    if overlap_area > (w * h * 0.5):
                        tracker.update(gray)
                        current_trackers[tid] = tracker
                        matched = True
                        break
                
                if not matched:
                    # Create new tracker
                    tracker = cv2.TrackerCSRT_create()
                    tracker.init(frame, (x, y, w, h))
                    tid = f"tracker_{self.tracker_id_counter}"
                    self.tracker_id_counter += 1
                    current_trackers[tid] = tracker
            
            # Update trackers list
            self.face_trackers = current_trackers
            
            # Process each face for recognition (throttled)
            results = []
            for (x, y, w, h) in faces[:3]:  # Limit to 3 faces per frame
                # Extract face region
                face_roi = frame[y:y+h, x:x+w]
                
                # Encode face for API
                _, buffer = cv2.imencode('.jpg', face_roi)
                face_base64 = base64.b64encode(buffer).decode('utf-8')
                
                # Create temporary file for API call
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    tmp.write(buffer)
                    tmp_path = tmp.name
                
                try:
                    # Detect face to get face token
                    faces_detected = self.detect_faces(tmp_path)
                    
                    if faces_detected:
                        face_token = faces_detected[0]['face_token']
                        
                        # Search for face
                        match = self.search_face(face_token)
                        
                        if match and match['confidence'] > 75:
                            # Get student info from database
                            from models import Student
                            student = Student.query.filter_by(face_token=match['face_token']).first()
                            
                            results.append({
                                'recognized': True,
                                'student_id': student.id if student else None,
                                'student_name': student.user.full_name if student and student.user else None,
                                'face_token': match['face_token'],
                                'confidence': match['confidence'],
                                'rectangle': {
                                    'left': x,
                                    'top': y,
                                    'width': w,
                                    'height': h
                                }
                            })
                        else:
                            results.append({
                                'recognized': False,
                                'rectangle': {
                                    'left': x,
                                    'top': y,
                                    'width': w,
                                    'height': h
                                }
                            })
                
                finally:
                    # Clean up
                    os.unlink(tmp_path)
            
            return {
                'success': True,
                'faces': results,
                'count': len(results)
            }
            
        except Exception as e:
            logger.error(f"Error processing frame: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def process_frame_fast(self, image_data):
        """Process a frame for real-time recognition (optimized for speed)"""
        try:
            # Decode image
            if ',' in image_data:
                image_data = image_data.split(',')[1]
            
            image_bytes = base64.b64decode(image_data)
            nparr = np.frombuffer(image_bytes, np.uint8)
            frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
            
            if frame is None:
                return {'success': False, 'error': 'Invalid image data'}
            
            # Convert to grayscale for local detection (faster)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Detect faces locally first (faster than API)
            faces = self.face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(100, 100)  # Larger min size = faster detection
            )
            
            results = []
            for (x, y, w, h) in faces[:2]:  # Limit to 2 faces per frame for speed
                # Extract face region
                face_roi = frame[y:y+h, x:x+w]
                
                # Quick check: if face is too small, skip
                if w < 100 or h < 100:
                    continue
                
                # Save temporarily
                import tempfile
                with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                    cv2.imwrite(tmp.name, face_roi)
                    tmp_path = tmp.name
                
                try:
                    # Detect face to get token (API call - slower but necessary)
                    faces_detected = self.detect_faces(tmp_path)
                    
                    if faces_detected:
                        face_token = faces_detected[0]['face_token']
                        
                        # Search for face
                        match = self.search_face(face_token)
                        
                        if match and match['confidence'] > 70:  # Slightly lower threshold for speed
                            student = Student.query.filter_by(face_token=match['face_token']).first()
                            
                            results.append({
                                'recognized': True,
                                'student_id': student.id if student else None,
                                'student_name': student.user.full_name if student and student.user else None,
                                'confidence': match['confidence'],
                                'face_rectangle': {'left': x, 'top': y, 'width': w, 'height': h}
                            })
                finally:
                    os.unlink(tmp_path)
            
            return {
                'success': True,
                'faces': results,
                'count': len(results)
            }
            
        except Exception as e:
            logger.error(f"Error processing frame: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def verify_face_quality(self, image_path):
        """Verify if face image meets quality standards"""
        try:
            faces = self.detect_faces(image_path)
            
            if not faces:
                return False, "No face detected"
            
            if len(faces) > 1:
                return False, "Multiple faces detected"
            
            face = faces[0]
            attributes = face.get('attributes', {})
            
            # Check face quality
            facequality = attributes.get('facequality', {}).get('value', 0)
            if facequality < 70:
                return False, f"Face quality too low: {facequality}"
            
            # Check blur
            blur = attributes.get('blur', {})
            blurness = blur.get('blurness', {}).get('value', 0)
            if blurness > 70:
                return False, "Image too blurry"
            
            # Check head pose
            headpose = attributes.get('headpose', {})
            yaw = abs(headpose.get('yaw_angle', 0))
            pitch = abs(headpose.get('pitch_angle', 0))
            
            if yaw > 20 or pitch > 20:
                return False, "Face not facing camera properly"
            
            # Check eye status
            eyestatus = attributes.get('eyestatus', {})
            left_eye = eyestatus.get('left_eye_status', 0)
            right_eye = eyestatus.get('right_eye_status', 0)
            
            if left_eye < 0.5 or right_eye < 0.5:
                return False, "Eyes not open properly"
            
            return True, "Face quality good"
            
        except Exception as e:
            logger.error(f"Error verifying face quality: {str(e)}")
            return False, str(e)
    
    def extract_face_features(self, image_path):
        """Extract detailed face features"""
        try:
            faces = self.detect_faces(image_path)
            
            if not faces:
                return None
            
            face = faces[0]
            attributes = face.get('attributes', {})
            
            return {
                'face_token': face['face_token'],
                'face_rectangle': face.get('face_rectangle', {}),
                'landmark': face.get('landmark', {}),
                'attributes': {
                    'gender': attributes.get('gender', {}).get('value'),
                    'age': attributes.get('age', {}).get('value'),
                    'smiling': attributes.get('smile', {}).get('value'),
                    'facequality': attributes.get('facequality', {}).get('value'),
                    'blurness': attributes.get('blur', {}).get('blurness', {}).get('value'),
                    'emotion': {
                        'anger': attributes.get('emotion', {}).get('anger'),
                        'disgust': attributes.get('emotion', {}).get('disgust'),
                        'fear': attributes.get('emotion', {}).get('fear'),
                        'happiness': attributes.get('emotion', {}).get('happiness'),
                        'neutral': attributes.get('emotion', {}).get('neutral'),
                        'sadness': attributes.get('emotion', {}).get('sadness'),
                        'surprise': attributes.get('emotion', {}).get('surprise')
                    },
                    'eyestatus': {
                        'left_eye': attributes.get('eyestatus', {}).get('left_eye_status'),
                        'right_eye': attributes.get('eyestatus', {}).get('right_eye_status')
                    }
                }
            }
            
        except Exception as e:
            logger.error(f"Error extracting face features: {str(e)}")
            return None