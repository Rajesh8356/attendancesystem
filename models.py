from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
import json

db = SQLAlchemy()


class Institute(db.Model):
    """Single institute configuration"""
    __tablename__ = 'institutes'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    address = db.Column(db.Text)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    logo_path = db.Column(db.String(200))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)

# In models.py, update the User class relationships:

class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(200))
    role = db.Column(db.String(20), nullable=False)  # admin, teacher, parent, student
    full_name = db.Column(db.String(200))
    phone = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)
    last_login = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    
    # Relationships - remove lazy='joined' to prevent automatic joins
    teacher = db.relationship('Teacher', backref='user', uselist=False)
    parent = db.relationship('Parent', backref='user', uselist=False)
    student = db.relationship('Student', backref='user', uselist=False)
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email,
            'role': self.role,
            'full_name': self.full_name,
            'phone': self.phone
        }

class Teacher(db.Model):
    __tablename__ = 'teachers'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    employee_id = db.Column(db.String(50), unique=True)
    qualification = db.Column(db.String(200))
    joining_date = db.Column(db.Date, default=date.today)
    department = db.Column(db.String(100))
    
    # Relationships - use different backref names
    classes = db.relationship('Class', backref='class_teacher', lazy='dynamic')
    subject_assignments = db.relationship('TeacherSubject', backref='assigned_teacher', lazy='dynamic')
    sessions = db.relationship('ClassSession', backref='session_teacher', lazy='dynamic')
    notifications = db.relationship('TeacherNotification', backref='sender_teacher', lazy='dynamic')
    
# models.py - Fix the relationship naming conflicts

class Subject(db.Model):
    """Subject model for courses/classes"""
    __tablename__ = 'subjects'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    code = db.Column(db.String(20), unique=True, nullable=False)
    description = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    teacher_assignments = db.relationship('TeacherSubject', backref='subject_info', lazy='dynamic')
    sessions = db.relationship('ClassSession', backref='subject_info', lazy='dynamic')
    notifications = db.relationship('TeacherNotification', backref='notification_subject', lazy='dynamic')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'code': self.code,
            'description': self.description
        }


class TeacherSubject(db.Model):
    """Teacher to Subject assignment"""
    __tablename__ = 'teacher_subjects'
    
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships - avoid naming conflicts
    teacher = db.relationship('Teacher', backref='teacher_assignments')
    subject = db.relationship('Subject', backref='subject_assignments')
    assigned_class = db.relationship('Class', backref='subject_teacher_assignments')

class ClassSession(db.Model):
    """Attendance session for a specific class/subject/teacher"""
    __tablename__ = 'class_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=False)
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'), nullable=False)
    
    # Session timing
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    
    # Repeat options
    repeat_type = db.Column(db.String(20), default='once')
    repeat_days = db.Column(db.String(50))
    repeat_until = db.Column(db.Date)
    
    # Camera settings
    camera_id = db.Column(db.Integer, db.ForeignKey('cameras.id'))
    line_position = db.Column(db.Float, default=0.5)
    
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships - avoid naming conflicts
    subject = db.relationship('Subject', backref='subject_sessions')
    teacher = db.relationship('Teacher', backref='teacher_sessions')
    assigned_class = db.relationship('Class', backref='class_sessions')
    camera = db.relationship('Camera', backref='camera_sessions')
    creator = db.relationship('User', foreign_keys=[created_by])
    attendance_records = db.relationship('SessionAttendance', backref='session_record', lazy='dynamic')
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'subject': self.subject.name if self.subject else None,
            'subject_id': self.subject_id,
            'teacher_name': self.teacher.user.full_name if self.teacher and self.teacher.user else None,
            'class_name': self.assigned_class.name if self.assigned_class else None,
            'start_time': self.start_time.strftime('%H:%M'),
            'end_time': self.end_time.strftime('%H:%M'),
            'repeat_type': self.repeat_type,
            'repeat_days': self.repeat_days,
            'repeat_until': self.repeat_until.isoformat() if self.repeat_until else None,
            'is_active': self.is_active
        }
    
    def is_active_at_time(self, timestamp):
        """Check if session is active at given timestamp"""
        if not self.is_active:
            return False
        
        current_time = timestamp.time()
        if not (self.start_time <= current_time <= self.end_time):
            return False
        
        if self.repeat_type == 'once':
            return True
        elif self.repeat_type == 'daily':
            if self.repeat_until and timestamp.date() > self.repeat_until:
                return False
            return True
        elif self.repeat_type == 'weekly':
            if self.repeat_until and timestamp.date() > self.repeat_until:
                return False
            if self.repeat_days:
                weekday = timestamp.weekday()
                allowed_days = [int(d) for d in self.repeat_days.split(',')]
                return weekday in allowed_days
            return True
        return False

