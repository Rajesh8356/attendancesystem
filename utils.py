import json
import csv
import io
import base64
import hashlib
import random
import string
from datetime import datetime, timedelta, date
from flask import send_file
import pandas as pd
import numpy as np
import cv2
import logging

logger = logging.getLogger(__name__)

def generate_password(length=8):
    """Generate a random password"""
    characters = string.ascii_letters + string.digits + '!@#$%^&*'
    password = ''.join(random.choice(characters) for i in range(length))
    return password

def hash_string(text):
    """Create a hash of a string"""
    return hashlib.sha256(text.encode()).hexdigest()

def calculate_attendance_percentage(student_id, days=30):
    """Calculate attendance percentage for a student"""
    from models import Attendance, db
    from sqlalchemy import func
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    total_days = db.session.query(func.count(func.distinct(func.date(Attendance.timestamp)))).filter(
        Attendance.student_id == student_id,
        Attendance.timestamp >= start_date,
        Attendance.status == 'in'
    ).scalar() or 0
    
    if total_days == 0:
        return 0
    
    return (total_days / days) * 100

def generate_attendance_report(start_date, end_date, student_id=None, class_id=None):
    """Generate attendance report in various formats"""
    from models import Attendance, Student, Class, db
    
    # Build query
    query = db.session.query(
        Student.admission_number,
        Student.user.has(full_name=True).label('name'),
        Class.name.label('class_name'),
        func.date(Attendance.timestamp).label('date'),
        Attendance.status,
        Attendance.timestamp,
        Attendance.confidence
    ).join(Student).join(Class, Student.class_id == Class.id)
    
    query = query.filter(func.date(Attendance.timestamp) >= start_date)
    query = query.filter(func.date(Attendance.timestamp) <= end_date)
    
    if student_id:
        query = query.filter(Student.id == student_id)
    
    if class_id:
        query = query.filter(Student.class_id == class_id)
    
    query = query.order_by(Attendance.timestamp)
    
    results = query.all()
    
    # Process into DataFrame
    data = []
    for row in results:
        data.append({
            'Admission Number': row[0],
            'Name': row[1],
            'Class': row[2],
            'Date': row[3],
            'Status': row[4].upper(),
            'Time': row[5].strftime('%H:%M:%S'),
            'Confidence': f"{row[6]:.1f}%" if row[6] else 'N/A'
        })
    
    df = pd.DataFrame(data)
    return df

def export_to_csv(df, filename):
    """Export DataFrame to CSV"""
    output = io.StringIO()
    df.to_csv(output, index=False)
    output.seek(0)
    
    return send_file(
        io.BytesIO(output.getvalue().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename
    )

def export_to_excel(df, filename):
    """Export DataFrame to Excel"""
    output = io.BytesIO()
    
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Attendance')
    
    output.seek(0)
    
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name=filename
    )

def decode_base64_image(image_data):
    """Decode base64 image to numpy array"""
    try:
        if ',' in image_data:
            image_data = image_data.split(',')[1]
        
        image_bytes = base64.b64decode(image_data)
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        return img
        
    except Exception as e:
        logger.error(f"Error decoding image: {str(e)}")
        return None

def encode_image_to_base64(image):
    """Encode numpy image to base64"""
    try:
        _, buffer = cv2.imencode('.jpg', image)
        return base64.b64encode(buffer).decode('utf-8')
        
    except Exception as e:
        logger.error(f"Error encoding image: {str(e)}")
        return None

def calculate_peak_hours(start_date=None, end_date=None):
    """Calculate peak attendance hours"""
    from models import Attendance, db
    from sqlalchemy import func, extract
    
    if not start_date:
        start_date = datetime.now() - timedelta(days=30)
    if not end_date:
        end_date = datetime.now()
    
    results = db.session.query(
        extract('hour', Attendance.timestamp).label('hour'),
        func.count(Attendance.id).label('count')
    ).filter(
        Attendance.timestamp >= start_date,
        Attendance.timestamp <= end_date
    ).group_by(
        extract('hour', Attendance.timestamp)
    ).order_by('hour').all()
    
    hours = [int(r[0]) for r in results]
    counts = [r[1] for r in results]
    
    peak_hour = hours[counts.index(max(counts))] if counts else None
    
    return {
        'hours': hours,
        'counts': counts,
        'peak_hour': peak_hour,
        'peak_count': max(counts) if counts else 0
    }

def get_daily_trend(days=30):
    """Get daily attendance trend"""
    from models import Attendance, Student, db
    from sqlalchemy import func
    
    end_date = datetime.now()
    start_date = end_date - timedelta(days=days)
    
    results = db.session.query(
        func.date(Attendance.timestamp).label('date'),
        func.count(func.distinct(Attendance.student_id)).label('count')
    ).filter(
        Attendance.timestamp >= start_date,
        Attendance.status == 'in'
    ).group_by(
        func.date(Attendance.timestamp)
    ).order_by('date').all()
    
    dates = [r[0] for r in results]
    counts = [r[1] for r in results]
    
    return {
        'dates': dates,
        'counts': counts,
        'total_students': Student.query.filter_by(is_active=True).count()
    }

def log_false_positive(detected_student_id, actual_student_id, confidence, image_path):
    """Log false positive detection for admin verification"""
    from models import FalsePositiveLog, db
    
    log = FalsePositiveLog(
        detected_student_id=detected_student_id,
        actual_student_id=actual_student_id,
        confidence=confidence,
        image_path=image_path,
        timestamp=datetime.utcnow()
    )
    
    db.session.add(log)
    db.session.commit()
    
    return log.id

def format_timedelta(td):
    """Format timedelta to human readable string"""
    if not td:
        return 'N/A'
    
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    
    if hours > 0:
        return f"{hours}h {minutes}m"
    else:
        return f"{minutes}m"

def validate_date_range(start_date, end_date):
    """Validate date range"""
    if start_date > end_date:
        return False, "Start date must be before end date"
    
    if (end_date - start_date).days > 365:
        return False, "Date range cannot exceed 1 year"
    
    return True, "Valid"

def paginate_query(query, page=1, per_page=20):
    """Paginate SQLAlchemy query"""
    offset = (page - 1) * per_page
    return query.offset(offset).limit(per_page).all()

def get_week_dates(date=None):
    """Get start and end dates of the week"""
    if not date:
        date = datetime.now().date()
    
    start = date - timedelta(days=date.weekday())
    end = start + timedelta(days=6)
    
    return start, end

def get_month_dates(date=None):
    """Get start and end dates of the month"""
    if not date:
        date = datetime.now().date()
    
    start = date.replace(day=1)
    if date.month == 12:
        end = date.replace(year=date.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        end = date.replace(month=date.month + 1, day=1) - timedelta(days=1)
    
    return start, end