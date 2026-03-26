import os
import logging
import json
import time
import threading
from datetime import datetime, timedelta, date
from functools import wraps

from liveness_attendance import LivenessAttendanceFaceRecognition
from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, session, send_file, Response
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_migrate import Migrate
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename
from sqlalchemy import func, and_, or_, desc, extract
import pandas as pd
import numpy as np
import io
import threading
import queue


from config import Config
from models import Subject, db, User, Student, Teacher, Parent, Class, Attendance, AttendanceSession, Notification, Camera, Recording, FalsePositiveLog, Institute, SystemLog, Announcement, TeacherNotification, StudentNotificationRead, TeacherSubject, ClassSession, SessionAttendance
from face_recognition_system import FaceRecognitionAPI
from notifications import NotificationService
from nvr_integration import NVRSystem
from camera_manager import CameraManager
from utils import *

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(Config.LOG_DIR, 'attendance.log')),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Liveness attendance system
liveness_system = None
liveness_thread = None
liveness_running = False

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)
socketio = SocketIO(app, cors_allowed_origins="*")

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'admin_login'
login_manager.login_message = 'Please log in to access this page.'

# Initialize services
face_system = FaceRecognitionAPI(
    api_key=app.config['FACE_API_KEY'],
    api_secret=app.config['FACE_API_SECRET']
)
notification_service = NotificationService(app.config)
nvr_system = NVRSystem(app.config)

# Global variables for real-time processing
active_sessions = {}
attendance_queue = []

# ==================== Camera Manager Initialization ====================
camera_manager = CameraManager(app)

def load_cameras_from_db():
    """Load cameras from database when needed"""
    with app.app_context():
        cameras = Camera.query.filter_by(is_active=True).all()
        for camera in cameras:
            camera_manager.add_camera(
                camera_id=f"cam_{camera.id}",
                name=camera.name,
                ip_address=camera.ip_address,
                username=app.config.get('NVR_USERNAME'),
                password=app.config.get('NVR_PASSWORD')
            )
            camera_manager.connect_camera(f"cam_{camera.id}")
        logger.info(f"Loaded {len(cameras)} cameras from database")

# ==================== Helper Functions ====================

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash('Access denied. Admin privileges required.', 'danger')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def teacher_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role not in ['admin', 'teacher']:
            flash('Access denied. Teacher privileges required.', 'danger')
            return redirect(url_for('teacher_login'))
        return f(*args, **kwargs)
    return decorated_function

def log_activity(level='INFO', module='system', message='', details=None):
    """Log system activity"""
    try:
        log = SystemLog(
            level=level,
            module=module,
            message=message,
            details=json.dumps(details) if details else None,
            ip_address=request.remote_addr if request else None,
            user_id=current_user.id if current_user.is_authenticated else None
        )
        db.session.add(log)
        db.session.commit()
    except Exception as e:
        logger.error(f"Error logging activity: {str(e)}")

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# ==================== Schedule Camera Functions ====================

def schedule_camera_for_session(session_id):
    """Schedule camera to start at session time"""
    session = ClassSession.query.get(session_id)
    if not session or not session.camera_id:
        return
    
    now = datetime.now()
    session_time = datetime.combine(now.date(), session.start_time)
    
    if session_time < now:
        if session.repeat_type == 'daily':
            session_time += timedelta(days=1)
        elif session.repeat_type == 'weekly' and session.repeat_days:
            current_weekday = now.weekday()
            allowed_days = [int(d) for d in session.repeat_days.split(',')]
            for days_ahead in range(1, 8):
                next_day = (current_weekday + days_ahead) % 7
                if next_day in allowed_days:
                    session_time = datetime.combine(now.date() + timedelta(days=days_ahead), session.start_time)
                    break
    
    delay = (session_time - now).total_seconds()
    if delay > 0:
        threading.Timer(delay, start_camera_for_session, args=[session_id]).start()


def start_camera_for_session(session_id):
    """Start camera for a specific session"""
    with app.app_context():
        session = ClassSession.query.get(session_id)
        if session and session.camera_id:
            camera = Camera.query.get(session.camera_id)
            if camera:
                camera_manager.add_camera(
                    camera_id=f"cam_{camera.id}",
                    name=camera.name,
                    ip_address=camera.ip_address,
                    username=app.config.get('NVR_USERNAME'),
                    password=app.config.get('NVR_PASSWORD')
                )
                camera_manager.connect_camera(f"cam_{camera.id}")
                camera_manager.set_line_position(f"cam_{camera.id}", int(session.line_position * 480))
                
                camera_manager.set_active_session(
                    session.id,
                    session.start_time,
                    session.end_time
                )
                
                logger.info(f"Camera started for session: {session.name}")


def send_notifications_to_students(notification_id):
    """Send notifications to students via WebSocket"""
    with app.app_context():
        notification = TeacherNotification.query.get(notification_id)
        if not notification:
            return
        
        students = []
        if notification.target_all:
            if notification.class_id:
                students = Student.query.filter_by(class_id=notification.class_id, is_active=True).all()
            elif notification.subject_id:
                assignment = TeacherSubject.query.filter_by(subject_id=notification.subject_id).first()
                if assignment:
                    students = assignment.assigned_class.enrolled_students.filter_by(is_active=True).all()
        elif notification.target_students:
            student_ids = json.loads(notification.target_students)
            students = Student.query.filter(Student.id.in_(student_ids)).all()
        elif notification.class_id:
            students = Student.query.filter_by(class_id=notification.class_id, is_active=True).all()
        
        for student in students:
            socketio.emit('new_notification', {
                'student_id': student.id,
                'notification_id': notification.id,
                'title': notification.title,
                'content': notification.content,
                'subject': notification.subject.name if notification.subject else None,
                'timestamp': notification.created_at.isoformat()
            })


def send_absence_sms(session_id, student_id):
    """Send SMS to parent about student absence"""
    try:
        session = ClassSession.query.get(session_id)
        student = Student.query.get(student_id)
        
        if not session or not student or not student.parent:
            return
        
        parent = student.parent.user
        
        if parent.phone:
            message = f"Dear Parent, {student.user.full_name} was absent for {session.subject.name} class on {datetime.now().strftime('%d/%m/%Y')} from {session.start_time.strftime('%H:%M')} to {session.end_time.strftime('%H:%M')}. Please ensure regular attendance."
            
            notification_service.send_sms(parent.phone, message)
            logger.info(f"SMS sent to {parent.phone} about {student.user.full_name}'s absence")
            
    except Exception as e:
        logger.error(f"Error sending absence SMS: {str(e)}")


def mark_session_attendance(session_id, student_id, status, duration=None):
    """Mark attendance for a specific session"""
    try:
        session = ClassSession.query.get(session_id)
        if not session:
            return
        
        attendance = SessionAttendance(
            session_id=session_id,
            student_id=student_id,
            status=status,
            duration_minutes=duration,
            confidence=95.0,
            marked_by=session.teacher_id,
            marked_at=datetime.now()
        )
        
        db.session.add(attendance)
        db.session.commit()
        
        if status == 'absent':
            send_absence_sms(session_id, student_id)
        
        main_attendance = Attendance(
            student_id=student_id,
            status='in' if status == 'present' else 'absent',
            confidence=95.0,
            camera_id=f"session_{session_id}",
            verified=True,
            timestamp=datetime.now()
        )
        db.session.add(main_attendance)
        db.session.commit()
        
        return True
        
    except Exception as e:
        logger.error(f"Error marking session attendance: {str(e)}")
        return False

# ==================== Routes - Admin ====================

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        elif current_user.role == 'teacher':
            return redirect(url_for('teacher_dashboard'))
        elif current_user.role == 'parent':
            return redirect(url_for('parent_dashboard'))
        elif current_user.role == 'student':
            return redirect(url_for('student_dashboard'))
    return redirect(url_for('admin_login'))


@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated:
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password) and user.role == 'admin':
            login_user(user, remember=remember)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            log_activity('INFO', 'auth', f'Admin login successful: {username}')
            return redirect(url_for('admin_dashboard'))
        
        log_activity('WARNING', 'auth', f'Failed admin login attempt: {username}')
        flash('Invalid username or password', 'danger')
    
    return render_template('admin/login.html')


@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    today = date.today()
    now = datetime.now()
    
    total_students = Student.query.filter_by(is_active=True).count()
    total_teachers = Teacher.query.count()
    total_classes = Class.query.filter_by(is_active=True).count()
    
    today_checkins = Attendance.query.filter(
        func.date(Attendance.timestamp) == today,
        Attendance.status == 'in'
    ).count()
    
    today_checkouts = Attendance.query.filter(
        func.date(Attendance.timestamp) == today,
        Attendance.status == 'out'
    ).count()
    
    currently_present = Attendance.query.filter(
        func.date(Attendance.timestamp) == today,
        Attendance.status == 'in'
    ).distinct(Attendance.student_id).count()
    
    recent_attendance = Attendance.query.order_by(
        Attendance.timestamp.desc()
    ).limit(10).all()
    
    thirty_days_ago = now - timedelta(days=30)
    low_attendance = []
    
    students = Student.query.filter_by(is_active=True).all()
    for student in students:
        total_days = db.session.query(func.count(func.distinct(func.date(Attendance.timestamp)))).filter(
            Attendance.student_id == student.id,
            Attendance.timestamp >= thirty_days_ago,
            Attendance.status == 'in'
        ).scalar() or 0
        
        if total_days > 0:
            percentage = (total_days / 30) * 100
            if percentage < app.config['MINIMUM_ATTENDANCE']:
                low_attendance.append({
                    'student': student,
                    'percentage': round(percentage, 1)
                })
    
    class_stats = []
    classes = Class.query.filter_by(is_active=True).all()
    for class_ in classes:
        total = Student.query.filter_by(class_id=class_.id, is_active=True).count()
        present = Attendance.query.filter(
            Attendance.student_id.in_(
                db.session.query(Student.id).filter_by(class_id=class_.id)
            ),
            func.date(Attendance.timestamp) == today,
            Attendance.status == 'in'
        ).distinct(Attendance.student_id).count()
        
        class_stats.append({
            'class': class_,
            'total': total,
            'present': present,
            'percentage': round((present / total * 100), 1) if total > 0 else 0
        })
    
    institute = Institute.query.first()
    institute_name = institute.name if institute else app.config['INSTITUTE_NAME']
    
    return render_template(
        'admin/dashboard.html',
        total_students=total_students,
        total_teachers=total_teachers,
        total_classes=total_classes,
        today_checkins=today_checkins,
        today_checkouts=today_checkouts,
        currently_present=currently_present,
        recent_attendance=recent_attendance,
        low_attendance=low_attendance,
        class_stats=class_stats,
        classes=classes,
        today=today.strftime('%Y-%m-%d'),
        institute_name=institute_name,
        min_attendance=app.config['MINIMUM_ATTENDANCE'],
        now=now
    )


# ==================== Routes - Teacher ====================

@app.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    if current_user.is_authenticated and current_user.role == 'teacher':
        return redirect(url_for('teacher_dashboard'))
    
    if request.method == 'POST':
        employee_id = request.form.get('employee_id')
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        teacher = Teacher.query.filter_by(employee_id=employee_id).first()
        
        if teacher and teacher.user and teacher.user.check_password(password):
            login_user(teacher.user, remember=remember)
            teacher.user.last_login = datetime.utcnow()
            db.session.commit()
            
            log_activity('INFO', 'auth', f'Teacher login successful: {employee_id}')
            return redirect(url_for('teacher_dashboard'))
        
        flash('Invalid Employee ID or password', 'danger')
    
    return render_template('teacher/login.html')


@app.route('/teacher/dashboard')
@login_required
def teacher_dashboard():
    if current_user.role != 'teacher':
        return redirect(url_for('index'))
    
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    if not teacher:
        flash('Teacher profile not found', 'danger')
        return redirect(url_for('logout'))
    
    today = date.today()
    now = datetime.now()
    
    today_sessions = ClassSession.query.filter(
        ClassSession.teacher_id == teacher.id,
        ClassSession.is_active == True
    ).all()
    
    active_today = [s for s in today_sessions if s.is_active_at_time(now)]
    
    total_students = 0
    for assignment in teacher.teacher_assignments:
        total_students += assignment.assigned_class.enrolled_students.filter_by(is_active=True).count()
    
    thirty_days_ago = now - timedelta(days=30)
    avg_attendance = db.session.query(func.avg(SessionAttendance.duration_minutes)).filter(
        SessionAttendance.session_id.in_([s.id for s in today_sessions]),
        SessionAttendance.marked_at >= thirty_days_ago
    ).scalar() or 0
    
    recent_notifications = TeacherNotification.query.filter_by(
        teacher_id=teacher.id
    ).order_by(TeacherNotification.created_at.desc()).limit(5).all()
    
    return render_template('teacher/dashboard.html',
                         teacher=teacher,
                         total_students=total_students,
                         today_sessions=len(active_today),
                         avg_attendance=round(avg_attendance, 1) if avg_attendance else 0,
                         today_sessions_list=active_today[:10],
                         recent_notifications=recent_notifications)