class SessionAttendance(db.Model):
    """Attendance records for specific class sessions"""
    __tablename__ = 'session_attendances'
    
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('class_sessions.id'), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    status = db.Column(db.String(10))
    check_in_time = db.Column(db.DateTime)
    check_out_time = db.Column(db.DateTime)
    duration_minutes = db.Column(db.Integer)
    confidence = db.Column(db.Float)
    marked_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    marked_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    student = db.relationship('Student', backref='session_attendances')
    marker = db.relationship('User', foreign_keys=[marked_by])
    session = db.relationship('ClassSession', backref='session_attendance_records')

class TeacherNotification(db.Model):
    """Notifications sent by teachers to students"""
    __tablename__ = 'teacher_notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    subject_id = db.Column(db.Integer, db.ForeignKey('subjects.id'))
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'))
    target_all = db.Column(db.Boolean, default=True)
    target_students = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    
    teacher = db.relationship('Teacher', backref='teacher_notifications')
    subject = db.relationship('Subject', backref='subject_notifications')
    target_class = db.relationship('Class', backref='class_notifications')
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'subject': self.subject.name if self.subject else None,
            'class_name': self.target_class.name if self.target_class else None,
            'created_at': self.created_at.isoformat(),
            'expires_at': self.expires_at.isoformat() if self.expires_at else None
        }

class StudentNotificationRead(db.Model):
    """Track which students have read notifications"""
    __tablename__ = 'student_notification_reads'
    
    id = db.Column(db.Integer, primary_key=True)
    notification_id = db.Column(db.Integer, db.ForeignKey('teacher_notifications.id'))
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'))
    read_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    notification = db.relationship('TeacherNotification')
    student = db.relationship('Student')
    
class Class(db.Model):
    __tablename__ = 'classes'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    section = db.Column(db.String(10))
    teacher_id = db.Column(db.Integer, db.ForeignKey('teachers.id'))
    academic_year = db.Column(db.String(20))
    room_number = db.Column(db.String(20))
    is_active = db.Column(db.Boolean, default=True)
    
    # Relationships - avoid naming conflicts
    enrolled_students = db.relationship('Student', backref='current_class', lazy='dynamic')
    teacher_assignments = db.relationship('TeacherSubject', backref='assignment_class', lazy='dynamic')
    sessions = db.relationship('ClassSession', backref='session_class', lazy='dynamic')
    notifications = db.relationship('TeacherNotification', backref='target_class_info', lazy='dynamic')
    teacher = db.relationship('Teacher', backref='teacher_classes', foreign_keys=[teacher_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'section': self.section,
            'academic_year': self.academic_year,
            'teacher_name': self.teacher.user.full_name if self.teacher else None,
            'student_count': self.enrolled_students.count()
        }


class Parent(db.Model):
    __tablename__ = 'parents'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    occupation = db.Column(db.String(100))
    alternate_phone = db.Column(db.String(20))
    address = db.Column(db.Text)
    
    # Relationships
    children = db.relationship('Student', backref='parent', lazy='dynamic')

class Student(db.Model):
    __tablename__ = 'students'
    
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    admission_number = db.Column(db.String(50), unique=True, nullable=False)
    roll_number = db.Column(db.String(20))
    class_id = db.Column(db.Integer, db.ForeignKey('classes.id'))
    parent_id = db.Column(db.Integer, db.ForeignKey('parents.id'))
    date_of_birth = db.Column(db.Date)
    gender = db.Column(db.String(10))
    address = db.Column(db.Text)
    
    # Face recognition data
    face_token = db.Column(db.String(100), unique=True)
    face_encodings = db.Column(db.Text)  # JSON array of encodings
    face_images = db.Column(db.Text)  # JSON array of image paths
    
    # Status
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, onupdate=datetime.utcnow)
    
    # Relationships
    attendance_records = db.relationship('Attendance', backref='student', lazy='dynamic')
    notifications = db.relationship('Notification', backref='student', lazy='dynamic')
    
    def get_face_encodings(self):
        return json.loads(self.face_encodings) if self.face_encodings else []
    
    def set_face_encodings(self, encodings):
        self.face_encodings = json.dumps(encodings)
    
    def get_face_images(self):
        return json.loads(self.face_images) if self.face_images else []
    
    def set_face_images(self, images):
        self.face_images = json.dumps(images)
    
    def to_dict(self):
        return {
            'id': self.id,
            'admission_number': self.admission_number,
            'roll_number': self.roll_number,
            'name': self.user.full_name if self.user else None,
            'class_name': self.current_class.name if self.current_class else None,
            'class_id': self.class_id,
            'parent_name': self.parent.user.full_name if self.parent and self.parent.user else None,
            'is_active': self.is_active
        }

class Attendance(db.Model):
    __tablename__ = 'attendances'
    
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    status = db.Column(db.String(10))  # 'in' or 'out'
    confidence = db.Column(db.Float)
    verified = db.Column(db.Boolean, default=False)
    is_false_positive = db.Column(db.Boolean, default=False)
    camera_id = db.Column(db.String(50))
    image_path = db.Column(db.String(200))
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        db.Index('idx_student_date', 'student_id', db.text('date(timestamp)')),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'student_id': self.student_id,
            'student_name': self.student.user.full_name if self.student and self.student.user else None,
            'timestamp': self.timestamp.isoformat(),
            'status': self.status,
            'confidence': self.confidence,
            'verified': self.verified
        }

