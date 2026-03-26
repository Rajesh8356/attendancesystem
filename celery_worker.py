from celery import Celery
from app import app
from models import db, Student, Attendance, Notification
from datetime import datetime, timedelta
import logging

celery = Celery('attendance_tasks', broker=app.config['REDIS_URL'])

@celery.task
def send_daily_summary():
    """Send daily attendance summary to parents"""
    with app.app_context():
        from notifications import NotificationService
        notification_service = NotificationService(app.config)
        
        students = Student.query.filter_by(is_active=True).all()
        today = datetime.now().date()
        
        for student in students:
            if student.parent:
                notification_service.send_daily_report(student)
        
        return f"Sent daily summary to {len(students)} students"

@celery.task
def check_low_attendance():
    """Check for students with low attendance"""
    with app.app_context():
        from notifications import NotificationService
        notification_service = NotificationService(app.config)
        
        thirty_days_ago = datetime.now() - timedelta(days=30)
        students = Student.query.filter_by(is_active=True).all()
        
        low_attendance_count = 0
        
        for student in students:
            total_days = db.session.query(db.func.count(db.func.distinct(db.func.date(Attendance.timestamp)))).filter(
                Attendance.student_id == student.id,
                Attendance.timestamp >= thirty_days_ago,
                Attendance.status == 'in'
            ).scalar() or 0
            
            if total_days > 0:
                percentage = (total_days / 30) * 100
                if percentage < app.config['MINIMUM_ATTENDANCE']:
                    notification_service.send_low_attendance_alert(student, round(percentage, 1))
                    low_attendance_count += 1
        
        return f"Found {low_attendance_count} students with low attendance"

@celery.task
def cleanup_old_recordings():
    """Clean up recordings older than 30 days"""
    with app.app_context():
        from models import Recording
        import os
        
        thirty_days_ago = datetime.now() - timedelta(days=30)
        old_recordings = Recording.query.filter(Recording.start_time < thirty_days_ago).all()
        
        deleted_count = 0
        for recording in old_recordings:
            try:
                if os.path.exists(recording.file_path):
                    os.remove(recording.file_path)
                db.session.delete(recording)
                deleted_count += 1
            except Exception as e:
                logging.error(f"Error deleting recording {recording.id}: {str(e)}")
        
        db.session.commit()
        return f"Deleted {deleted_count} old recordings"