@app.route('/teacher/create-session', methods=['GET', 'POST'])
@login_required
def teacher_create_session():
    if current_user.role != 'teacher':
        return redirect(url_for('index'))
    
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    if not teacher:
        flash('Teacher profile not found', 'danger')
        return redirect(url_for('logout'))
    
    cameras = Camera.query.filter_by(is_active=True).all()
    
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            subject_id = request.form.get('subject_id')
            start_time = datetime.strptime(request.form.get('start_time'), '%H:%M').time()
            end_time = datetime.strptime(request.form.get('end_time'), '%H:%M').time()
            repeat_type = request.form.get('repeat_type', 'once')
            repeat_days = ','.join(request.form.getlist('repeat_days')) if repeat_type == 'weekly' else None
            repeat_until = request.form.get('repeat_until')
            camera_id = request.form.get('camera_id') or None
            line_position = float(request.form.get('line_position', 0.5))
            
            assignment = TeacherSubject.query.filter_by(
                teacher_id=teacher.id,
                subject_id=subject_id
            ).first()
            
            if not assignment:
                flash('Invalid subject selection', 'danger')
                return redirect(request.url)
            
            session = ClassSession(
                name=name,
                subject_id=subject_id,
                teacher_id=teacher.id,
                class_id=assignment.class_id,
                start_time=start_time,
                end_time=end_time,
                repeat_type=repeat_type,
                repeat_days=repeat_days,
                repeat_until=datetime.strptime(repeat_until, '%Y-%m-%d').date() if repeat_until else None,
                camera_id=camera_id,
                line_position=line_position,
                is_active=True,
                created_by=current_user.id
            )
            
            db.session.add(session)
            db.session.commit()
            
            if camera_id:
                schedule_camera_for_session(session.id)
            
            flash('Session created successfully!', 'success')
            return redirect(url_for('teacher_dashboard'))
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error creating session: {str(e)}', 'danger')
    
    return render_template('teacher/create_session.html',
                         teacher=teacher,
                         cameras=cameras)


@app.route('/teacher/notifications', methods=['GET', 'POST'])
@login_required
def teacher_notifications():
    if current_user.role != 'teacher':
        return redirect(url_for('index'))
    
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    if not teacher:
        flash('Teacher profile not found', 'danger')
        return redirect(url_for('logout'))
    
    if request.method == 'POST':
        try:
            title = request.form.get('title')
            content = request.form.get('content')
            target_type = request.form.get('target_type')
            subject_id = request.form.get('subject_id')
            class_id = request.form.get('class_id')
            send_sms = request.form.get('send_sms') == 'on'
            
            notification = TeacherNotification(
                teacher_id=teacher.id,
                title=title,
                content=content,
                subject_id=subject_id if subject_id else None,
                class_id=class_id if class_id else None,
                target_all=(target_type == 'all'),
                target_students=json.dumps(request.form.getlist('student_ids')) if target_type == 'specific' else None
            )
            
            db.session.add(notification)
            db.session.commit()
            
            send_notifications_to_students(notification.id)
            
            flash('Notification sent successfully!', 'success')
            
        except Exception as e:
            db.session.rollback()
            flash(f'Error sending notification: {str(e)}', 'danger')
    
    subjects = teacher.teacher_assignments.all()
    classes = list(set([a.assigned_class for a in subjects]))
    
    return render_template('teacher/notifications.html',
                         teacher=teacher,
                         subjects=subjects,
                         classes=classes)


@app.route('/teacher/attendance')
@login_required
def teacher_take_attendance():
    if current_user.role != 'teacher':
        return redirect(url_for('index'))
    
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    if not teacher:
        flash('Teacher profile not found', 'danger')
        return redirect(url_for('logout'))
    
    now = datetime.now()
    active_sessions = ClassSession.query.filter(
        ClassSession.teacher_id == teacher.id,
        ClassSession.is_active == True
    ).all()
    
    active_now = [s for s in active_sessions if s.is_active_at_time(now)]
    upcoming = [s for s in active_sessions if s.start_time > now.time()]
    past = [s for s in active_sessions if s.end_time < now.time()]
    
    cameras = Camera.query.filter_by(is_active=True).all()
    
    return render_template('teacher/attendance.html',
                         teacher=teacher,
                         active_sessions=active_now,
                         upcoming_sessions=upcoming,
                         past_sessions=past,
                         cameras=cameras)


@app.route('/teacher/session/<int:session_id>')
@login_required
def teacher_session_detail(session_id):
    if current_user.role != 'teacher':
        return redirect(url_for('index'))
    
    session = ClassSession.query.get_or_404(session_id)
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    
    if session.teacher_id != teacher.id:
        flash('Access denied', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    attendance_records = SessionAttendance.query.filter_by(session_id=session_id).all()
    students = Student.query.filter_by(class_id=session.class_id, is_active=True).all()
    
    attendance_map = {a.student_id: a for a in attendance_records}
    
    attendance_data = []
    for student in students:
        record = attendance_map.get(student.id)
        attendance_data.append({
            'student': student,
            'status': record.status if record else 'absent',
            'check_in_time': record.check_in_time if record else None,
            'check_out_time': record.check_out_time if record else None,
            'duration_minutes': record.duration_minutes if record else None,
            'confidence': record.confidence if record else None
        })
    
    present_count = len([a for a in attendance_data if a['status'] == 'present'])
    absent_count = len([a for a in attendance_data if a['status'] == 'absent'])
    attendance_rate = (present_count / len(students) * 100) if students else 0
    
    return render_template('teacher/session_detail.html',
                         session=session,
                         teacher=teacher,
                         attendance_data=attendance_data,
                         present_count=present_count,
                         absent_count=absent_count,
                         attendance_rate=round(attendance_rate, 1),
                         total_students=len(students))


@app.route('/teacher/reports')
@login_required
def teacher_reports():
    if current_user.role != 'teacher':
        return redirect(url_for('index'))
    
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    if not teacher:
        flash('Teacher profile not found', 'danger')
        return redirect(url_for('logout'))
    
    subjects = teacher.teacher_assignments.all()
    
    start_date = request.args.get('start_date', (date.today() - timedelta(days=30)).isoformat())
    end_date = request.args.get('end_date', date.today().isoformat())
    subject_id = request.args.get('subject_id', type=int)
    class_id = request.args.get('class_id', type=int)
    format_type = request.args.get('format', 'html')
    
    query = SessionAttendance.query.join(ClassSession).filter(
        ClassSession.teacher_id == teacher.id,
        SessionAttendance.marked_at >= datetime.strptime(start_date, '%Y-%m-%d'),
        SessionAttendance.marked_at <= datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
    )
    
    if subject_id:
        query = query.filter(ClassSession.subject_id == subject_id)
    
    if class_id:
        query = query.filter(ClassSession.class_id == class_id)
    
    attendance_records = query.all()
    
    total_students = set()
    present_students = set()
    for record in attendance_records:
        total_students.add(record.student_id)
        if record.status == 'present':
            present_students.add(record.student_id)
    
    classes = list(set([a.assigned_class for a in teacher.teacher_assignments]))
    
    report_data = []
    for record in attendance_records:
        report_data.append({
            'Date': record.marked_at.strftime('%Y-%m-%d'),
            'Student Name': record.student.user.full_name if record.student.user else 'Unknown',
            'Admission No': record.student.admission_number,
            'Subject': record.session.subject.name if record.session.subject else 'N/A',
            'Class': record.session.assigned_class.name if record.session.assigned_class else 'N/A',
            'Status': record.status.upper(),
            'Duration': f"{record.duration_minutes} mins" if record.duration_minutes else 'N/A',
            'Confidence': f"{record.confidence}%" if record.confidence else 'N/A'
        })
    
    institute = Institute.query.first()
    institute_name = institute.name if institute else app.config['INSTITUTE_NAME']
    
    if format_type == 'csv':
        df = pd.DataFrame(report_data)
        output = io.StringIO()
        df.to_csv(output, index=False)
        return Response(
            output.getvalue(),
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=attendance_report_{start_date}_to_{end_date}.csv'}
        )
    elif format_type == 'excel':
        df = pd.DataFrame(report_data)
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Attendance Report')
        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'attendance_report_{start_date}_to_{end_date}.xlsx'
        )
    
    return render_template('teacher/reports.html',
                         teacher=teacher,
                         subjects=subjects,
                         classes=classes,
                         attendance_records=attendance_records,
                         report_data=report_data,
                         total_students=len(total_students),
                         present_students=len(present_students),
                         start_date=start_date,
                         end_date=end_date,
                         selected_subject=subject_id,
                         selected_class=class_id,
                         institute_name=institute_name)


@app.route('/teacher/class/<int:class_id>')
@login_required
def teacher_class_view(class_id):
    if current_user.role != 'teacher':
        return redirect(url_for('teacher_login'))
    
    class_obj = Class.query.get_or_404(class_id)
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    
    if class_obj.teacher_id != teacher.id:
        flash('Access denied', 'danger')
        return redirect(url_for('teacher_dashboard'))
    
    students = Student.query.filter_by(class_id=class_id, is_active=True).all()
    today = date.today()
    
    attendance_data = []
    for student in students:
        check_in = Attendance.query.filter(
            Attendance.student_id == student.id,
            func.date(Attendance.timestamp) == today,
            Attendance.status == 'in'
        ).first()
        
        check_out = Attendance.query.filter(
            Attendance.student_id == student.id,
            func.date(Attendance.timestamp) == today,
            Attendance.status == 'out'
        ).first()
        
        attendance_data.append({
            'student': student,
            'check_in': check_in.timestamp if check_in else None,
            'check_out': check_out.timestamp if check_out else None
        })
    
    return render_template('teacher/class_view.html',
                         class_obj=class_obj,
                         attendance_data=attendance_data,
                         today=today.strftime('%Y-%m-%d'))


# ==================== Routes - Student ====================

@app.route('/student/login', methods=['GET', 'POST'])
def student_login():
    if current_user.is_authenticated and current_user.role == 'student':
        return redirect(url_for('student_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('admission_number')
        password = request.form.get('password')
        remember = request.form.get('remember', False)
        
        student = Student.query.filter_by(admission_number=username, is_active=True).first()
        
        if not student:
            student = Student.query.join(User).filter(
                User.full_name.ilike(f'%{username}%'),
                Student.is_active == True
            ).first()
        
        if student and student.user and student.user.check_password(password):
            login_user(student.user, remember=remember)
            student.user.last_login = datetime.utcnow()
            db.session.commit()
            
            log_activity('INFO', 'auth', f'Student login successful: {student.admission_number}')
            flash(f'Welcome back, {student.user.full_name}!', 'success')
            return redirect(url_for('student_dashboard'))
        
        flash('Invalid username/admission number or password', 'danger')
    
    return render_template('student/login.html')


@app.route('/student/dashboard')
@login_required
def student_dashboard():
    if current_user.role != 'student':
        return redirect(url_for('index'))
    
    student = current_user.student
    
    total_days = Attendance.query.filter_by(student_id=student.id, status='in').count()
    
    month_start = date.today().replace(day=1)
    this_month = Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.status == 'in',
        func.date(Attendance.timestamp) >= month_start
    ).count()
    
    thirty_days_ago = datetime.now() - timedelta(days=30)
    last_30_days = Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.status == 'in',
        Attendance.timestamp >= thirty_days_ago
    ).distinct(func.date(Attendance.timestamp)).count()
    
    attendance_percentage = (last_30_days / 30) * 100 if last_30_days > 0 else 0
    
    recent_records = Attendance.query.filter_by(student_id=student.id).order_by(
        Attendance.timestamp.desc()
    ).all()
    
    attendance_by_date = {}
    for record in recent_records:
        date_str = record.timestamp.strftime('%Y-%m-%d')
        if date_str not in attendance_by_date:
            attendance_by_date[date_str] = {
                'date': date_str,
                'day': record.timestamp.strftime('%A'),
                'check_in': None,
                'check_out': None,
                'status': 'absent'
            }
        
        if record.status == 'in':
            attendance_by_date[date_str]['check_in'] = record.timestamp.strftime('%H:%M:%S')
            attendance_by_date[date_str]['status'] = 'present'
        elif record.status == 'out':
            attendance_by_date[date_str]['check_out'] = record.timestamp.strftime('%H:%M:%S')
    
    recent_attendance = list(attendance_by_date.values())[:10]
    
    announcements = Announcement.query.filter(
        or_(
            Announcement.target_role == 'all',
            Announcement.target_role == 'students'
        ),
        or_(
            Announcement.target_class_id == None,
            Announcement.target_class_id == student.class_id
        )
    ).order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc()).limit(5).all()
    
    return render_template('student/dashboard.html',
                         student=student,
                         total_days=total_days,
                         this_month=this_month,
                         last_30_days=last_30_days,
                         attendance_percentage=round(attendance_percentage, 1),
                         recent_attendance=recent_attendance,
                         announcements=announcements)


