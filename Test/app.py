import cv2
import face_recognition
import numpy as np
import pickle
import pandas as pd
from datetime import datetime
import os
import time

class FaceAttendanceSystem:
    def __init__(self, registered_faces_path='registered_faces.pkl', 
                 attendance_file='attendance.csv'):
        """
        Initialize the Face Attendance System
        """
        self.registered_faces_path = registered_faces_path
        self.attendance_file = attendance_file
        self.known_face_encodings = []
        self.known_face_names = []
        self.known_face_ids = []
        self.marked_attendance = set()  # Track who already got attendance
        self.load_registered_faces()
        
    def load_registered_faces(self):
        """Load registered faces from pickle file"""
        if os.path.exists(self.registered_faces_path):
            with open(self.registered_faces_path, 'rb') as f:
                data = pickle.load(f)
                self.known_face_encodings = data['encodings']
                self.known_face_names = data['names']
                self.known_face_ids = data['ids']
            print(f"✓ Loaded {len(self.known_face_names)} registered faces")
        else:
            print("⚠ No registered faces found. Please register faces first.")
    
    def register_face_manually(self):
        """
        Register a new face by capturing from camera
        """
        print("\n=== Manual Face Registration ===")
        print("Instructions:")
        print("1. Look directly at the camera")
        print("2. Ensure good lighting")
        print("3. Press 'SPACE' to capture your face")
        print("4. Press 'q' to cancel")
        
        # Get user details
        name = input("\nEnter person's name: ").strip()
        if not name:
            print("❌ Name cannot be empty!")
            return False
        
        emp_id = input("Enter employee ID: ").strip()
        if not emp_id:
            print("❌ Employee ID cannot be empty!")
            return False
        
        # Check if ID already exists
        if emp_id in self.known_face_ids:
            print(f"❌ Employee ID {emp_id} already exists!")
            return False
        
        # Initialize camera
        video_capture = cv2.VideoCapture(0)
        
        if not video_capture.isOpened():
            print("❌ Error: Could not open camera.")
            return False
        
        print("\n📸 Starting camera. Look at the camera and press SPACE to capture...")
        
        face_encoded = False
        face_encoding = None
        
        while True:
            # Grab frame
            ret, frame = video_capture.read()
            if not ret:
                print("❌ Failed to grab frame")
                break
            
            # Create a copy for display
            display_frame = frame.copy()
            
            # Find faces in the frame
            small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            face_locations = face_recognition.face_locations(rgb_small_frame)
            
            # Draw rectangles around faces
            for (top, right, bottom, left) in face_locations:
                # Scale back up
                top *= 2
                right *= 2
                bottom *= 2
                left *= 2
                
                # Draw rectangle
                cv2.rectangle(display_frame, (left, top), (right, bottom), (0, 255, 0), 2)
            
            # Add instructions on frame
            cv2.putText(display_frame, f"Registering: {name} ({emp_id})", (10, 30),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(display_frame, f"Faces detected: {len(face_locations)}", (10, 60),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.putText(display_frame, "Press SPACE to capture | 'q' to cancel", (10, 90),
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            
            # Show frame
            cv2.imshow('Face Registration', display_frame)
            
            # Handle key presses
            key = cv2.waitKey(1) & 0xFF
            
            if key == ord(' '):  # Space key
                if len(face_locations) == 0:
                    print("❌ No face detected! Please try again.")
                elif len(face_locations) > 1:
                    print("❌ Multiple faces detected! Please ensure only one person is in frame.")
                else:
                    # Get face encoding
                    face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
                    if len(face_encodings) > 0:
                        face_encoding = face_encodings[0]
                        face_encoded = True
                        print(f"✅ Face captured successfully for {name}!")
                        break
                    else:
                        print("❌ Could not encode face. Please try again.")
            
            elif key == ord('q'):  # Quit
                print("❌ Registration cancelled.")
                break
        
        video_capture.release()
        cv2.destroyAllWindows()
        
        # Save the registered face
        if face_encoded and face_encoding is not None:
            # Add to known faces
            self.known_face_encodings.append(face_encoding)
            self.known_face_names.append(name)
            self.known_face_ids.append(emp_id)
            
            # Save to file
            self.save_registered_faces()
            
            print(f"\n✅ Successfully registered {name} (ID: {emp_id})!")
            
            # Optionally save the captured image
            save_image = input("Save captured image? (y/n): ").lower()
            if save_image == 'y':
                if not os.path.exists('captured_faces'):
                    os.makedirs('captured_faces')
                img_path = f"captured_faces/{name}_{emp_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
                cv2.imwrite(img_path, frame)
                print(f"✅ Image saved as: {img_path}")
            
            return True
        
        return False
    
    def save_registered_faces(self):
        """Save registered faces to pickle file"""
        data = {
            'encodings': self.known_face_encodings,
            'names': self.known_face_names,
            'ids': self.known_face_ids
        }
        
        with open(self.registered_faces_path, 'wb') as f:
            pickle.dump(data, f)
        
        print(f"✓ Saved {len(self.known_face_names)} registered faces to file")
    
    def register_multiple_faces(self):
        """Register multiple faces one after another"""
        print("\n=== Multiple Face Registration ===")
        
        while True:
            self.register_face_manually()
            
            cont = input("\nRegister another face? (y/n): ").lower()
            if cont != 'y':
                break
        
        print(f"\n✓ Total registered faces: {len(self.known_face_names)}")
    
    def view_registered_faces(self):
        """Display all registered faces"""
        if len(self.known_face_names) == 0:
            print("⚠ No registered faces found.")
            return
        
        print("\n=== Registered Faces ===")
        print(f"{'Index':<6} {'Name':<20} {'ID':<10}")
        print("-" * 40)
        
        for i, (name, emp_id) in enumerate(zip(self.known_face_names, self.known_face_ids)):
            print(f"{i+1:<6} {name:<20} {emp_id:<10}")
        
        print(f"\nTotal: {len(self.known_face_names)} faces")
    
    def delete_registered_face(self):
        """Delete a registered face"""
        if len(self.known_face_names) == 0:
            print("⚠ No registered faces to delete.")
            return
        
        self.view_registered_faces()
        
        try:
            index = int(input("\nEnter the index number to delete (0 to cancel): ")) - 1
            
            if index == -1:
                return
            
            if 0 <= index < len(self.known_face_names):
                name = self.known_face_names[index]
                emp_id = self.known_face_ids[index]
                
                confirm = input(f"Delete {name} (ID: {emp_id})? (y/n): ").lower()
                if confirm == 'y':
                    # Remove from lists
                    del self.known_face_encodings[index]
                    del self.known_face_names[index]
                    del self.known_face_ids[index]
                    
                    # Save to file
                    self.save_registered_faces()
                    
                    print(f"✅ Deleted {name} (ID: {emp_id})")
            else:
                print("❌ Invalid index number!")
                
        except ValueError:
            print("❌ Please enter a valid number!")
    
    def mark_attendance(self, name, emp_id):
        """Mark attendance in CSV file"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        date = datetime.now().strftime('%Y-%m-%d')
        time_in = datetime.now().strftime('%H:%M:%S')
        
        # Create attendance DataFrame if doesn't exist
        if os.path.exists(self.attendance_file):
            df = pd.read_csv(self.attendance_file)
        else:
            df = pd.DataFrame(columns=['Name', 'ID', 'Date', 'Time', 'Status'])
        
        # Check if already marked for today
        if f"{emp_id}_{date}" not in self.marked_attendance:
            new_record = pd.DataFrame({
                'Name': [name],
                'ID': [emp_id],
                'Date': [date],
                'Time': [time_in],
                'Status': ['Present']
            })
            
            df = pd.concat([df, new_record], ignore_index=True)
            df.to_csv(self.attendance_file, index=False)
            self.marked_attendance.add(f"{emp_id}_{date}")
            print(f"✓ Attendance marked for {name} (ID: {emp_id}) at {time_in}")
            return True
        return False
    
    def run_real_time_attendance(self):
        """
        Run real-time face detection and attendance marking
        """
        if len(self.known_face_encodings) == 0:
            print("⚠ No registered faces found! Please register faces first.")
            return
        
        video_capture = cv2.VideoCapture(0)
        
        if not video_capture.isOpened():
            print("❌ Error: Could not open camera.")
            return
        
        print("\n=== Starting Real-Time Attendance System ===")
        print(f"📊 Registered faces: {len(self.known_face_names)}")
        print("📍 Press 'q' to quit, 's' to save attendance report")
        print("📍 Press 'r' to reset today's marked attendance")
        
        frame_count = 0
        face_locations = []
        face_encodings = []
        face_names = []
        process_this_frame = True
        
        # Reset marked attendance for new session
        self.marked_attendance.clear()
        
        while True:
            # Grab a single frame of video
            ret, frame = video_capture.read()
            if not ret:
                print("❌ Failed to grab frame")
                break
            
            # Resize frame for faster processing
            small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
            rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
            
            # Process every other frame to save time
            if process_this_frame:
                # Find all faces in the current frame
                face_locations = face_recognition.face_locations(rgb_small_frame)
                face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
                
                face_names = []
                for face_encoding in face_encodings:
                    # Compare with known faces
                    if len(self.known_face_encodings) > 0:
                        matches = face_recognition.compare_faces(
                            self.known_face_encodings, 
                            face_encoding,
                            tolerance=0.5
                        )
                        
                        face_distances = face_recognition.face_distance(
                            self.known_face_encodings, 
                            face_encoding
                        )
                        
                        if True in matches:
                            best_match_index = np.argmin(face_distances)
                            if matches[best_match_index]:
                                name = self.known_face_names[best_match_index]
                                emp_id = self.known_face_ids[best_match_index]
                                
                                # Mark attendance
                                self.mark_attendance(name, emp_id)
                                
                                face_names.append(f"{name} ({emp_id})")
                            else:
                                face_names.append("Unknown")
                        else:
                            face_names.append("Unknown")
                    else:
                        face_names.append("Unknown")
            
            process_this_frame = not process_this_frame
            
            # Display results
            for (top, right, bottom, left), name in zip(face_locations, face_names):
                # Scale back up face locations
                top *= 2
                right *= 2
                bottom *= 2
                left *= 2
                
                # Draw box and label
                if name != "Unknown":
                    color = (0, 255, 0)  # Green for known faces
                else:
                    color = (0, 0, 255)  # Red for unknown faces
                
                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                
                # Draw label background
                cv2.rectangle(frame, (left, bottom - 35), (right, bottom), color, cv2.FILLED)
                font = cv2.FONT_HERSHEY_DUPLEX
                cv2.putText(frame, name, (left + 6, bottom - 6), font, 0.6, (255, 255, 255), 1)
            
            # Display info
            cv2.putText(frame, f"Faces detected: {len(face_locations)}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.putText(frame, f"Marked today: {len(self.marked_attendance)}", (10, 60),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
            cv2.putText(frame, "Press 'q' to quit, 's' to save report", (10, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            
            # Display frame
            cv2.imshow('Face Attendance System', frame)
            
            # Handle keyboard input
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q'):
                break
            elif key == ord('s'):
                self.save_attendance_report()
            elif key == ord('r'):
                self.marked_attendance.clear()
                print("🔄 Reset today's marked attendance")
        
        video_capture.release()
        cv2.destroyAllWindows()
    
    def save_attendance_report(self):
        """Save today's attendance report"""
        if os.path.exists(self.attendance_file):
            df = pd.read_csv(self.attendance_file)
            today = datetime.now().strftime('%Y-%m-%d')
            today_attendance = df[df['Date'] == today]
            
            if len(today_attendance) > 0:
                report_file = f"attendance_report_{today}.csv"
                today_attendance.to_csv(report_file, index=False)
                
                print(f"\n📊 Attendance Report - {today}")
                print(f"Total Present: {len(today_attendance)}")
                print(f"Report saved as: {report_file}")
                print("\n" + today_attendance.to_string(index=False))
            else:
                print("⚠ No attendance records for today")
        else:
            print("⚠ No attendance records found")

# Main menu function
def main():
    # Initialize the system
    system = FaceAttendanceSystem()
    
    while True:
        print("\n" + "="*50)
        print("         FACE ATTENDANCE SYSTEM")
        print("="*50)
        print("1. 📝 Register New Face (Manual Capture)")
        print("2. 📋 Register Multiple Faces")
        print("3. 📸 Start Real-Time Attendance")
        print("4. 👥 View Registered Faces")
        print("5. ❌ Delete Registered Face")
        print("6. 📊 View Today's Attendance")
        print("7. 🚪 Exit")
        print("-"*50)
        
        choice = input("Enter your choice (1-7): ").strip()
        
        if choice == '1':
            system.register_face_manually()
            
        elif choice == '2':
            system.register_multiple_faces()
            
        elif choice == '3':
            system.run_real_time_attendance()
            
        elif choice == '4':
            system.view_registered_faces()
            
        elif choice == '5':
            system.delete_registered_face()
            
        elif choice == '6':
            system.save_attendance_report()
            
        elif choice == '7':
            print("\n👋 Thank you for using Face Attendance System!")
            break
            
        else:
            print("❌ Invalid choice. Please try again.")

if __name__ == "__main__":
    main()