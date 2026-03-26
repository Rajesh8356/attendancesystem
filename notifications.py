import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
import threading
import time
from twilio.rest import Client

logger = logging.getLogger(__name__)

class NotificationService:
    def __init__(self, config):
        self.config = config
        
        # Email configuration
        self.smtp_server = config.get('SMTP_SERVER')
        self.smtp_port = config.get('SMTP_PORT')
        self.smtp_username = config.get('SMTP_USERNAME')
        self.smtp_password = config.get('SMTP_PASSWORD')
        
        # SMS configuration (Twilio)
        self.twilio_sid = config.get('TWILIO_ACCOUNT_SID')
        self.twilio_token = config.get('TWILIO_AUTH_TOKEN')
        self.twilio_phone = config.get('TWILIO_PHONE_NUMBER')
        
        # Notification queue
        self.notification_queue = []
        self.queue_lock = threading.Lock()
        
        # Start background processor
        self._start_processor()
    
    def _start_processor(self):
        """Start background thread to process notifications"""
        def process_queue():
            while True:
                time.sleep(2)  # Process every 2 seconds
                
                with self.queue_lock:
                    notifications = self.notification_queue.copy()
                    self.notification_queue.clear()
                
                for notification in notifications:
                    try:
                        if notification['channel'] in ['email', 'both']:
                            self._send_email_sync(notification)
                        if notification['channel'] in ['sms', 'both']:
                            self._send_sms_sync(notification)
                    except Exception as e:
                        logger.error(f"Error sending notification: {str(e)}")
                        
                        # Update status in database
                        from models import Notification as NotificationModel, db
                        if 'db_id' in notification:
                            notif = NotificationModel.query.get(notification['db_id'])
                            if notif:
                                notif.status = 'failed'
                                notif.error_message = str(e)
                                db.session.commit()
        
        thread = threading.Thread(target=process_queue, daemon=True)
        thread.start()
        logger.info("Notification processor started")
    
    def send_notification(self, recipient_type, recipient_id, subject, message, channel='email', student_id=None):
        """Queue a notification for sending"""
        from models import Notification as NotificationModel, db, User
        
        # Get recipient
        user = User.query.get(recipient_id)
        if not user:
            logger.error(f"Recipient not found: {recipient_id}")
            return None
        
        # Create notification record
        notification = NotificationModel(
            student_id=student_id,
            recipient_type=recipient_type,
            recipient_id=recipient_id,
            type='custom',
            channel=channel,
            subject=subject,
            message=message,
            status='pending'
        )
        db.session.add(notification)
        db.session.commit()
        
        # Queue for sending
        with self.queue_lock:
            self.notification_queue.append({
                'db_id': notification.id,
                'to_email': user.email if channel in ['email', 'both'] else None,
                'to_phone': user.phone if channel in ['sms', 'both'] else None,
                'subject': subject,
                'message': message,
                'channel': channel
            })
        
        return notification.id
    
    def send_email(self, to_email, subject, html_content, html=True):
        """Send email using SMTP"""
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.smtp_username
            msg['To'] = to_email
            msg['Subject'] = subject
            
            if html:
                msg.attach(MIMEText(html_content, 'html'))
            else:
                msg.attach(MIMEText(html_content, 'plain'))
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
            
            logger.info(f"Email sent to {to_email}")
            return True
        except Exception as e:
            logger.error(f"Error sending email: {str(e)}")
            return False
    
    def send_attendance_notification(self, student, status):
        """Send notification when attendance is recorded"""
        if not student.parent:
            return
        
        parent = student.parent.user
        
        if status == 'in':
            subject = f"Check-in Notification - {student.user.full_name}"
            message = f"""
            Dear {parent.full_name},
            
            This is to inform you that {student.user.full_name} has checked in at {datetime.now().strftime('%H:%M:%S')}.
            
            Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            Status: Present
            
            Thank you,
            {self.config.get('INSTITUTE_NAME', 'Attendance System')}
            """
        else:
            subject = f"Check-out Notification - {student.user.full_name}"
            message = f"""
            Dear {parent.full_name},
            
            This is to inform you that {student.user.full_name} has checked out at {datetime.now().strftime('%H:%M:%S')}.
            
            Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
            Status: Left
            
            Thank you,
            {self.config.get('INSTITUTE_NAME', 'Attendance System')}
            """
        
        self.send_notification(
            recipient_type='parent',
            recipient_id=parent.id,
            subject=subject,
            message=message,
            channel='both',
            student_id=student.id
        )
    
    def send_absence_alert(self, student):
        """Send absence alert to parent"""
        if not student.parent:
            return
        
        parent = student.parent.user
        
        subject = f"Absence Alert - {student.user.full_name}"
        message = f"""
        Dear {parent.full_name},
        
        This is to inform you that {student.user.full_name} was marked absent today ({datetime.now().strftime('%Y-%m-%d')}).
        
        Please ensure regular attendance is maintained. If this is an error, please contact the school.
        
        Regards,
        {self.config.get('INSTITUTE_NAME', 'Attendance System')}
        """
        
        self.send_notification(
            recipient_type='parent',
            recipient_id=parent.id,
            subject=subject,
            message=message,
            channel='both',
            student_id=student.id
        )
    
    def send_low_attendance_alert(self, student, percentage):
        """Send alert when attendance falls below threshold"""
        # Send to parent
        if student.parent:
            parent = student.parent.user
            
            subject = f"Low Attendance Alert - {student.user.full_name}"
            message = f"""
            Dear {parent.full_name},
            
            This is to inform you that {student.user.full_name}'s attendance is currently {percentage}%, 
            which is below the required minimum of {self.config.get('MINIMUM_ATTENDANCE', 75)}%.
            
            Please ensure regular attendance to maintain academic progress.
            
            Current Statistics:
            - Student: {student.user.full_name}
            - Admission No: {student.admission_number}
            - Current Attendance: {percentage}%
            - Required: {self.config.get('MINIMUM_ATTENDANCE', 75)}%
            
            Regards,
            {self.config.get('INSTITUTE_NAME', 'Attendance System')}
            """
            
            self.send_notification(
                recipient_type='parent',
                recipient_id=parent.id,
                subject=subject,
                message=message,
                channel='both',
                student_id=student.id
            )
        
        # Send to teacher - FIXED: Changed student.class to student.current_class
        if student.current_class and student.current_class.teacher:
            teacher = student.current_class.teacher.user
            
            subject = f"Low Attendance Alert - {student.user.full_name}"
            message = f"""
            Dear {teacher.full_name},
            
            This is to inform you that {student.user.full_name} ({student.admission_number}) from your class 
            has an attendance rate of {percentage}%, which is below the required minimum.
            
            Please take necessary action.
            
            Regards,
            {self.config.get('INSTITUTE_NAME', 'Attendance System')}
            """
            
            self.send_notification(
                recipient_type='teacher',
                recipient_id=teacher.id,
                subject=subject,
                message=message,
                channel='email',
                student_id=student.id
            )
    
    def send_welcome_message(self, user, password=None):
        """Send welcome message to new user"""
        subject = f"Welcome to {self.config.get('INSTITUTE_NAME', 'Attendance System')}"
        
        html_message = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px;">
                <h2 style="color: #4CAF50; text-align: center;">Welcome to {self.config.get('INSTITUTE_NAME', 'Attendance System')}!</h2>
                
                <p>Dear {user.full_name},</p>
                
                <p>Your account has been created successfully. You can now access the attendance system using the following credentials:</p>
                
                <table style="width: 100%; margin: 20px 0; padding: 10px; background-color: #f9f9f9; border-radius: 5px;">
                    <tr>
                        <td><strong>Username/Email:</strong></td>
                        <td>{user.email}</td>
                    </tr>
                    <tr>
                        <td><strong>Password:</strong></td>
                        <td>{password if password else 'Set by you'}</td>
                    </tr>
                </table>
                
                <p>Please log in and change your password if necessary.</p>
                
                <div style="text-align: center; margin: 30px 0;">
                    <a href="http://your-domain.com/login" style="background-color: #4CAF50; color: white; padding: 12px 30px; text-decoration: none; border-radius: 5px;">Login to System</a>
                </div>
                
                <p>If you have any questions, please contact the administration.</p>
                
                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                
                <p style="color: #666; font-size: 12px; text-align: center;">
                    This is an automated message from the Attendance Management System.<br>
                    Please do not reply to this email.
                </p>
            </div>
        </body>
        </html>
        """
        
        self.send_notification(
            recipient_type=user.role,
            recipient_id=user.id,
            subject=subject,
            message=html_message,
            channel='email'
        )
    
    def send_daily_report(self, student):
        """Send daily attendance report to parent"""
        if not student.parent:
            return
        
        from models import Attendance
        from datetime import date, timedelta
        from sqlalchemy import func
        
        parent = student.parent.user
        today = date.today()
        
        # Get today's attendance
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
        
        # Get weekly summary
        week_start = today - timedelta(days=today.weekday())
        weekly = Attendance.query.filter(
            Attendance.student_id == student.id,
            Attendance.timestamp >= week_start,
            Attendance.status == 'in'
        ).distinct(func.date(Attendance.timestamp)).count()
        
        # Calculate duration
        duration = None
        if check_in and check_out:
            delta = check_out.timestamp - check_in.timestamp
            hours = delta.seconds // 3600
            minutes = (delta.seconds % 3600) // 60
            duration = f"{hours}h {minutes}m"
        
        html_message = f"""
        <html>
        <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
            <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #ddd; border-radius: 10px;">
                <h2 style="color: #4CAF50; text-align: center;">Daily Attendance Report</h2>
                <p style="text-align: center; color: #666;">{today.strftime('%A, %B %d, %Y')}</p>
                
                <p>Dear {parent.full_name},</p>
                
                <p>Here is today's attendance report for <strong>{student.user.full_name}</strong>:</p>
                
                <table style="width: 100%; margin: 20px 0; border-collapse: collapse;">
                    <tr style="background-color: #4CAF50; color: white;">
                        <th style="padding: 10px; text-align: left;">Activity</th>
                        <th style="padding: 10px; text-align: left;">Time</th>
                    </tr>
                    <tr style="background-color: #f9f9f9;">
                        <td style="padding: 10px; border: 1px solid #ddd;">Check In</td>
                        <td style="padding: 10px; border: 1px solid #ddd;">{check_in.timestamp.strftime('%H:%M:%S') if check_in else 'Not recorded'}</td>
                    </tr>
                    <tr>
                        <td style="padding: 10px; border: 1px solid #ddd;">Check Out</td>
                        <td style="padding: 10px; border: 1px solid #ddd;">{check_out.timestamp.strftime('%H:%M:%S') if check_out else 'Not recorded'}</td>
                    </tr>
                    <tr style="background-color: #f9f9f9;">
                        <td style="padding: 10px; border: 1px solid #ddd;">Total Duration</td>
                        <td style="padding: 10px; border: 1px solid #ddd;">{duration if duration else 'N/A'}</td>
                    </tr>
                </table>
                
                <h3 style="color: #4CAF50; margin-top: 30px;">Weekly Summary</h3>
                <p>Days present this week: <strong>{weekly}</strong> out of 5</p>
                
                <div style="margin: 30px 0; padding: 20px; background-color: #f9f9f9; border-radius: 5px;">
                    <p style="margin: 0;"><strong>Admission Number:</strong> {student.admission_number}</p>
                    <p style="margin: 5px 0 0;"><strong>Class:</strong> {student.current_class.name if student.current_class else 'N/A'}</p>
                </div>
                
                <p>To view complete attendance history, please <a href="http://your-domain.com/parent/login" style="color: #4CAF50;">log in to the parent portal</a>.</p>
                
                <hr style="border: none; border-top: 1px solid #ddd; margin: 20px 0;">
                
                <p style="color: #666; font-size: 12px; text-align: center;">
                    This is an automated message from the Attendance Management System.<br>
                    Please do not reply to this email.
                </p>
            </div>
        </body>
        </html>
        """
        
        self.send_notification(
            recipient_type='parent',
            recipient_id=parent.id,
            subject=f"Daily Attendance Report - {student.user.full_name} - {today}",
            message=html_message,
            channel='email',
            student_id=student.id
        )
    
    def _send_email_sync(self, notification):
        """Synchronous email sending"""
        try:
            msg = MIMEMultipart('alternative')
            msg['From'] = self.smtp_username
            msg['To'] = notification['to_email']
            msg['Subject'] = notification['subject']
            
            # Check if message is HTML
            if notification['message'].strip().startswith('<html'):
                msg.attach(MIMEText(notification['message'], 'html'))
            else:
                msg.attach(MIMEText(notification['message'], 'plain'))
            
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
            
            logger.info(f"Email sent to {notification['to_email']}")
            
            # Update status in database
            if 'db_id' in notification:
                from models import Notification as NotificationModel, db
                notif = NotificationModel.query.get(notification['db_id'])
                if notif:
                    notif.status = 'sent'
                    notif.sent_at = datetime.utcnow()
                    db.session.commit()
            
        except Exception as e:
            logger.error(f"Error sending email: {str(e)}")
            raise
    
    def _send_sms_sync(self, notification):
        """Synchronous SMS sending via Twilio"""
        try:
            if not self.twilio_sid or not self.twilio_token:
                logger.warning("Twilio not configured")
                return
            
            client = Client(self.twilio_sid, self.twilio_token)
            
            message = client.messages.create(
                body=notification['message'][:160],  # SMS length limit
                from_=self.twilio_phone,
                to=notification['to_phone']
            )
            
            logger.info(f"SMS sent to {notification['to_phone']}, SID: {message.sid}")
            
        except Exception as e:
            logger.error(f"Error sending SMS: {str(e)}")
            raise