@app.route('/student/attendance')
@login_required
def student_attendance():
    if current_user.role != 'student':
        return redirect(url_for('index'))
    
    student = current_user.student
    start_date = request.args.get('start_date', (date.today() - timedelta(days=30)).isoformat())
    end_date = request.args.get('end_date', date.today().isoformat())
    
    records = Attendance.query.filter(
        Attendance.student_id == student.id,
        func.date(Attendance.timestamp) >= start_date,
        func.date(Attendance.timestamp) <= end_date
    ).order_by(Attendance.timestamp.desc()).all()
    
    total_days = Attendance.query.filter_by(student_id=student.id, status='in').count()
    
    month_start = date.today().replace(day=1)
    this_month = Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.status == 'in',
        func.date(Attendance.timestamp) >= month_start
    ).count()
    
    thirty_days_ago = datetime.now() - timedelta(days=30)
    last_30_days = Attendance.query.filter(
        Attendance.student_id == student.id,
        Attendance.status == 'in',
        Attendance.timestamp >= thirty_days_ago
    ).distinct(func.date(Attendance.timestamp)).count()
    
    attendance_percentage = (last_30_days / 30) * 100 if last_30_days > 0 else 0
    
    return render_template('student/attendance.html',
                         student=student,
                         records=records,
                         start_date=start_date,
                         end_date=end_date,
                         total_days=total_days,
                         this_month=this_month,
                         last_30_days=last_30_days,
                         attendance_percentage=round(attendance_percentage, 1))


@app.route('/student/subjects')
@login_required
def student_subjects():
    if current_user.role != 'student':
        return redirect(url_for('index'))
    
    student = current_user.student
    class_obj = student.current_class
    
    subjects = []
    if class_obj:
        assignments = TeacherSubject.query.filter_by(class_id=class_obj.id).all()
        for assignment in assignments:
            subjects.append({
                'subject': assignment.subject,
                'teacher': assignment.teacher.user.full_name if assignment.teacher.user else 'N/A',
                'teacher_id': assignment.teacher.employee_id,
                'sessions': ClassSession.query.filter_by(
                    subject_id=assignment.subject_id,
                    class_id=class_obj.id,
                    is_active=True
                ).all()
            })
    
    return render_template('student/subjects.html', subjects=subjects)


@app.route('/student/subject-attendance/<int:subject_id>')
@login_required
def student_subject_attendance(subject_id):
    if current_user.role != 'student':
        return redirect(url_for('index'))
    
    student = current_user.student
    subject = Subject.query.get_or_404(subject_id)
    
    sessions = ClassSession.query.filter_by(
        subject_id=subject_id,
        class_id=student.class_id
    ).all()
    
    attendance_records = []
    for session in sessions:
        record = SessionAttendance.query.filter_by(
            session_id=session.id,
            student_id=student.id
        ).first()
        
        attendance_records.append({
            'session': session,
            'record': record,
            'status': record.status if record else 'absent'
        })
    
    present_count = len([r for r in attendance_records if r['status'] == 'present'])
    total_count = len(attendance_records)
    percentage = (present_count / total_count * 100) if total_count > 0 else 0
    
    return render_template('student/subject_attendance.html',
                         subject=subject,
                         attendance_records=attendance_records,
                         present_count=present_count,
                         total_count=total_count,
                         percentage=round(percentage, 1))


@app.route('/student/announcements')
@login_required
def student_announcements():
    if current_user.role != 'student':
        return redirect(url_for('index'))
    
    student = current_user.student
    
    announcements = Announcement.query.filter(
        or_(
            Announcement.target_role == 'all',
            Announcement.target_role == 'students'
        ),
        or_(
            Announcement.target_class_id == None,
            Announcement.target_class_id == student.class_id
        )
    ).order_by(Announcement.is_pinned.desc(), Announcement.created_at.desc()).all()
    
    return render_template('student/announcements.html', announcements=announcements)


# ==================== Routes - Parent ====================

@app.route('/parent/login', methods=['GET', 'POST'])
def parent_login():
    if current_user.is_authenticated:
        return redirect(url_for('parent_dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        user = User.query.filter_by(email=email, role='parent').first()
        
        if user and user.check_password(password):
            login_user(user)
            user.last_login = datetime.utcnow()
            db.session.commit()
            
            log_activity('INFO', 'auth', f'Parent login successful: {email}')
            return redirect(url_for('parent_dashboard'))
        
        flash('Invalid email or password', 'danger')
    
    return render_template('parent/login.html')


@app.route('/parent/dashboard')
@login_required
def parent_dashboard():
    if current_user.role != 'parent':
        return redirect(url_for('parent_login'))
    
    parent = Parent.query.filter_by(user_id=current_user.id).first()
    if not parent:
        flash('Parent profile not found', 'danger')
        return redirect(url_for('logout'))
    
    students = parent.children.all()
    today = date.today()
    
    student_attendance = []
    for student in students:
        check_in = Attendance.query.filter(
            Attendance.student_id == student.id,
            func.date(Attendance.timestamp) == today,
            Attendance.status == 'in'
        ).first()
        
        check_out = Attendance.query.filter(
            Attendance.student_id == student.id,
            func.date(Attendance.timestamp) == today,
            Attendance.status == 'out'
        ).first()
        
        month_start = today.replace(day=1)
        month_days = Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.timestamp >= month_start,
            Attendance.status == 'in'
        ).distinct(func.date(Attendance.timestamp)).count()
        
        thirty_days_ago = datetime.now() - timedelta(days=30)
        total_days = Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.timestamp >= thirty_days_ago,
            Attendance.status == 'in'
        ).distinct(func.date(Attendance.timestamp)).count()
        
        percentage = (total_days / 30) * 100 if total_days > 0 else 0
        
        student_attendance.append({
            'student': student,
            'check_in': check_in.timestamp if check_in else None,
            'check_out': check_out.timestamp if check_out else None,
            'month_days': month_days,
            'percentage': round(percentage, 1)
        })
    
    return render_template('parent/dashboard.html', students=student_attendance)


# ==================== Routes - Admin Management ====================

@app.route('/admin/students')
@login_required
@admin_required
def student_list():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    search = request.args.get('search', '')
    class_id = request.args.get('class_id', type=int)
    
    query = Student.query.filter_by(is_active=True)
    
    if search:
        query = query.join(User).filter(
            or_(
                Student.admission_number.ilike(f'%{search}%'),
                User.full_name.ilike(f'%{search}%')
            )
        )
    
    if class_id:
        query = query.filter_by(class_id=class_id)
    
    students = query.order_by(Student.admission_number).paginate(
        page=page, per_page=per_page, error_out=False
    )
    
    classes = Class.query.filter_by(is_active=True).all()
    
    return render_template(
        'admin/students.html',
        students=students,
        classes=classes,
        search=search,
        selected_class=class_id
    )


@app.route('/admin/student/<int:student_id>')
@login_required
@admin_required
def student_profile(student_id):
    student = Student.query.get_or_404(student_id)
    
    total_days = db.session.query(func.count(func.distinct(func.date(Attendance.timestamp)))).filter(
        Attendance.student_id == student_id,
        Attendance.status == 'in'
    ).scalar() or 0
    
    month_start = date.today().replace(day=1)
    this_month = Attendance.query.filter(
        Attendance.student_id == student_id,
        Attendance.status == 'in',
        func.date(Attendance.timestamp) >= month_start
    ).count()
    
    current_year = datetime.now().year
    monthly_stats = []
    
    for month in range(1, 13):
        month_start_date = datetime(current_year, month, 1)
        if month == 12:
            month_end = datetime(current_year + 1, 1, 1)
        else:
            month_end = datetime(current_year, month + 1, 1)
        
        count = Attendance.query.filter(
            Attendance.student_id == student_id,
            Attendance.timestamp >= month_start_date,
            Attendance.timestamp < month_end,
            Attendance.status == 'in'
        ).count()
        
        month_name = month_start_date.strftime('%B')
        monthly_stats.append({
            'month': month_name[:3],
            'count': count
        })
    
    recent_attendance = Attendance.query.filter_by(
        student_id=student_id
    ).order_by(Attendance.timestamp.desc()).limit(30).all()
    
    thirty_days_ago = datetime.now() - timedelta(days=30)
    last_30_days = Attendance.query.filter(
        Attendance.student_id == student_id,
        Attendance.timestamp >= thirty_days_ago,
        Attendance.status == 'in'
    ).distinct(func.date(Attendance.timestamp)).count()
    
    attendance_percentage = (last_30_days / 30) * 100 if last_30_days > 0 else 0
    
    return render_template(
        'admin/student_profile.html',
        student=student,
        total_days=total_days,
        this_month=this_month,
        monthly_stats=monthly_stats,
        recent_attendance=recent_attendance,
        last_30_days=last_30_days,
        attendance_percentage=round(attendance_percentage, 1),
        min_attendance=app.config['MINIMUM_ATTENDANCE']
    )


