-- Create database
CREATE DATABASE attendance_system;

-- Connect to database
\c attendance_system;

-- Create extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Create roles
CREATE USER attendance_user WITH PASSWORD 'attendance123';
GRANT ALL PRIVILEGES ON DATABASE attendance_system TO attendance_user;

-- Set timezone
ALTER DATABASE attendance_system SET timezone TO 'UTC';

-- Create schema
CREATE SCHEMA IF NOT EXISTS attendance AUTHORIZATION attendance_user;

-- Set search path
ALTER DATABASE attendance_system SET search_path TO attendance, public;

-- Grant schema privileges
GRANT ALL ON SCHEMA attendance TO attendance_user;
GRANT ALL ON SCHEMA public TO attendance_user;

-- Create tables (these will be created by SQLAlchemy, but here's the structure)
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(80) UNIQUE NOT NULL,
    email VARCHAR(120) UNIQUE NOT NULL,
    password_hash VARCHAR(200),
    role VARCHAR(20) NOT NULL,
    full_name VARCHAR(200),
    phone VARCHAR(20),
    is_active BOOLEAN DEFAULT TRUE,
    last_login TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS institutes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(200) NOT NULL,
    address TEXT,
    phone VARCHAR(20),
    email VARCHAR(120),
    logo_path VARCHAR(200),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert default institute
INSERT INTO institutes (name, address, phone, email) 
VALUES ('Default Institute', 'Address', '+1234567890', 'info@institute.com')
ON CONFLICT DO NOTHING;

-- Insert default admin
INSERT INTO users (username, email, password_hash, role, full_name, phone)
VALUES (
    'admin',
    'admin@institute.com',
    crypt('Admin@123', gen_salt('bf')),
    'admin',
    'System Administrator',
    '+1234567890'
)
ON CONFLICT (username) DO NOTHING;

-- Create indexes
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_role ON users(role);
CREATE INDEX idx_users_username ON users(username);