import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Flask
    SECRET_KEY = os.getenv('SECRET_KEY', 'dev-key-change-in-production')
    
    FLASK_DEBUG = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    
    # Database - PostgreSQL
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = os.getenv('DB_PORT', '5432')
    DB_NAME = os.getenv('DB_NAME', 'attendance_system')
    DB_USER = os.getenv('DB_USER', 'postgres')
    DB_PASSWORD = os.getenv('DB_PASSWORD', 'postgres')
    
    SQLALCHEMY_DATABASE_URI = f'postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Face++ API
    FACE_API_KEY = os.getenv('FACE_API_KEY', 'Cz3NEYhnMRBpsF4b9WkCQ4d7E5mY6MpM')
    FACE_API_SECRET = os.getenv('FACE_API_SECRET', 'bkRPjHityhARYqzLJuIucRvb1iJHAU7A')
    
    # Twilio
    TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
    TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
    TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
    
    # Email
    SMTP_SERVER = os.getenv('SMTP_SERVER', 'smtp.gmail.com')
    SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
    SMTP_USERNAME = os.getenv('SMTP_USERNAME')
    SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
    
    # NVR
    NVR_IP = os.getenv('NVR_IP')
    NVR_USERNAME = os.getenv('NVR_USERNAME')
    NVR_PASSWORD = os.getenv('NVR_PASSWORD')
    
    # Redis
    REDIS_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
    
    # Institute
    INSTITUTE_NAME = os.getenv('INSTITUTE_NAME', 'Attendance System')
    INSTITUTE_PHONE = os.getenv('INSTITUTE_PHONE')
    INSTITUTE_EMAIL = os.getenv('INSTITUTE_EMAIL')
    INSTITUTE_ADDRESS = os.getenv('INSTITUTE_ADDRESS')
    
    # Attendance
    MINIMUM_ATTENDANCE = float(os.getenv('MINIMUM_ATTENDANCE', 75))
    ATTENDANCE_START_HOUR = int(os.getenv('ATTENDANCE_START_HOUR', 8))
    ATTENDANCE_END_HOUR = int(os.getenv('ATTENDANCE_END_HOUR', 17))
    
    # Paths
    BASE_DIR = os.path.abspath(os.path.dirname(__file__))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'static', 'uploads')
    RECORDING_PATH = os.path.join(BASE_DIR, 'recordings')
    LOG_DIR = os.path.join(BASE_DIR, 'logs')
    
    # Create directories
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    os.makedirs(RECORDING_PATH, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)