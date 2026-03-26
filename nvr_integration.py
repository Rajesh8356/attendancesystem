import cv2
import os
import time
import threading
import logging
import requests
import numpy as np
from datetime import datetime, timedelta
import subprocess

logger = logging.getLogger(__name__)

class NVRSystem:
    def __init__(self, config):
        self.config = config
        self.nvr_ip = config.get('NVR_IP', '192.168.1.100')
        self.username = config.get('NVR_USERNAME', 'admin')
        self.password = config.get('NVR_PASSWORD', 'admin123')
        self.recording_path = config.get('RECORDING_PATH', 'recordings/')
        
        # Ensure recording directory exists
        os.makedirs(self.recording_path, exist_ok=True)
        
        # Active recordings
        self.active_recordings = {}
        
        # RTSP URLs for cameras
        self.rtsp_urls = {}
    
    def add_camera(self, camera_id, ip_address, channel=1, stream='main'):
        """Add a camera to the system"""
        # RTSP URL format: rtsp://username:password@ip:port/Streaming/Channels/{channel}01
        rtsp_url = f"rtsp://{self.username}:{self.password}@{ip_address}:554/Streaming/Channels/{channel}01"
        self.rtsp_urls[camera_id] = rtsp_url
        logger.info(f"Added camera {camera_id} with RTSP: {rtsp_url}")
        
    def get_live_feed(self, camera_id):
        """Get live feed from camera"""
        if camera_id not in self.rtsp_urls:
            logger.error(f"Camera {camera_id} not found")
            return None
        
        try:
            # Open RTSP stream
            cap = cv2.VideoCapture(self.rtsp_urls[camera_id])
            
            if not cap.isOpened():
                logger.error(f"Failed to open camera {camera_id}")
                return None
            
            return cap
            
        except Exception as e:
            logger.error(f"Error getting live feed: {str(e)}")
            return None
    
    def start_recording(self, camera_id, duration_hours=24):
        """Start continuous recording for a camera"""
        if camera_id in self.active_recordings:
            logger.warning(f"Recording already active for camera {camera_id}")
            return False
        
        # Create recording thread
        thread = threading.Thread(
            target=self._record_continuous,
            args=(camera_id, duration_hours),
            daemon=True
        )
        
        self.active_recordings[camera_id] = {
            'thread': thread,
            'start_time': datetime.now(),
            'duration': duration_hours,
            'is_recording': True
        }
        
        thread.start()
        logger.info(f"Started recording for camera {camera_id}")
        return True
    
    def stop_recording(self, camera_id):
        """Stop recording for a camera"""
        if camera_id in self.active_recordings:
            self.active_recordings[camera_id]['is_recording'] = False
            logger.info(f"Stopped recording for camera {camera_id}")
            return True
        
        return False
    
    def _record_continuous(self, camera_id, duration_hours):
        """Continuous recording implementation"""
        cap = self.get_live_feed(camera_id)
        
        if cap is None:
            logger.error(f"Cannot open camera {camera_id} for recording")
            return
        
        # Get video properties
        fps = int(cap.get(cv2.CAP_PROP_FPS))
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        # Calculate segment duration (1 hour = 3600 seconds)
        segment_duration = 3600
        frames_per_segment = segment_duration * fps
        
        start_time = datetime.now()
        segment_start = start_time
        
        frame_count = 0
        segment_count = 0
        
        # Create video writer for first segment
        segment_filename = self._get_segment_filename(camera_id, segment_start)
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(
            segment_filename,
            fourcc,
            fps,
            (width, height)
        )
        
        logger.info(f"Recording to {segment_filename}")
        
        try:
            while self.active_recordings[camera_id]['is_recording']:
                ret, frame = cap.read()
                
                if not ret:
                    logger.error(f"Failed to read frame from camera {camera_id}")
                    break
                
                # Write frame
                out.write(frame)
                frame_count += 1
                
                # Check if we need to start a new segment
                if frame_count >= frames_per_segment:
                    out.release()
                    
                    # Calculate segment size
                    size_mb = os.path.getsize(segment_filename) / (1024 * 1024)
                    
                    logger.info(f"Completed segment {segment_filename} ({size_mb:.2f} MB)")
                    
                    # Start new segment
                    frame_count = 0
                    segment_count += 1
                    segment_start = datetime.now()
                    segment_filename = self._get_segment_filename(
                        camera_id,
                        segment_start,
                        segment_count
                    )
                    
                    out = cv2.VideoWriter(
                        segment_filename,
                        fourcc,
                        fps,
                        (width, height)
                    )
                
                # Check if we've exceeded duration
                elapsed = (datetime.now() - start_time).total_seconds()
                if elapsed > duration_hours * 3600:
                    logger.info(f"Recording duration completed for camera {camera_id}")
                    break
                
                # Small sleep to prevent CPU overload
                time.sleep(0.001)
                
        except Exception as e:
            logger.error(f"Error in recording thread: {str(e)}")
            
        finally:
            out.release()
            cap.release()
            
            if camera_id in self.active_recordings:
                del self.active_recordings[camera_id]
            
            logger.info(f"Recording stopped for camera {camera_id}")
    
    def _get_segment_filename(self, camera_id, timestamp, segment=0):
        """Generate filename for recording segment"""
        date_str = timestamp.strftime('%Y%m%d')
        time_str = timestamp.strftime('%H%M%S')
        
        # Create date directory
        date_dir = os.path.join(self.recording_path, date_str)
        os.makedirs(date_dir, exist_ok=True)
        
        # Camera directory
        camera_dir = os.path.join(date_dir, f"camera_{camera_id}")
        os.makedirs(camera_dir, exist_ok=True)
        
        filename = f"{time_str}_segment_{segment:03d}.mp4"
        return os.path.join(camera_dir, filename)
    
    def get_recordings(self, camera_ip, start_time, end_time):
        """Get list of recordings for a time range"""
        recordings = []
        
        # In production, this would query the NVR's API
        # For demo, return mock recordings
        current_time = start_time
        while current_time < end_time:
            recordings.append({
                'start_time': current_time,
                'end_time': current_time + timedelta(minutes=30),
                'file_path': f"/recordings/{current_time.strftime('%Y%m%d_%H%M%S')}.mp4",
                'size_mb': 50 + np.random.randint(20)
            })
            current_time += timedelta(minutes=30)
        
        return recordings
    
    def get_snapshot(self, camera_id):
        """Get a snapshot from live feed"""
        cap = self.get_live_feed(camera_id)
        
        if cap is None:
            return None
        
        ret, frame = cap.read()
        cap.release()
        
        if ret:
            # Encode frame to JPEG
            _, buffer = cv2.imencode('.jpg', frame)
            return buffer.tobytes()
        
        return None
    
    def detect_motion(self, camera_id, sensitivity=25):
        """Detect motion in camera feed"""
        cap = self.get_live_feed(camera_id)
        
        if cap is None:
            return None
        
        # Initialize background subtractor
        fgbg = cv2.createBackgroundSubtractorMOG2()
        
        motion_detected = False
        frames_checked = 0
        max_frames = 30
        
        while frames_checked < max_frames:
            ret, frame = cap.read()
            
            if not ret:
                break
            
            # Apply background subtraction
            fgmask = fgbg.apply(frame)
            
            # Count non-zero pixels
            contours, _ = cv2.findContours(
                fgmask,
                cv2.RETR_EXTERNAL,
                cv2.CHAIN_APPROX_SIMPLE
            )
            
            for contour in contours:
                if cv2.contourArea(contour) > sensitivity:
                    motion_detected = True
                    break
            
            frames_checked += 1
            
            if motion_detected:
                break
        
        cap.release()
        
        return {
            'motion_detected': motion_detected,
            'timestamp': datetime.now(),
            'camera_id': camera_id
        }
    
    def get_rtsp_stream_url(self, camera_id):
        """Get RTSP URL for streaming"""
        if camera_id in self.rtsp_urls:
            return self.rtsp_urls[camera_id]
        return None
    
    def save_snapshot(self, camera_id):
        """Save a snapshot to disk"""
        image_data = self.get_snapshot(camera_id)
        
        if image_data:
            filename = f"snapshot_{camera_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
            filepath = os.path.join(self.recording_path, 'snapshots', filename)
            
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            
            with open(filepath, 'wb') as f:
                f.write(image_data)
            
            return filepath
        
        return None