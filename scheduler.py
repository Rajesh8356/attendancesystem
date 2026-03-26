from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from celery_worker import send_daily_summary, check_low_attendance, cleanup_old_recordings
import atexit

def start_scheduler():
    """Start the background scheduler"""
    scheduler = BackgroundScheduler()
    
    # Send daily summary at 8 PM every day
    scheduler.add_job(
        func=send_daily_summary.delay,
        trigger=CronTrigger(hour=20, minute=0),
        id='daily_summary',
        name='Send daily attendance summary',
        replace_existing=True
    )
    
    # Check low attendance at 9 AM every day
    scheduler.add_job(
        func=check_low_attendance.delay,
        trigger=CronTrigger(hour=9, minute=0),
        id='low_attendance_check',
        name='Check for low attendance',
        replace_existing=True
    )
    
    # Clean up old recordings at 2 AM every Sunday
    scheduler.add_job(
        func=cleanup_old_recordings.delay,
        trigger=CronTrigger(day_of_week='sun', hour=2, minute=0),
        id='cleanup_recordings',
        name='Clean up old recordings',
        replace_existing=True
    )
    
    scheduler.start()
    
    # Shut down the scheduler when exiting the app
    atexit.register(lambda: scheduler.shutdown())
    
    return scheduler