@app.route('/admin/student/register', methods=['GET', 'POST'])
@login_required
@admin_required
def register_student():
    if request.method == 'POST':
        try:
            admission_number = request.form.get('admission_number')
            full_name = request.form.get('full_name')
            email = request.form.get('email')
            phone = request.form.get('phone')
            
            class_id = request.form.get('class_id')
            if class_id and class_id.strip():
                try:
                    class_id = int(class_id)
                except ValueError:
                    class_id = None
            else:
                class_id = None
                
            roll_number = request.form.get('roll_number')
            date_of_birth = request.form.get('date_of_birth')
            gender = request.form.get('gender')
            address = request.form.get('address')
            
            parent_name = request.form.get('parent_name')
            parent_email = request.form.get('parent_email')
            parent_phone = request.form.get('parent_phone')
            
            face_images = []
            for i in range(1, 4):
                image_data = request.form.get(f'face_image_{i}')
                if image_data:
                    face_images.append(image_data)
            
            if not face_images:
                flash('Please capture at least one face image', 'danger')
                return redirect(request.url)
            
            if Student.query.filter_by(admission_number=admission_number).first():
                flash('Student with this admission number already exists', 'danger')
                return redirect(request.url)
            
            if email and User.query.filter_by(email=email).first():
                flash('Email already registered', 'danger')
                return redirect(request.url)
            
            student_user = User(
                username=admission_number,
                email=email,
                role='student',
                full_name=full_name,
                phone=phone
            )
            student_user.set_password(admission_number)
            db.session.add(student_user)
            db.session.flush()
            
            parent = None
            if parent_email:
                parent_user = User.query.filter_by(email=parent_email).first()
                if not parent_user:
                    parent_user = User(
                        username=parent_email,
                        email=parent_email,
                        role='parent',
                        full_name=parent_name,
                        phone=parent_phone
                    )
                    parent_user.set_password(parent_phone[-6:] if parent_phone else 'parent123')
                    db.session.add(parent_user)
                    db.session.flush()
                    
                    parent = Parent(
                        user_id=parent_user.id,
                        alternate_phone=parent_phone,
                        address=address
                    )
                    db.session.add(parent)
                    db.session.flush()
                else:
                    parent = Parent.query.filter_by(user_id=parent_user.id).first()
            
            saved_images = []
            
            for i, img_data in enumerate(face_images):
                if ',' in img_data:
                    img_data = img_data.split(',')[1]
                
                import base64
                import cv2
                import numpy as np
                
                img_bytes = base64.b64decode(img_data)
                nparr = np.frombuffer(img_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if frame is not None:
                    filename = f"{admission_number}_{i}_{int(time.time())}.jpg"
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    cv2.imwrite(filepath, frame)
                    saved_images.append(filename)
            
            student = Student(
                user_id=student_user.id,
                admission_number=admission_number,
                roll_number=roll_number,
                class_id=class_id,
                parent_id=parent.id if parent else None,
                date_of_birth=datetime.strptime(date_of_birth, '%Y-%m-%d') if date_of_birth else None,
                gender=gender,
                address=address,
                is_active=True
            )
            
            student.set_face_images(saved_images)
            
            db.session.add(student)
            db.session.commit()
            
            log_activity('INFO', 'student', f'Student registered: {admission_number}', {
                'student_id': student.id,
                'name': full_name
            })
            
            flash(f'Student {full_name} registered successfully!', 'success')
            return redirect(url_for('student_profile', student_id=student.id))
            
        except Exception as e:
            logger.error(f"Error registering student: {str(e)}")
            db.session.rollback()
            flash(f'Error registering student: {str(e)}', 'danger')
    
    classes = Class.query.filter_by(is_active=True).all()
    return render_template('admin/register_student.html', classes=classes)


@app.route('/admin/student/edit/<int:student_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def edit_student(student_id):
    student = Student.query.get_or_404(student_id)
    
    if request.method == 'POST':
        try:
            if student.user:
                student.user.full_name = request.form.get('full_name', student.user.full_name)
                student.user.email = request.form.get('email', student.user.email)
                student.user.phone = request.form.get('phone', student.user.phone)
            
            student.roll_number = request.form.get('roll_number', student.roll_number)
            student.class_id = request.form.get('class_id') or None
            
            if request.form.get('date_of_birth'):
                student.date_of_birth = datetime.strptime(request.form.get('date_of_birth'), '%Y-%m-%d').date()
            
            student.gender = request.form.get('gender', student.gender)
            student.address = request.form.get('address', student.address)
            
            parent_name = request.form.get('parent_name')
            parent_email = request.form.get('parent_email')
            parent_phone = request.form.get('parent_phone')
            
            if parent_email:
                if student.parent:
                    if student.parent.user:
                        student.parent.user.full_name = parent_name or student.parent.user.full_name
                        student.parent.user.email = parent_email
                        student.parent.user.phone = parent_phone or student.parent.user.phone
                else:
                    existing_parent_user = User.query.filter_by(email=parent_email, role='parent').first()
                    
                    if existing_parent_user:
                        existing_parent = Parent.query.filter_by(user_id=existing_parent_user.id).first()
                        if existing_parent:
                            student.parent_id = existing_parent.id
                            existing_parent.alternate_phone = parent_phone or existing_parent.alternate_phone
                            existing_parent.address = student.address or existing_parent.address
                        else:
                            parent = Parent(
                                user_id=existing_parent_user.id,
                                alternate_phone=parent_phone,
                                address=student.address
                            )
                            db.session.add(parent)
                            db.session.flush()
                            student.parent_id = parent.id
                    else:
                        parent_user = User(
                            username=parent_email,
                            email=parent_email,
                            role='parent',
                            full_name=parent_name or '',
                            phone=parent_phone or ''
                        )
                        parent_user.set_password(parent_phone[-6:] if parent_phone else 'parent123')
                        db.session.add(parent_user)
                        db.session.flush()
                        
                        parent = Parent(
                            user_id=parent_user.id,
                            alternate_phone=parent_phone,
                            address=student.address
                        )
                        db.session.add(parent)
                        db.session.flush()
                        student.parent_id = parent.id
            
            face_image = request.form.get('face_image')
            if face_image:
                if ',' in face_image:
                    face_image = face_image.split(',')[1]
                
                import base64
                import cv2
                import numpy as np
                import face_recognition
                
                img_bytes = base64.b64decode(face_image)
                nparr = np.frombuffer(img_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                
                if frame is not None:
                    rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    face_locations = face_recognition.face_locations(rgb_frame, model='hog')
                    
                    if face_locations:
                        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
                        
                        if face_encodings:
                            existing_encodings = []
                            if student.face_encodings:
                                try:
                                    existing_encodings = json.loads(student.face_encodings)
                                    if not isinstance(existing_encodings, list):
                                        existing_encodings = []
                                except:
                                    existing_encodings = []
                            
                            new_encoding = face_encodings[0].tolist()
                            existing_encodings.append(new_encoding)
                            
                            if len(existing_encodings) > 10:
                                existing_encodings = existing_encodings[-10:]
                            
                            student.face_encodings = json.dumps(existing_encodings)
                            
                            top, right, bottom, left = face_locations[0]
                            face_crop = frame[top:bottom, left:right]
                            face_crop = cv2.resize(face_crop, (150, 150))
                            
                            timestamp = int(time.time())
                            filename = f"{student.admission_number}_{timestamp}.jpg"
                            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                            cv2.imwrite(filepath, face_crop)
                            
                            existing_images = [filename]
                            student.face_images = json.dumps(existing_images)
                            
                            flash('Face updated successfully!', 'success')
                        else:
                            flash('Could not extract face features. Please try again with better lighting.', 'danger')
                    else:
                        flash('No face detected in image. Please ensure your face is clearly visible.', 'danger')
                else:
                    flash('Could not process image.', 'danger')
            
            db.session.commit()
            flash('Student profile updated successfully!', 'success')
            return redirect(url_for('student_profile', student_id=student.id))
            
        except Exception as e:
            db.session.rollback()
            print(f"Error updating student: {str(e)}")
            import traceback
            traceback.print_exc()
            flash(f'Error updating student: {str(e)}', 'danger')
    
    classes = Class.query.filter_by(is_active=True).all()
    return render_template('admin/edit_student.html', student=student, classes=classes)


@app.route('/admin/student/delete/<int:student_id>', methods=['POST'])
@login_required
@admin_required
def delete_student(student_id):
    try:
        student = Student.query.get_or_404(student_id)
        
        student_name = student.user.full_name if student.user else student.admission_number
        
        if student.face_images:
            try:
                images = json.loads(student.face_images)
                for img_path in images:
                    full_path = os.path.join(app.config['UPLOAD_FOLDER'], img_path)
                    if os.path.exists(full_path):
                        os.remove(full_path)
            except Exception as e:
                print(f"Error deleting face images: {e}")
        
        Attendance.query.filter_by(student_id=student.id).delete()
        Notification.query.filter_by(student_id=student.id).delete()
        
        user = student.user
        if user:
            db.session.delete(user)
        
        db.session.delete(student)
        
        db.session.commit()
        
        log_activity('INFO', 'student', f'Student deleted: {student_name}', {'student_id': student_id})
        flash(f'Student {student_name} deleted successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting student: {str(e)}")
        flash(f'Error deleting student: {str(e)}', 'danger')
    
    return redirect(url_for('student_list'))


# ==================== Routes - Admin Teacher & Subject Management ====================

@app.route('/admin/manage-teachers')
@login_required
@admin_required
def manage_teachers():
    teachers = Teacher.query.all()
    return render_template('admin/manage_teachers.html', teachers=teachers)


@app.route('/admin/teacher/add', methods=['POST'])
@login_required
@admin_required
def admin_add_teacher():
    try:
        full_name = request.form.get('full_name')
        email = request.form.get('email')
        phone = request.form.get('phone')
        employee_id = request.form.get('employee_id')
        qualification = request.form.get('qualification')
        department = request.form.get('department')
        
        existing = Teacher.query.filter_by(employee_id=employee_id).first()
        if existing:
            flash(f'Teacher with Employee ID {employee_id} already exists!', 'danger')
            return redirect(url_for('manage_teachers'))
        
        existing_user = User.query.filter_by(email=email).first()
        if existing_user:
            flash(f'Email {email} already registered!', 'danger')
            return redirect(url_for('manage_teachers'))
        
        user = User(
            username=employee_id,
            email=email,
            role='teacher',
            full_name=full_name,
            phone=phone,
            is_active=True
        )
        user.set_password(employee_id)
        db.session.add(user)
        db.session.flush()
        
        teacher = Teacher(
            user_id=user.id,
            employee_id=employee_id,
            qualification=qualification,
            joining_date=date.today(),
            department=department
        )
        db.session.add(teacher)
        db.session.commit()
        
        flash(f'Teacher {full_name} added successfully! Default password: {employee_id}', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding teacher: {str(e)}', 'danger')
    
    return redirect(url_for('manage_teachers'))


@app.route('/admin/teacher/edit/<int:teacher_id>', methods=['POST'])
@login_required
@admin_required
def admin_edit_teacher(teacher_id):
    try:
        teacher = Teacher.query.get_or_404(teacher_id)
        
        teacher.user.full_name = request.form.get('full_name')
        teacher.user.email = request.form.get('email')
        teacher.user.phone = request.form.get('phone')
        teacher.qualification = request.form.get('qualification')
        teacher.department = request.form.get('department')
        teacher.user.is_active = request.form.get('is_active') == 'on'
        
        db.session.commit()
        flash('Teacher updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating teacher: {str(e)}', 'danger')
    
    return redirect(url_for('manage_teachers'))


@app.route('/admin/teacher/delete/<int:teacher_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_teacher(teacher_id):
    try:
        teacher = Teacher.query.get_or_404(teacher_id)
        user = teacher.user
        
        TeacherSubject.query.filter_by(teacher_id=teacher_id).delete()
        
        db.session.delete(teacher)
        
        if user:
            db.session.delete(user)
        
        db.session.commit()
        flash('Teacher deleted successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting teacher: {str(e)}', 'danger')
    
    return redirect(url_for('manage_teachers'))


@app.route('/admin/manage-subjects')
@login_required
@admin_required
def manage_subjects():
    subjects = Subject.query.all()
    return render_template('admin/manage_subjects.html', subjects=subjects)


@app.route('/admin/subject/add', methods=['POST'])
@login_required
@admin_required
def admin_add_subject():
    try:
        name = request.form.get('name')
        code = request.form.get('code')
        description = request.form.get('description')
        
        existing = Subject.query.filter_by(code=code).first()
        if existing:
            flash(f'Subject code {code} already exists!', 'danger')
            return redirect(url_for('manage_subjects'))
        
        subject = Subject(
            name=name,
            code=code,
            description=description
        )
        db.session.add(subject)
        db.session.commit()
        
        flash(f'Subject {name} added successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error adding subject: {str(e)}', 'danger')
    
    return redirect(url_for('manage_subjects'))


@app.route('/admin/subject/edit/<int:subject_id>', methods=['POST'])
@login_required
@admin_required
def admin_edit_subject(subject_id):
    try:
        subject = Subject.query.get_or_404(subject_id)
        subject.name = request.form.get('name')
        subject.code = request.form.get('code')
        subject.description = request.form.get('description')
        
        db.session.commit()
        flash('Subject updated successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error updating subject: {str(e)}', 'danger')
    
    return redirect(url_for('manage_subjects'))


@app.route('/admin/subject/delete/<int:subject_id>', methods=['POST'])
@login_required
@admin_required
def admin_delete_subject(subject_id):
    try:
        subject = Subject.query.get_or_404(subject_id)
        
        assignments = TeacherSubject.query.filter_by(subject_id=subject_id).count()
        if assignments > 0:
            flash(f'Cannot delete subject! It is assigned to {assignments} teacher(s).', 'danger')
            return redirect(url_for('manage_subjects'))
        
        db.session.delete(subject)
        db.session.commit()
        flash('Subject deleted successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting subject: {str(e)}', 'danger')
    
    return redirect(url_for('manage_subjects'))


@app.route('/admin/teacher-assignments')
@login_required
@admin_required
def admin_teacher_assignments():
    teachers = Teacher.query.all()
    subjects = Subject.query.all()
    classes = Class.query.filter_by(is_active=True).all()
    assignments = TeacherSubject.query.all()
    
    return render_template('admin/teacher_assignments.html',
                         teachers=teachers,
                         subjects=subjects,
                         classes=classes,
                         assignments=assignments)


@app.route('/admin/teacher-assign/add', methods=['POST'])
@login_required
@admin_required
def admin_add_teacher_assignment():
    try:
        teacher_id = request.form.get('teacher_id')
        subject_id = request.form.get('subject_id')
        class_id = request.form.get('class_id')
        
        existing = TeacherSubject.query.filter_by(
            teacher_id=teacher_id,
            subject_id=subject_id,
            class_id=class_id
        ).first()
        
        if existing:
            flash('This assignment already exists!', 'warning')
            return redirect(url_for('admin_teacher_assignments'))
        
        assignment = TeacherSubject(
            teacher_id=teacher_id,
            subject_id=subject_id,
            class_id=class_id
        )
        
        db.session.add(assignment)
        db.session.commit()
        
        teacher = Teacher.query.get(teacher_id)
        subject = Subject.query.get(subject_id)
        class_obj = Class.query.get(class_id)
        
        flash(f'Assigned {subject.name} to {teacher.user.full_name} for {class_obj.name}', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error creating assignment: {str(e)}', 'danger')
    
    return redirect(url_for('admin_teacher_assignments'))


@app.route('/admin/teacher-assign/remove/<int:assignment_id>', methods=['POST'])
@login_required
@admin_required
def admin_remove_teacher_assignment(assignment_id):
    try:
        assignment = TeacherSubject.query.get_or_404(assignment_id)
        
        active_sessions = ClassSession.query.filter_by(
            subject_id=assignment.subject_id,
            teacher_id=assignment.teacher_id,
            class_id=assignment.class_id,
            is_active=True
        ).count()
        
        if active_sessions > 0:
            flash(f'Cannot remove assignment! There are {active_sessions} active sessions using this assignment.', 'danger')
            return redirect(url_for('admin_teacher_assignments'))
        
        db.session.delete(assignment)
        db.session.commit()
        flash('Assignment removed successfully!', 'success')
        
    except Exception as e:
        db.session.rollback()
        flash(f'Error removing assignment: {str(e)}', 'danger')
    
    return redirect(url_for('admin_teacher_assignments'))


# ==================== Routes - Camera & Monitoring ====================

@app.route('/admin/monitoring')
@login_required
@admin_required
def live_monitoring():
    cameras = Camera.query.filter_by(is_active=True).all()
    return render_template('admin/monitoring.html', cameras=cameras)


@app.route('/admin/dual-camera')
@login_required
@admin_required
def dual_camera():
    active_session = AttendanceSession.query.filter_by(is_active=True).first()
    active_sessions = AttendanceSession.query.filter_by(is_active=True).all()
    cameras = Camera.query.filter_by(is_active=True).all()
    
    return render_template('admin/dual_camera.html', 
                         active_session=active_session,
                         active_sessions=active_sessions,
                         cameras=cameras)


@app.route('/admin/liveness-dual-camera')
@login_required
@admin_required
def liveness_dual_camera():
    active_session = AttendanceSession.query.filter_by(is_active=True).first()
    cameras = Camera.query.filter_by(is_active=True).all()
    
    return render_template('admin/liveness_dual_camera.html',
                         active_session=active_session,
                         cameras=cameras)


@app.route('/admin/simple-attendance')
@login_required
@admin_required
def simple_attendance():
    active_session = AttendanceSession.query.filter_by(is_active=True).first()
    active_sessions = AttendanceSession.query.filter_by(is_active=True).all()
    students = Student.query.filter_by(is_active=True).all()
    cameras = Camera.query.filter_by(is_active=True).all()
    today = date.today().strftime('%Y-%m-%d')
    
    return render_template('admin/simple_attendance.html',
                         active_session=active_session,
                         active_sessions=active_sessions,
                         students=students,
                         cameras=cameras,
                         today=today)


# ==================== API Routes ====================

@app.route('/api/cameras/list')
@login_required
def list_cameras():
    try:
        db_cameras = Camera.query.filter_by(is_active=True).all()
        manager_cameras = camera_manager.get_all_cameras()
        manager_dict = {cam['id']: cam for cam in manager_cameras}
        
        cameras = []
        for db_cam in db_cameras:
            cam_id = f"cam_{db_cam.id}"
            manager_info = manager_dict.get(cam_id, {})
            
            cameras.append({
                'id': cam_id,
                'db_id': db_cam.id,
                'name': db_cam.name,
                'ip': db_cam.ip_address,
                'status': manager_info.get('status', 'disconnected'),
                'fps': manager_info.get('fps', 0),
                'crossed_today': manager_info.get('crossed_today', 0),
                'last_seen': manager_info.get('last_seen'),
                'direction': db_cam.direction,
                'location': db_cam.location
            })
        
        return jsonify({'success': True, 'cameras': cameras})
    except Exception as e:
        logger.error(f"Error listing cameras: {e}")
        return jsonify({'success': False, 'error': str(e), 'cameras': []})


@app.route('/api/cameras/add', methods=['POST'])
@login_required
def add_camera():
    try:
        data = request.json
        
        camera_id = f"cam_{int(time.time())}"
        
        stream_path = data.get('path', '/stream1')
        
        if data.get('username') and data.get('password'):
            import urllib.parse
            encoded_password = urllib.parse.quote(data['password'], safe='')
            rtsp_url = f"rtsp://{data['username']}:{encoded_password}@{data['ip']}:{data['port']}{stream_path}"
        else:
            rtsp_url = f"rtsp://{data['ip']}:{data['port']}{stream_path}"
        
        camera = Camera(
            name=data['name'],
            ip_address=data['ip'],
            rtsp_url=rtsp_url,
            direction=data.get('direction', 'in'),
            location=data.get('location', ''),
            is_active=True
        )
        db.session.add(camera)
        db.session.commit()
        
        camera_manager_id = f"cam_{camera.id}"
        
        success = camera_manager.add_camera(
            camera_id=camera_manager_id,
            name=data['name'],
            ip_address=data['ip'],
            username=data.get('username'),
            password=data.get('password'),
            port=int(data['port']),
            stream_path=stream_path
        )
        
        if success:
            camera_manager.connect_camera(camera_manager_id)
            log_activity('INFO', 'camera', f'Camera added: {data["name"]}', {'camera_id': camera.id})
            
            return jsonify({
                'success': True, 
                'camera_id': camera_manager_id,
                'db_id': camera.id,
                'message': 'Camera added successfully'
            })
        else:
            db.session.delete(camera)
            db.session.commit()
            return jsonify({'success': False, 'error': 'Failed to add camera to manager'})
    
    except Exception as e:
        logger.error(f"Error adding camera: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/cameras/remove/<camera_id>', methods=['DELETE'])
@login_required
def remove_camera(camera_id):
    if camera_manager.remove_camera(camera_id):
        camera_info = camera_manager.get_camera_info(camera_id)
        if camera_info and 'ip' in camera_info:
            camera = Camera.query.filter_by(ip_address=camera_info['ip']).first()
            if camera:
                db.session.delete(camera)
                db.session.commit()
        return jsonify({'success': True})
    return jsonify({'success': False, 'error': 'Camera not found'})


@app.route('/api/camera/feed/<camera_id>')
@login_required
def camera_feed(camera_id):
    try:
        frame = camera_manager.get_frame(camera_id)
        
        if frame is None:
            import cv2
            import numpy as np
            blank = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(blank, "Waiting for camera...", (150, 240), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.7, (100, 100, 100), 2)
            ret, jpeg = cv2.imencode('.jpg', blank)
            if ret:
                return Response(jpeg.tobytes(), mimetype='image/jpeg')
        
        camera_info = camera_manager.get_camera_info(camera_id)
        if not camera_info:
            return jsonify({'error': 'Camera not found'}), 404
        
        line_y = camera_manager.line_positions.get(camera_id, 240)
        
        cv2.line(frame, (0, line_y), (frame.shape[1], line_y), (0, 255, 255), 3)
        cv2.putText(frame, "DETECTION LINE", (10, line_y - 10), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
        
        zone_top = max(0, line_y - 50)
        cv2.putText(frame, "NO DETECTION ZONE", (10, zone_top + 20), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)
        cv2.putText(frame, "DETECTION ACTIVE", (10, frame.shape[0] - 20), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
        
        overlay = frame.copy()
        cv2.rectangle(overlay, (0, 0), (frame.shape[1], line_y), (0, 0, 255), -1)
        cv2.addWeighted(overlay, 0.2, frame, 0.8, 0, frame)
        
        with camera_manager.lock:
            detections = camera_manager.detection_data.get(camera_id, [])
            fps = camera_manager.cameras.get(camera_id, {}).get('fps', 0)
            crossed = camera_manager.cameras.get(camera_id, {}).get('crossed_today', 0)
        
        for detection in detections:
            rect = detection['rectangle']
            student = detection['student']
            center_y = detection.get('center_y', 0)
            
            if center_y > line_y:
                color = (0, 255, 0)
                status = "ACTIVE"
            else:
                color = (255, 255, 0)
                status = "APPROACHING"
            
            cv2.rectangle(frame, 
                         (rect['left'], rect['top']), 
                         (rect['left'] + rect['width'], rect['top'] + rect['height']), 
                         color, 2)
            
            label = f"{student['name']} ({status})"
            (text_w, text_h), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)
            cv2.rectangle(frame, 
                         (rect['left'] + 5, rect['top'] - text_h - 8),
                         (rect['left'] + text_w + 12, rect['top'] - 3),
                         color, -1)
            cv2.putText(frame, label, 
                       (rect['left'] + 8, rect['top'] - 8), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        
        cv2.putText(frame, f"TIME: {datetime.now().strftime('%H:%M:%S')}", 
                   (10, frame.shape[0] - 45), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(frame, f"DETECTIONS: {len(detections)} | CROSSED: {crossed} | FPS: {fps}", 
                   (10, frame.shape[0] - 25), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        cv2.putText(frame, f"LINE: {int((line_y / frame.shape[0]) * 100)}%", 
                   (10, frame.shape[0] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 0), 1)
        
        if camera_manager.is_within_session():
            cv2.putText(frame, "SESSION ACTIVE", (frame.shape[1] - 150, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        else:
            cv2.putText(frame, "SESSION INACTIVE", (frame.shape[1] - 160, 30), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        
        encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), 80]
        ret, jpeg = cv2.imencode('.jpg', frame, encode_param)
        if ret:
            return Response(jpeg.tobytes(), mimetype='image/jpeg')
            
    except Exception as e:
        logger.error(f"Error getting camera feed: {e}")
        import cv2
        import numpy as np
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(blank, "Camera Error", (200, 240), 
                   cv2.FONT_HERSHEY_SIMPLEX, 0.7, (100, 100, 100), 2)
        ret, jpeg = cv2.imencode('.jpg', blank)
        if ret:
            return Response(jpeg.tobytes(), mimetype='image/jpeg')


@app.route('/api/camera/line-position', methods=['POST'])
@login_required
def set_line_position():
    data = request.json
    camera_id = data.get('camera_id')
    position = data.get('position')
    
    if camera_id and position:
        camera_manager.set_line_position(camera_id, position)
        return jsonify({'success': True})
    
    return jsonify({'success': False, 'error': 'Invalid data'}), 400


@app.route('/api/camera/snapshot/<camera_id>')
@login_required
def camera_snapshot(camera_id):
    snapshot = camera_manager.get_snapshot(camera_id)
    if snapshot:
        return Response(snapshot, mimetype='image/jpeg')
    return jsonify({'success': False, 'error': 'Could not take snapshot'}), 404


@app.route('/api/attendance/stats')
def attendance_stats():
    today = date.today()
    
    total_students = Student.query.filter_by(is_active=True).count()
    
    checkins_today = Attendance.query.filter(
        func.date(Attendance.timestamp) == today,
        Attendance.status == 'in'
    ).count()
    
    checkouts_today = Attendance.query.filter(
        func.date(Attendance.timestamp) == today,
        Attendance.status == 'out'
    ).count()
    
    active_now = Attendance.query.filter(
        func.date(Attendance.timestamp) == today,
        Attendance.status == 'in'
    ).distinct(Attendance.student_id).count()
    
    return jsonify({
        'total_students': total_students,
        'checkins_today': checkins_today,
        'checkouts_today': checkouts_today,
        'active_now': active_now,
        'attendance_rate': round((checkins_today / total_students * 100), 1) if total_students > 0 else 0
    })


@app.route('/api/attendance/recent')
def recent_attendance():
    limit = request.args.get('limit', 20, type=int)
    
    records = Attendance.query.order_by(
        Attendance.timestamp.desc()
    ).limit(limit).all()
    
    return jsonify([{
        'id': r.id,
        'student_id': r.student_id,
        'student_name': r.student.user.full_name if r.student and r.student.user else None,
        'status': r.status,
        'timestamp': r.timestamp.isoformat(),
        'confidence': r.confidence,
        'camera_name': r.camera_id
    } for r in records])


@app.route('/api/students/list')
@login_required
def list_students_api():
    students = Student.query.filter_by(is_active=True).all()
    return jsonify({
        'students': [{
            'id': s.id,
            'name': s.user.full_name if s.user else 'Unknown',
            'admission_number': s.admission_number
        } for s in students]
    })


@app.route('/api/simple/register', methods=['POST'])
@login_required
def simple_register_face():
    try:
        data = request.json
        student_id = data.get('student_id')
        image_data = data.get('image')
        
        if not student_id or not image_data:
            return jsonify({'success': False, 'error': 'Missing data'})
        
        student = Student.query.get(student_id)
        if not student:
            return jsonify({'success': False, 'error': 'Student not found'})
        
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        
        import base64
        import cv2
        import numpy as np
        import face_recognition
        
        img_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            return jsonify({'success': False, 'error': 'Could not decode image'})
        
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        face_locations = face_recognition.face_locations(rgb_frame, model='hog')
        
        if not face_locations:
            return jsonify({'success': False, 'error': 'No face detected in image. Please ensure your face is clearly visible and well-lit.'})
        
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
        
        if not face_encodings:
            return jsonify({'success': False, 'error': 'Could not extract face features. Please try again with better lighting.'})
        
        existing_encodings = []
        if student.face_encodings:
            try:
                existing_encodings = json.loads(student.face_encodings)
                if not isinstance(existing_encodings, list):
                    existing_encodings = []
            except:
                existing_encodings = []
        
        new_encoding = face_encodings[0].tolist()
        existing_encodings.append(new_encoding)
        
        if len(existing_encodings) > 10:
            existing_encodings = existing_encodings[-10:]
        
        student.face_encodings = json.dumps(existing_encodings)
        
        if face_locations:
            top, right, bottom, left = face_locations[0]
            face_crop = frame[top:bottom, left:right]
            face_crop = cv2.resize(face_crop, (150, 150))
            
            filename = f"{student.admission_number}_{int(time.time())}.jpg"
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            cv2.imwrite(filepath, face_crop)
            
            existing_images = []
            if student.face_images:
                try:
                    existing_images = json.loads(student.face_images)
                    if not isinstance(existing_images, list):
                        existing_images = []
                except:
                    existing_images = []
            
            existing_images.append(filename)
            if len(existing_images) > 5:
                existing_images = existing_images[-5:]
            
            student.face_images = json.dumps(existing_images)
        
        db.session.commit()
        
        return jsonify({
            'success': True, 
            'message': f'Face registered for {student.user.full_name}',
            'encodings_count': len(existing_encodings)
        })
        
    except Exception as e:
        logger.error(f"Error registering face: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/simple/get_faces', methods=['GET'])
@login_required
def simple_get_faces():
    try:
        students = Student.query.filter_by(is_active=True).all()
        
        faces = []
        for student in students:
            has_face = bool(student.face_encodings and student.face_encodings != '[]')
            faces.append({
                'id': student.id,
                'name': student.user.full_name if student.user else f"Student_{student.id}",
                'admission_number': student.admission_number,
                'has_face': has_face
            })
        
        return jsonify(faces)
        
    except Exception as e:
        logger.error(f"Error getting faces: {str(e)}")
        return jsonify([])


@app.route('/api/simple/delete_face/<int:student_id>', methods=['DELETE'])
@login_required
def simple_delete_face(student_id):
    try:
        student = Student.query.get(student_id)
        if student:
            student.face_encodings = None
            student.face_images = None
            db.session.commit()
            return jsonify({'success': True, 'message': f'Face data deleted for {student.user.full_name}'})
        return jsonify({'success': False, 'error': 'Student not found'})
    except Exception as e:
        logger.error(f"Error deleting face: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/simple/clear_database', methods=['POST'])
@login_required
def simple_clear_database():
    try:
        students = Student.query.filter_by(is_active=True).all()
        for student in students:
            student.face_encodings = None
            student.face_images = None
        db.session.commit()
        return jsonify({'success': True, 'message': 'All face data cleared'})
    except Exception as e:
        logger.error(f"Error clearing database: {str(e)}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/simple/mark-attendance', methods=['POST'])
@login_required
def simple_mark_attendance():
    try:
        data = request.json
        student_id = data.get('student_id')
        camera_type = data.get('camera_type', 'in')
        confidence = data.get('confidence', 0)
        session_id = data.get('session_id')
        
        if not student_id:
            return jsonify({'success': False, 'error': 'Student ID required'})
        
        student = Student.query.get(student_id)
        if not student:
            return jsonify({'success': False, 'error': 'Student not found'})
        
        if session_id:
            session = AttendanceSession.query.get(session_id)
            if not session:
                return jsonify({'success': False, 'error': 'Session not found'})
            if not session.is_active:
                return jsonify({'success': False, 'error': 'Session is not active'})
            
            now = datetime.now()
            if not session.is_within_session(now):
                return jsonify({'success': False, 'error': 'Outside session time'})
        
        status = 'in' if camera_type == 'in' else 'out'
        
        today = date.today()
        existing = Attendance.query.filter(
            Attendance.student_id == student_id,
            func.date(Attendance.timestamp) == today,
            Attendance.status == status
        ).first()
        
        if existing:
            return jsonify({'success': False, 'error': f'Already marked {status} today', 'already_marked': True})
        
        attendance = Attendance(
            student_id=student_id,
            status=status,
            confidence=confidence,
            camera_id=f'simple_attendance_{camera_type}',
            verified=True,
            timestamp=datetime.now()
        )
        db.session.add(attendance)
        db.session.commit()
        
        socketio.emit('attendance_update', {
            'student_id': student_id,
            'student_name': student.user.full_name,
            'status': status,
            'timestamp': datetime.now().isoformat(),
            'confidence': confidence,
            'camera_id': camera_type
        })
        
        return jsonify({
            'success': True,
            'message': f'{student.user.full_name} marked {status}',
            'attendance_id': attendance.id
        })
        
    except Exception as e:
        logger.error(f"Error marking attendance: {str(e)}")
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/simple/get_report', methods=['GET'])
@login_required
def simple_get_report():
    try:
        session_id = request.args.get('session_id', type=int)
        date_filter = request.args.get('date')
        
        if date_filter:
            filter_date = datetime.strptime(date_filter, '%Y-%m-%d').date()
        else:
            filter_date = date.today()
        
        records = Attendance.query.filter(
            func.date(Attendance.timestamp) == filter_date
        ).order_by(Attendance.timestamp.desc()).all()
        
        attendance_dict = {}
        
        for record in records:
            student = record.student
            if student and student.user:
                student_key = record.student_id
                
                if student_key not in attendance_dict:
                    attendance_dict[student_key] = {
                        'student': student,
                        'check_in': None,
                        'check_out': None,
                        'check_in_time': None,
                        'check_out_time': None,
                        'check_in_confidence': None
                    }
                
                if record.status == 'in':
                    attendance_dict[student_key]['check_in'] = record
                    attendance_dict[student_key]['check_in_time'] = record.timestamp
                    attendance_dict[student_key]['check_in_confidence'] = record.confidence
                elif record.status == 'out':
                    attendance_dict[student_key]['check_out'] = record
                    attendance_dict[student_key]['check_out_time'] = record.timestamp
        
        report_entries = []
        
        for student_key, data in attendance_dict.items():
            student = data['student']
            check_in_time = data['check_in_time']
            check_out_time = data['check_out_time']
            
            entry_time = check_in_time.strftime('%H:%M:%S') if check_in_time else '-'
            exit_time = check_out_time.strftime('%H:%M:%S') if check_out_time else '-'
            
            duration = '-'
            if check_in_time and check_out_time:
                delta = check_out_time - check_in_time
                hours = delta.seconds // 3600
                minutes = (delta.seconds % 3600) // 60
                duration = f"{hours}h {minutes}m"
            elif check_in_time and not check_out_time:
                duration = 'Still in class'
            
            report_entries.append({
                'Name': student.user.full_name,
                'Admission': student.admission_number,
                'Date': filter_date.strftime('%Y-%m-%d'),
                'Entry Time': entry_time,
                'Exit Time': exit_time,
                'Duration': duration,
                'Status': 'PRESENT'
            })
        
        all_students = Student.query.filter_by(is_active=True).all()
        marked_student_ids = set(attendance_dict.keys())
        
        for student in all_students:
            if student.id not in marked_student_ids:
                report_entries.append({
                    'Name': student.user.full_name if student.user else 'Unknown',
                    'Admission': student.admission_number,
                    'Date': filter_date.strftime('%Y-%m-%d'),
                    'Entry Time': 'ABSENT',
                    'Exit Time': '-',
                    'Duration': '-',
                    'Status': 'ABSENT'
                })
        
        report_entries.sort(key=lambda x: 0 if x['Entry Time'] != 'ABSENT' else 1)
        
        total_students = len(all_students)
        present_today = len([r for r in report_entries if r['Entry Time'] != 'ABSENT'])
        absent_today = total_students - present_today
        
        return jsonify({
            'success': True,
            'sessions': report_entries,
            'total_students': total_students,
            'present_today': present_today,
            'absent_today': absent_today,
            'date': filter_date.strftime('%Y-%m-%d')
        })
        
    except Exception as e:
        logger.error(f"Error getting report: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/simple/detect', methods=['POST'])
@login_required
def simple_detect_faces():
    try:
        data = request.json
        image_data = data.get('image')
        session_id = data.get('session_id')
        camera_type = data.get('camera_type', 'in')
        line_position = data.get('line_position', 0.5)
        
        if not image_data:
            return jsonify({'faces': [], 'count': 0})
        
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        
        import base64
        import cv2
        import numpy as np
        import face_recognition
        
        img_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            return jsonify({'faces': [], 'count': 0})
        
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        
        face_locations = face_recognition.face_locations(rgb_frame, model='hog')
        
        if not face_locations:
            return jsonify({'faces': [], 'count': 0})
        
        face_encodings = face_recognition.face_encodings(rgb_frame, face_locations)
        
        students = Student.query.filter_by(is_active=True).all()
        known_face_encodings = []
        known_face_students = []
        known_face_names = []
        
        for student in students:
            if student.face_encodings:
                try:
                    encodings = json.loads(student.face_encodings)
                    if isinstance(encodings, list):
                        for enc in encodings:
                            if isinstance(enc, list) and len(enc) == 128:
                                known_face_encodings.append(np.array(enc, dtype=np.float64))
                                known_face_students.append(student)
                                known_face_names.append(student.user.full_name if student.user else f"Student_{student.id}")
                except Exception as e:
                    print(f"Error loading encoding for {student.id}: {e}")
        
        detected_faces = []
        marked_count = 0
        
        session_active = False
        within_time = False
        session = None
        
        if session_id:
            session = AttendanceSession.query.get(session_id)
            if session:
                session_active = session.is_active
                now = datetime.now()
                within_time = session.is_within_session(now)
        
        frame_height, frame_width = frame.shape[:2]
        line_y = int(frame_height * line_position)
        
        for i, face_encoding in enumerate(face_encodings):
            name = "Unknown"
            student_id = None
            confidence = 0
            attendance_marked = False
            best_distance = 1.0
            
            top, right, bottom, left = face_locations[i]
            
            if known_face_encodings:
                distances = face_recognition.face_distance(known_face_encodings, face_encoding)
                
                if len(distances) > 0:
                    best_match_index = np.argmin(distances)
                    best_distance = distances[best_match_index]
                    
                    if best_distance < 0.5:
                        student = known_face_students[best_match_index]
                        name = known_face_names[best_match_index]
                        student_id = student.id
                        confidence = (1 - best_distance) * 100
                        
                        if session_active and within_time and confidence > 50:
                            today = date.today()
                            
                            existing = Attendance.query.filter(
                                Attendance.student_id == student_id,
                                func.date(Attendance.timestamp) == today,
                                Attendance.status == camera_type
                            ).first()
                            
                            if not existing:
                                attendance = Attendance(
                                    student_id=student_id,
                                    status=camera_type,
                                    confidence=confidence,
                                    camera_id=f'simple_attendance_{camera_type}',
                                    verified=True,
                                    timestamp=datetime.now()
                                )
                                db.session.add(attendance)
                                db.session.commit()
                                attendance_marked = True
                                marked_count += 1
                                
                                socketio.emit('attendance_update', {
                                    'student_id': student_id,
                                    'student_name': name,
                                    'status': camera_type,
                                    'timestamp': datetime.now().isoformat(),
                                    'confidence': confidence,
                                    'camera_id': camera_type
                                })
            
            detected_faces.append({
                'name': name,
                'student_id': student_id,
                'confidence': round(confidence, 1),
                'rectangle': {'left': left, 'top': top, 'width': right - left, 'height': bottom - top},
                'attendance_marked': attendance_marked,
                'match_distance': round(best_distance, 4)
            })
        
        return jsonify({
            'faces': detected_faces, 
            'count': len(detected_faces), 
            'marked_count': marked_count,
            'total_known_faces': len(known_face_encodings),
            'session_active': session_active,
            'within_time': within_time
        })
        
    except Exception as e:
        logger.error(f"Detection error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'faces': [], 'count': 0, 'error': str(e)})


@app.route('/api/test/attendance', methods=['POST'])
@login_required
def test_attendance():
    try:
        data = request.json
        
        timestamp_str = data['timestamp']
        
        if timestamp_str.endswith('Z'):
            timestamp_str = timestamp_str.replace('Z', '+00:00')
        
        try:
            timestamp = datetime.fromisoformat(timestamp_str)
        except ValueError:
            timestamp = datetime.utcnow()
        
        attendance = Attendance(
            student_id=data['student_id'],
            status=data['status'],
            confidence=float(data['confidence']),
            timestamp=timestamp,
            verified=True,
            camera_id=data.get('camera_id', 'test_camera')
        )
        
        db.session.add(attendance)
        db.session.commit()
        
        student = Student.query.get(data['student_id'])
        student_name = student.user.full_name if student and student.user else 'Unknown'
        
        socketio.emit('attendance_update', {
            'student_id': data['student_id'],
            'student_name': student_name,
            'status': data['status'],
            'timestamp': timestamp.isoformat(),
            'confidence': data['confidence'],
            'test_mode': True
        })
        
        return jsonify({
            'success': True,
            'student_name': student_name,
            'message': f'Test attendance recorded for {student_name}'
        })
        
    except Exception as e:
        logger.error(f"Error in test attendance: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/detect-faces', methods=['POST'])
@login_required
def detect_faces():
    try:
        data = request.json
        image_data = data.get('image')
        camera_id = data.get('camera_id')
        line_position = data.get('line_position', 240)
        
        if not image_data:
            return jsonify({'success': False, 'error': 'No image data'})
        
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        
        import base64
        import cv2
        import numpy as np
        import face_recognition
        
        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        if frame is None:
            return jsonify({'success': False, 'error': 'Invalid image data'})
        
        small_frame = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
        
        face_locations = face_recognition.face_locations(rgb_small_frame)
        
        face_encodings = []
        if face_locations:
            face_encodings = face_recognition.face_encodings(rgb_small_frame, face_locations)
        
        faces = []
        
        try:
            students = Student.query.filter_by(is_active=True).all()
        except Exception as e:
            logger.error(f"Error loading students: {e}")
            students = []
        
        known_face_encodings = []
        known_face_ids = []
        known_face_names = []
        
        for student in students:
            if student.face_encodings:
                try:
                    encodings_data = json.loads(student.face_encodings)
                    
                    if isinstance(encodings_data, list):
                        for enc in encodings_data:
                            if isinstance(enc, list):
                                encoding_array = np.array(enc, dtype=np.float64)
                            else:
                                encoding_array = np.array(encodings_data, dtype=np.float64)
                            
                            if encoding_array.shape == (128,):
                                known_face_encodings.append(encoding_array)
                                known_face_ids.append(student.id)
                                name = student.user.full_name if student.user else f"Student_{student.id}"
                                known_face_names.append(name)
                except Exception as e:
                    logger.error(f"Error parsing face encoding for student {student.id}: {e}")
                    continue
        
        for i, (top, right, bottom, left) in enumerate(face_locations):
            top *= 2
            right *= 2
            bottom *= 2
            left *= 2
            
            face_center_y = (top + bottom) // 2
            face_center_x = (left + right) // 2
            
            face_data = {
                'rectangle': {
                    'left': left,
                    'top': top,
                    'width': right - left,
                    'height': bottom - top
                },
                'center_y': face_center_y,
                'center_x': face_center_x
            }
            
            if i < len(face_encodings) and known_face_encodings:
                try:
                    current_encoding = face_encodings[i]
                    
                    face_distances = face_recognition.face_distance(known_face_encodings, current_encoding)
                    
                    if len(face_distances) > 0:
                        best_match_index = np.argmin(face_distances)
                        best_distance = face_distances[best_match_index]
                        
                        confidence = 1 - min(best_distance, 1.0)
                        
                        if best_distance < 0.5:
                            student_id = known_face_ids[best_match_index]
                            student_name = known_face_names[best_match_index]
                            
                            face_data['student'] = {
                                'id': student_id,
                                'name': student_name
                            }
                            face_data['confidence'] = float(confidence * 100)
                except Exception as e:
                    logger.error(f"Error matching face: {e}")
            
            faces.append(face_data)
        
        return jsonify({
            'success': True,
            'faces': faces,
            'count': len(faces)
        })
        
    except ImportError as e:
        logger.error(f"Import error in detect_faces: {str(e)}")
        return jsonify({
            'success': False,
            'error': f"Import error: {str(e)}",
            'faces': [],
            'count': 0
        }), 500
    except Exception as e:
        logger.error(f"Error detecting faces: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False, 
            'error': str(e),
            'faces': [],
            'count': 0
        }), 500


@app.route('/api/analytics/daily-trend')
def daily_trend():
    days = request.args.get('days', 30, type=int)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    results = db.session.query(
        func.date(Attendance.timestamp).label('date'),
        func.count(func.distinct(Attendance.student_id)).label('count')
    ).filter(
        Attendance.timestamp >= start_date,
        Attendance.timestamp <= end_date,
        Attendance.status == 'in'
    ).group_by(func.date(Attendance.timestamp)).order_by('date').all()
    
    date_dict = {str(r[0]): r[1] for r in results}
    
    dates = []
    counts = []
    current_date = start_date.date()
    
    while current_date <= end_date.date():
        date_str = current_date.strftime('%Y-%m-%d')
        dates.append(date_str)
        counts.append(date_dict.get(date_str, 0))
        current_date += timedelta(days=1)
    
    avg = sum(counts) / len(counts) if counts else 0
    peak = max(counts) if counts else 0
    
    return jsonify({
        'dates': dates,
        'counts': counts,
        'average': round(avg, 1),
        'peak': peak
    })


@app.route('/api/analytics/class-attendance')
def class_attendance_analytics():
    classes = Class.query.filter_by(is_active=True).all()
    today = date.today()
    
    class_names = []
    percentages = []
    
    for class_ in classes:
        total = Student.query.filter_by(class_id=class_.id, is_active=True).count()
        if total > 0:
            present = Attendance.query.filter(
                Attendance.student_id.in_(
                    db.session.query(Student.id).filter_by(class_id=class_.id)
                ),
                func.date(Attendance.timestamp) == today,
                Attendance.status == 'in'
            ).distinct(Attendance.student_id).count()
            
            class_names.append(class_.name)
            percentages.append(round((present / total) * 100, 1))
    
    return jsonify({
        'classes': class_names,
        'percentages': percentages
    })


@app.route('/api/analytics/peak-hours')
def peak_hours_analytics():
    days = request.args.get('days', 30, type=int)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    results = db.session.query(
        extract('hour', Attendance.timestamp).label('hour'),
        func.count(Attendance.id).label('count')
    ).filter(
        Attendance.timestamp >= start_date,
        Attendance.status == 'in'
    ).group_by(
        extract('hour', Attendance.timestamp)
    ).order_by('hour').all()
    
    hours = []
    counts = []
    hour_dict = {int(r[0]): r[1] for r in results}
    
    for hour in range(0, 24):
        hours.append(hour)
        counts.append(hour_dict.get(hour, 0))
    
    return jsonify({
        'hours': hours,
        'counts': counts
    })


@app.route('/api/analytics/student-attendance')
def student_attendance_analytics():
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    class_id = request.args.get('class_id', type=int)
    
    if start_date_str and end_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
    else:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
    
    students = Student.query.filter_by(is_active=True)
    if class_id:
        students = students.filter_by(class_id=class_id)
    
    result = []
    for student in students.all():
        present_days = Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.status == 'in',
            func.date(Attendance.timestamp) >= start_date,
            func.date(Attendance.timestamp) <= end_date
        ).distinct(func.date(Attendance.timestamp)).count()
        
        total_days = (end_date - start_date).days + 1
        absent_days = total_days - present_days
        
        percentage = (present_days / total_days * 100) if total_days > 0 else 0
        
        result.append({
            'id': student.id,
            'name': student.user.full_name if student.user else 'Unknown',
            'admission_number': student.admission_number,
            'class_name': student.current_class.name if student.current_class else 'Not Assigned',
            'present_days': present_days,
            'absent_days': absent_days,
            'attendance_percentage': round(percentage, 1)
        })
    
    return jsonify(result)


@app.route('/api/analytics/low-attendance')
def low_attendance_analytics():
    threshold = request.args.get('threshold', 75, type=int)
    class_id = request.args.get('class_id', type=int)
    
    thirty_days_ago = datetime.now() - timedelta(days=30)
    
    query = Student.query.filter_by(is_active=True)
    if class_id:
        query = query.filter_by(class_id=class_id)
    
    students = query.all()
    
    low_attendance = []
    for student in students:
        present_days = Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.timestamp >= thirty_days_ago,
            Attendance.status == 'in'
        ).distinct(func.date(Attendance.timestamp)).count()
        
        absent_days = 30 - present_days
        percentage = (present_days / 30) * 100 if present_days > 0 else 0
        
        if percentage < threshold:
            low_attendance.append({
                'id': student.id,
                'name': student.user.full_name if student.user else 'Unknown',
                'admission_number': student.admission_number,
                'class_name': student.current_class.name if student.current_class else 'N/A',
                'percentage': round(percentage, 1),
                'present_days': present_days,
                'absent_days': absent_days,
                'total_days': 30
            })
    
    low_attendance.sort(key=lambda x: x['percentage'])
    
    return jsonify({
        'success': True,
        'students': low_attendance,
        'total_students': len(students),
        'low_attendance_count': len(low_attendance)
    })


@app.route('/api/analytics/student-detail')
def student_detail_analytics():
    student_id = request.args.get('student_id', type=int)
    start_date_str = request.args.get('start')
    end_date_str = request.args.get('end')
    
    if not student_id:
        return jsonify({'error': 'Student ID required'}), 400
    
    if start_date_str and end_date_str:
        start_date = datetime.strptime(start_date_str, '%Y-%m-%d')
        end_date = datetime.strptime(end_date_str, '%Y-%m-%d')
    else:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=30)
    
    student = Student.query.get(student_id)
    if not student:
        return jsonify({'error': 'Student not found'}), 404
    
    present_days = Attendance.query.filter(
        Attendance.student_id == student_id,
        Attendance.status == 'in',
        func.date(Attendance.timestamp) >= start_date,
        func.date(Attendance.timestamp) <= end_date
    ).distinct(func.date(Attendance.timestamp)).count()
    
    total_days = (end_date - start_date).days + 1
    absent_days = total_days - present_days
    percentage = (present_days / total_days * 100) if total_days > 0 else 0
    
    records = Attendance.query.filter(
        Attendance.student_id == student_id,
        func.date(Attendance.timestamp) >= start_date,
        func.date(Attendance.timestamp) <= end_date
    ).order_by(Attendance.timestamp.desc()).limit(10).all()
    
    recent_records = []
    for record in records:
        recent_records.append({
            'date': record.timestamp.strftime('%Y-%m-%d'),
            'check_in': record.timestamp.strftime('%H:%M:%S') if record.status == 'in' else None,
            'check_out': record.timestamp.strftime('%H:%M:%S') if record.status == 'out' else None,
            'confidence': round(record.confidence, 1) if record.confidence else None
        })
    
    return jsonify({
        'student_id': student_id,
        'student_name': student.user.full_name if student.user else 'Unknown',
        'total_days': total_days,
        'present_days': present_days,
        'absent_days': absent_days,
        'attendance_percentage': round(percentage, 1),
        'recent_records': recent_records
    })


@app.route('/api/announcements')
@login_required
def get_announcements():
    query = Announcement.query.filter(
        or_(
            Announcement.target_role == 'all',
            Announcement.target_role == current_user.role
        )
    )
    
    if current_user.role == 'student' and current_user.student and current_user.student.current_class:
        query = query.filter(
            or_(
                Announcement.target_class_id == None,
                Announcement.target_class_id == current_user.student.current_class.id
            )
        )
    
    announcements = query.order_by(Announcement.is_pinned.desc(), 
                                  Announcement.created_at.desc()).limit(20).all()
    
    return jsonify({
        'success': True,
        'announcements': [a.to_dict() for a in announcements]
    })


@app.route('/api/announcements/create', methods=['POST'])
@login_required
def create_announcement():
    if current_user.role not in ['admin', 'teacher']:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    data = request.json
    
    announcement = Announcement(
        title=data['title'],
        content=data['content'],
        target_role=data.get('target', 'all'),
        target_class_id=data.get('class_id') if data.get('target') == 'class' else None,
        created_by=current_user.id,
        expires_at=datetime.strptime(data['expires'], '%Y-%m-%d') if data.get('expires') else None,
        is_pinned=data.get('is_pinned', False)
    )
    
    db.session.add(announcement)
    db.session.commit()
    
    socketio.emit('new_announcement', announcement.to_dict())
    
    return jsonify({'success': True, 'id': announcement.id})


@app.route('/api/student/<int:student_id>/face-image')
@login_required
def get_student_face_image(student_id):
    try:
        student = Student.query.get(student_id)
        if student and student.face_images:
            images = json.loads(student.face_images)
            if images and len(images) > 0:
                image_path = os.path.join(app.config['UPLOAD_FOLDER'], images[0])
                if os.path.exists(image_path):
                    return send_file(image_path, mimetype='image/jpeg')
        return jsonify({'error': 'No face image'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 404


@app.route('/api/student/<int:student_id>/attendance')
def student_attendance_api(student_id):
    days = request.args.get('days', 30, type=int)
    
    start_date = datetime.now() - timedelta(days=days)
    
    records = Attendance.query.filter(
        Attendance.student_id == student_id,
        Attendance.timestamp >= start_date
    ).order_by(Attendance.timestamp).all()
    
    attendance_by_date = {}
    for record in records:
        date_str = record.timestamp.date().isoformat()
        if date_str not in attendance_by_date:
            attendance_by_date[date_str] = []
        attendance_by_date[date_str].append({
            'status': record.status,
            'time': record.timestamp.strftime('%H:%M:%S')
        })
    
    return jsonify(attendance_by_date)


@app.route('/api/teacher/session/<int:session_id>/notify-absent', methods=['POST'])
@login_required
def notify_absent_students(session_id):
    if current_user.role != 'teacher':
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    teacher = Teacher.query.filter_by(user_id=current_user.id).first()
    session = ClassSession.query.get_or_404(session_id)
    
    if session.teacher_id != teacher.id:
        return jsonify({'success': False, 'error': 'Unauthorized'}), 403
    
    absent_records = SessionAttendance.query.filter_by(
        session_id=session_id,
        status='absent'
    ).all()
    
    sent_count = 0
    for record in absent_records:
        student = record.student
        if student.parent and student.parent.user and student.parent.user.phone:
            message = f"Dear Parent, {student.user.full_name} was absent for {session.subject.name} class on {record.marked_at.strftime('%d/%m/%Y')}. Please ensure regular attendance."
            notification_service.send_sms(student.parent.user.phone, message)
            sent_count += 1
    
    return jsonify({'success': True, 'sent_count': sent_count})


@app.route('/api/export/session/<int:session_id>')
@login_required
def export_session_attendance(session_id):
    session = ClassSession.query.get_or_404(session_id)
    
    attendance_records = SessionAttendance.query.filter_by(session_id=session_id).all()
    
    data = []
    for record in attendance_records:
        data.append({
            'Student Name': record.student.user.full_name if record.student.user else 'Unknown',
            'Admission Number': record.student.admission_number,
            'Status': record.status.upper(),
            'Check In Time': record.check_in_time.strftime('%H:%M:%S') if record.check_in_time else '-',
            'Check Out Time': record.check_out_time.strftime('%H:%M:%S') if record.check_out_time else '-',
            'Duration (mins)': record.duration_minutes or '-',
            'Confidence': f"{record.confidence}%" if record.confidence else '-'
        })
    
    df = pd.DataFrame(data)
    
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name=f'Session_{session_id}')
    
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=f'attendance_{session.subject.name}_{session.start_time.strftime("%H%M")}.xlsx'
    )


@app.route('/api/export/attendance', methods=['GET', 'POST'])
@login_required
def export_attendance():
    if request.method == 'POST':
        data = request.json
    else:
        data = request.args
    
    start_date = datetime.strptime(data.get('start_date'), '%Y-%m-%d')
    end_date = datetime.strptime(data.get('end_date'), '%Y-%m-%d') + timedelta(days=1)
    format_type = data.get('format', 'csv')
    
    records = Attendance.query.filter(
        Attendance.timestamp >= start_date,
        Attendance.timestamp <= end_date
    ).order_by(Attendance.timestamp).all()
    
    df = pd.DataFrame([{
        'Date': r.timestamp.strftime('%Y-%m-%d'),
        'Time': r.timestamp.strftime('%H:%M:%S'),
        'Student ID': r.student.admission_number if r.student else None,
        'Student Name': r.student.user.full_name if r.student and r.student.user else None,
        'Status': r.status.upper(),
        'Confidence': f"{r.confidence:.1f}%" if r.confidence else 'N/A',
        'Verified': 'Yes' if r.verified else 'No'
    } for r in records])
    
    if format_type == 'csv':
        output = df.to_csv(index=False)
        return Response(
            output,
            mimetype='text/csv',
            headers={'Content-Disposition': f'attachment; filename=attendance_{start_date.strftime("%Y%m%d")}_to_{end_date.strftime("%Y%m%d")}.csv'}
        )
    elif format_type == 'excel':
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Attendance')
        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=f'attendance_{start_date.strftime("%Y%m%d")}_to_{end_date.strftime("%Y%m%d")}.xlsx'
        )
    
    return jsonify({'success': False, 'error': 'Invalid format'}), 400


@app.route('/api/export/chart-data')
@login_required
def export_chart_data():
    days = request.args.get('days', 30, type=int)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    records = Attendance.query.filter(
        Attendance.timestamp >= start_date,
        Attendance.timestamp <= end_date,
        Attendance.status == 'in'
    ).order_by(Attendance.timestamp).all()
    
    df = pd.DataFrame([{
        'Date': r.timestamp.strftime('%Y-%m-%d'),
        'Student Name': r.student.user.full_name if r.student and r.student.user else 'Unknown',
        'Student ID': r.student.admission_number if r.student else None,
        'Check-in Time': r.timestamp.strftime('%H:%M:%S'),
        'Confidence': f"{r.confidence:.1f}%" if r.confidence else 'N/A'
    } for r in records])
    
    output = df.to_csv(index=False)
    return Response(
        output,
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment; filename=attendance_chart_{start_date.strftime("%Y%m%d")}_to_{end_date.strftime("%Y%m%d")}.csv'}
    )


@app.route('/api/notify-parent/<int:student_id>', methods=['POST'])
@login_required
def notify_parent(student_id):
    student = Student.query.get_or_404(student_id)
    parent = student.parent

    if parent:
        notification = Notification(
            student_id=student.id,
            subject="Low Attendance Alert",
            message=f"{student.user.full_name} has low attendance",
            recipient_type='parent'
        )

        db.session.add(notification)
        db.session.commit()

    return jsonify({'success': True})


@app.route('/admin/attendance-test')
@login_required
@admin_required
def attendance_test():
    students = Student.query.filter_by(is_active=True).all()
    cameras = Camera.query.filter_by(is_active=True).all()
    return render_template('admin/attendance_test.html', students=students, cameras=cameras)


@app.route('/admin/remove-all-cameras', methods=['POST'])
@login_required
@admin_required
def remove_all_cameras():
    try:
        Camera.query.delete()
        db.session.commit()
        
        for cam_id in list(camera_manager.cameras.keys()):
            camera_manager.remove_camera(cam_id)
        
        flash('All cameras removed successfully', 'success')
        return redirect(url_for('live_monitoring'))
    except Exception as e:
        flash(f'Error removing cameras: {str(e)}', 'danger')
        return redirect(url_for('live_monitoring'))


@app.route('/admin/analytics')
@login_required
@admin_required
def analytics():
    active_session = AttendanceSession.query.filter_by(is_active=True).first()
    classes = Class.query.filter_by(is_active=True).all()
    
    return render_template('admin/analytics.html',
                         active_session=active_session,
                         classes=classes)


@app.route('/admin/notifications')
@login_required
@admin_required
def notifications_page():
    classes = Class.query.filter_by(is_active=True).all()
    return render_template('admin/notifications.html', classes=classes)


@app.route('/admin/low-attendance')
@login_required
@admin_required
def low_attendance_page():
    classes = Class.query.filter_by(is_active=True).all()
    return render_template('admin/low_attendance.html', classes=classes)


@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@admin_required
def settings():
    if request.method == 'POST':
        try:
            institute = Institute.query.first()
            if institute:
                institute.name = request.form.get('institute_name', institute.name)
                institute.address = request.form.get('institute_address', institute.address)
                institute.phone = request.form.get('institute_phone', institute.phone)
                institute.email = request.form.get('institute_email', institute.email)
            
            app.config['MINIMUM_ATTENDANCE'] = float(request.form.get('min_attendance', 75))
            app.config['ATTENDANCE_START_HOUR'] = int(request.form.get('start_hour', 8))
            app.config['ATTENDANCE_END_HOUR'] = int(request.form.get('end_hour', 17))
            
            db.session.commit()
            
            log_activity('INFO', 'settings', 'System settings updated')
            flash('Settings updated successfully!', 'success')
            
        except Exception as e:
            logger.error(f"Error updating settings: {str(e)}")
            flash(f'Error updating settings: {str(e)}', 'danger')
    
    institute = Institute.query.first()
    camera_count = Camera.query.filter_by(is_active=True).count()
    
    return render_template(
        'admin/settings.html',
        institute=institute,
        min_attendance=app.config['MINIMUM_ATTENDANCE'],
        start_hour=app.config['ATTENDANCE_START_HOUR'],
        end_hour=app.config['ATTENDANCE_END_HOUR'],
        camera_count=camera_count,
        storage_used="0.5",
        storage_total="10",
        last_backup="Never"
    )


@app.route('/admin/register-face')
@login_required
@admin_required
def register_face():
    students = Student.query.filter_by(is_active=True).all()
    return render_template('admin/register_face.html', students=students)


# ==================== SocketIO Events ====================

@socketio.on('connect')
def handle_connect():
    logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Client disconnected: {request.sid}")

@socketio.on('subscribe_camera')
def handle_subscribe_camera(data):
    camera_id = data.get('camera_id')
    room = f'camera_{camera_id}'
    emit('subscribed', {'camera_id': camera_id, 'room': room})

@socketio.on('timeline_update')
def handle_timeline_update(data):
    hour = data.get('hour')
    emit('timeline_updated', {'hour': hour}, broadcast=True)


# ==================== Logout ====================

@app.route('/logout')
@login_required
def logout():
    log_activity('INFO', 'auth', f'User logged out: {current_user.username}')
    logout_user()
    return redirect(url_for('admin_login'))


# ==================== Error Handlers ====================

@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    logger.error(f"Internal server error: {str(error)}")
    return render_template('errors/500.html'), 500

@app.route('/health')
def health_check():
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})


# ==================== Main ====================

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
        
        admin_exists = db.session.query(db.exists().where(User.username == 'admin')).scalar()
        if not admin_exists:
            admin = User(
                username='admin',
                email='admin@institute.com',
                role='admin',
                full_name='System Administrator'
            )
            admin.set_password('Admin@123')
            db.session.add(admin)
            db.session.commit()
            print("Default admin created: admin / Admin@123")
        
        if not Institute.query.first():
            institute = Institute(
                name=app.config['INSTITUTE_NAME'],
                address=app.config['INSTITUTE_ADDRESS'],
                phone=app.config['INSTITUTE_PHONE'],
                email=app.config['INSTITUTE_EMAIL']
            )
            db.session.add(institute)
            db.session.commit()
            print("Default institute created")
        
        print("Camera system ready - add cameras through the UI")
    
    print("\n" + "="*50)
    print("Attendance System Started!")
    print("="*50)
    print("Access the application at:")
    print("  → http://localhost:5000")
    print("  → http://127.0.0.1:5000")
    print("\nDefault Admin Credentials:")
    print("  Username: admin")
    print("  Password: Admin@123")
    print("\nTeacher Portal:")
    print("  Login with Employee ID and Password (same as ID)")
    print("  Create sessions with repeat options")
    print("  Send notifications to students")
    print("\nStudent Portal:")
    print("  Login with Admission Number")
    print("  View subjects and teachers")
    print("="*50 + "\n")
    
    socketio.run(app, debug=True, host='127.0.0.1', port=5000)