class AttendanceSession(db.Model):
    """Attendance session configuration"""
    __tablename__ = 'attendance_sessions'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    start_date = db.Column(db.Date, nullable=False)
    end_date = db.Column(db.Date, nullable=False)
    start_time = db.Column(db.Time, nullable=False)
    end_time = db.Column(db.Time, nullable=False)
    days_of_week = db.Column(db.String(50))  # Comma-separated: "0,1,2,3,4" (Mon-Fri)
    is_active = db.Column(db.Boolean, default=True)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    creator = db.relationship('User', foreign_keys=[created_by])
    
    def is_within_session(self, timestamp):
        """Check if a timestamp falls within this session"""
        if not self.is_active:
            return False
        
        # Check date range
        if timestamp.date() < self.start_date or timestamp.date() > self.end_date:
            return False
        
        # Check day of week
        day = timestamp.weekday()
        allowed_days = [int(d) for d in self.days_of_week.split(',') if d.strip()]
        if day not in allowed_days:
            return False
        
        # Check time range
        time = timestamp.time()
        
        # Handle sessions that cross midnight
        if self.start_time <= self.end_time:
            return self.start_time <= time <= self.end_time
        else:
            # Session crosses midnight (e.g., 22:00 to 02:00)
            return time >= self.start_time or time <= self.end_time

class Announcement(db.Model):
    __tablename__ = 'announcements'
    
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    target_role = db.Column(db.String(20))  # 'all', 'students', 'parents', 'teachers'
    target_class_id = db.Column(db.Integer, db.ForeignKey('classes.id'))  # Null for all classes
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime)
    is_pinned = db.Column(db.Boolean, default=False)
    
    creator = db.relationship('User', foreign_keys=[created_by])
    target_class = db.relationship('Class', foreign_keys=[target_class_id])
    
    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'content': self.content,
            'target_role': self.target_role,
            'created_at': self.created_at.isoformat(),
            'created_by': self.creator.full_name if self.creator else 'System',
            'is_pinned': self.is_pinned
        }

class AnnouncementRead(db.Model):
    """Track which users have read which announcements"""
    __tablename__ = 'announcement_reads'
    
    id = db.Column(db.Integer, primary_key=True)
    announcement_id = db.Column(db.Integer, db.ForeignKey('announcements.id'))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    read_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    announcement = db.relationship('Announcement')
    user = db.relationship('User')

class Notification(db.Model):
    __tablename__ = 'notifications'
    
    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey('students.id'))
    recipient_type = db.Column(db.String(20))  # parent, teacher, admin
    recipient_id = db.Column(db.Integer)  # User ID
    type = db.Column(db.String(50))  # absence, low_attendance, daily_report, welcome
    channel = db.Column(db.String(20))  # email, sms, both
    subject = db.Column(db.String(200))
    message = db.Column(db.Text)
    status = db.Column(db.String(20), default='pending')  # pending, sent, failed
    sent_at = db.Column(db.DateTime)
    error_message = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Camera(db.Model):
    __tablename__ = 'cameras'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    ip_address = db.Column(db.String(50))
    rtsp_url = db.Column(db.String(200))
    direction = db.Column(db.String(10))  # in, out
    location = db.Column(db.String(100))
    is_active = db.Column(db.Boolean, default=True)
    last_online = db.Column(db.DateTime)
    settings = db.Column(db.Text)  # JSON settings
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def get_settings(self):
        return json.loads(self.settings) if self.settings else {}
    
    def set_settings(self, settings_dict):
        self.settings = json.dumps(settings_dict)

class Recording(db.Model):
    __tablename__ = 'recordings'
    
    id = db.Column(db.Integer, primary_key=True)
    camera_id = db.Column(db.Integer, db.ForeignKey('cameras.id'))
    start_time = db.Column(db.DateTime, index=True)
    end_time = db.Column(db.DateTime)
    file_path = db.Column(db.String(200))
    size_mb = db.Column(db.Float)
    motion_detected = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    camera = db.relationship('Camera', backref='recordings')

class FalsePositiveLog(db.Model):
    __tablename__ = 'false_positive_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    detected_student_id = db.Column(db.Integer, db.ForeignKey('students.id'))
    actual_student_id = db.Column(db.Integer, db.ForeignKey('students.id'))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    confidence = db.Column(db.Float)
    image_path = db.Column(db.String(200))
    verified_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    verified_at = db.Column(db.DateTime)
    notes = db.Column(db.Text)
    
    detected_student = db.relationship('Student', foreign_keys=[detected_student_id])
    actual_student = db.relationship('Student', foreign_keys=[actual_student_id])
    verifier = db.relationship('User', foreign_keys=[verified_by])

class SystemLog(db.Model):
    __tablename__ = 'system_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    level = db.Column(db.String(20))
    module = db.Column(db.String(50))
    message = db.Column(db.Text)
    details = db.Column(db.Text)
    ip_address = db.Column(db.String(50))
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    user = db.relationship('User', foreign_keys=[user_id])