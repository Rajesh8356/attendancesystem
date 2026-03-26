// static/js/line_detection.js

class LineDetectionSystem {
    constructor() {
        this.cameras = {};
        this.activeSession = null;
        this.socket = io();
        this.linePositions = {};
        this.detectionIntervals = {};
        this.crossedStudents = {};
        this.absentStudents = [];
        
        this.initSocket();
        this.loadSession();
        this.loadLinePositions();
    }
    
    initSocket() {
        this.socket.on('connect', () => {
            console.log('Connected to line detection server');
        });
        
        this.socket.on('attendance_update', (data) => {
            if (data.crossing_detected) {
                this.handleLineCrossing(data);
            }
            this.addToLog(data);
        });
        
        this.socket.on('session_ended', (data) => {
            this.showAbsentNotification(data);
        });
    }
    
    loadSession() {
        fetch('/api/attendance-session/active')
            .then(response => response.json())
            .then(data => {
                if (data.success && data.session) {
                    this.activeSession = data.session;
                    this.startSessionTimer();
                }
            });
    }
    
    loadLinePositions() {
        // Load saved line positions from localStorage
        Object.keys(localStorage).forEach(key => {
            if (key.startsWith('line_')) {
                const cameraId = key.replace('line_', '');
                this.linePositions[cameraId] = parseInt(localStorage.getItem(key));
            }
        });
    }
    
    initializeCamera(cameraId, containerId) {
        const container = document.getElementById(containerId);
        if (!container) return;
        
        this.cameras[cameraId] = {
            container: container,
            linePosition: this.linePositions[cameraId] || 240,
            detectionBoxes: {},
            crossedToday: 0
        };
        
        // Add line control
        this.addLineControl(cameraId, container);
        
        // Start detection
        this.startDetection(cameraId);
    }
    
    addLineControl(cameraId, container) {
        const controlDiv = document.createElement('div');
        controlDiv.className = 'line-control-container';
        controlDiv.innerHTML = `
            <div class="line-control-label">
                <span><i class="fas fa-arrows-alt-v"></i> Detection Line</span>
                <span id="line-value-${cameraId}">${this.cameras[cameraId].linePosition}px</span>
            </div>
            <input type="range" class="line-control-slider" id="line-slider-${cameraId}"
                   min="50" max="430" value="${this.cameras[cameraId].linePosition}"
                   onchange="lineDetection.updateLinePosition('${cameraId}', this.value)">
        `;
        
        container.appendChild(controlDiv);
        
        // Add visual line
        this.addVisualLine(cameraId, container);
    }
    
    addVisualLine(cameraId, container) {
        const line = document.createElement('div');
        line.className = 'detection-line';
        line.id = `line-${cameraId}`;
        line.style.top = this.cameras[cameraId].linePosition + 'px';
        container.appendChild(line);
    }
    
    updateLinePosition(cameraId, position) {
        this.cameras[cameraId].linePosition = parseInt(position);
        this.linePositions[cameraId] = parseInt(position);
        
        // Update visual line
        const line = document.getElementById(`line-${cameraId}`);
        if (line) {
            line.style.top = position + 'px';
        }
        
        // Update display
        const valueDisplay = document.getElementById(`line-value-${cameraId}`);
        if (valueDisplay) {
            valueDisplay.textContent = position + 'px';
        }
        
        // Save to localStorage
        localStorage.setItem(`line_${cameraId}`, position);
        
        // Send to server
        fetch('/api/camera/line-position', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ camera_id: cameraId, position: position })
        });
    }
    
    startDetection(cameraId) {
        if (this.detectionIntervals[cameraId]) {
            clearInterval(this.detectionIntervals[cameraId]);
        }
        
        // Check for detections every 500ms
        this.detectionIntervals[cameraId] = setInterval(() => {
            this.checkDetections(cameraId);
        }, 500);
    }
    
    checkDetections(cameraId) {
        const feed = document.getElementById(`feed-${cameraId}`);
        if (!feed || !feed.complete) return;
        
        // Create a canvas to analyze the frame
        const canvas = document.createElement('canvas');
        canvas.width = feed.naturalWidth || 640;
        canvas.height = feed.naturalHeight || 480;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(feed, 0, 0);
        
        // Convert to base64 and send for detection
        const imageData = canvas.toDataURL('image/jpeg', 0.7);
        
        fetch('/api/detect-faces', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ 
                image: imageData, 
                camera_id: cameraId,
                line_position: this.cameras[cameraId].linePosition
            })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success && data.faces) {
                this.drawDetectionBoxes(cameraId, data.faces);
            }
        })
        .catch(error => console.error('Detection error:', error));
    }
    
    drawDetectionBoxes(cameraId, faces) {
        const container = document.getElementById(`detection-boxes-${cameraId}`);
        if (!container) return;
        
        // Clear old boxes
        container.innerHTML = '';
        
        const lineY = this.cameras[cameraId].linePosition;
        
        faces.forEach(face => {
            const rect = face.rectangle;
            const centerY = rect.top + rect.height / 2;
            
            // Determine if crossing or about to cross
            const isBelow = centerY > lineY;
            const distanceToLine = Math.abs(centerY - lineY);
            const isNearLine = distanceToLine < 50;
            
            // Create detection box
            const box = document.createElement('div');
            box.className = `detection-box ${isBelow ? 'in' : 'out'}`;
            if (isNearLine) box.classList.add('crossing');
            
            box.style.left = rect.left + 'px';
            box.style.top = rect.top + 'px';
            box.style.width = rect.width + 'px';
            box.style.height = rect.height + 'px';
            
            // Add label
            const label = document.createElement('div');
            label.className = `detection-label ${isBelow ? 'in' : 'out'}`;
            label.textContent = `${face.student.name} (${Math.round(face.confidence)}%)`;
            
            box.appendChild(label);
            container.appendChild(box);
        });
        
        // Update detection count
        const countEl = document.getElementById(`detection-count-${cameraId}`);
        if (countEl) {
            countEl.textContent = faces.length;
        }
    }
    
    handleLineCrossing(data) {
        const cameraId = data.camera_id;
        
        if (!this.crossedStudents[cameraId]) {
            this.crossedStudents[cameraId] = new Set();
        }
        
        this.crossedStudents[cameraId].add(data.student_id);
        
        // Update crossed count
        const crossedEl = document.getElementById(`crossed-${cameraId}`);
        if (crossedEl) {
            crossedEl.textContent = this.crossedStudents[cameraId].size;
        }
        
        // Show crossing animation
        this.showCrossingAnimation(cameraId, data);
    }
    
    showCrossingAnimation(cameraId, data) {
        const container = document.getElementById(`camera-${cameraId}`);
        if (!container) return;
        
        const flash = document.createElement('div');
        flash.style.position = 'absolute';
        flash.style.top = '0';
        flash.style.left = '0';
        flash.style.width = '100%';
        flash.style.height = '100%';
        flash.style.backgroundColor = 'rgba(6, 214, 160, 0.3)';
        flash.style.animation = 'fadeOut 1s ease';
        flash.style.pointerEvents = 'none';
        flash.style.zIndex = '100';
        
        container.appendChild(flash);
        
        setTimeout(() => {
            flash.remove();
        }, 1000);
        
        // Play sound
        this.playCrossingSound();
    }
    
    playCrossingSound() {
        try {
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const oscillator = audioContext.createOscillator();
            const gainNode = audioContext.createGain();
            
            oscillator.connect(gainNode);
            gainNode.connect(audioContext.destination);
            
            oscillator.frequency.value = 800;
            gainNode.gain.value = 0.1;
            
            oscillator.start();
            setTimeout(() => oscillator.stop(), 150);
        } catch (e) {
            console.log('Audio not supported');
        }
    }
    
    addToLog(data) {
        const logContainer = document.getElementById('attendance-log');
        if (!logContainer) return;
        
        // Remove placeholder if exists
        if (logContainer.children.length === 1 && logContainer.children[0].classList.contains('text-center')) {
            logContainer.innerHTML = '';
        }
        
        const logItem = document.createElement('div');
        logItem.className = `attendance-log-item ${data.status}`;
        
        const time = new Date(data.timestamp).toLocaleTimeString();
        const cameraIcon = data.camera_id?.includes('in') ? '📷' : '🎥';
        
        logItem.innerHTML = `
            <div class="log-time">
                <i class="far fa-clock"></i> ${time} ${cameraIcon}
            </div>
            <div class="log-name">
                <i class="fas fa-user-graduate"></i> ${data.student_name}
            </div>
            <div class="log-details">
                <span><i class="fas fa-${data.status === 'in' ? 'sign-in-alt' : 'sign-out-alt'}"></i> ${data.status.toUpperCase()}</span>
                <span><i class="fas fa-chart-line"></i> ${data.confidence}%</span>
                <span><i class="fas fa-arrows-alt-v"></i> Line Crossing</span>
            </div>
        `;
        
        logContainer.insertBefore(logItem, logContainer.firstChild);
        
        // Keep only last 20 items
        while (logContainer.children.length > 20) {
            logContainer.removeChild(logContainer.lastChild);
        }
    }
    
    startSessionTimer() {
        if (!this.activeSession) return;
        
        const timerDiv = document.createElement('div');
        timerDiv.className = 'session-timer';
        timerDiv.id = 'session-timer';
        timerDiv.innerHTML = `
            <div class="timer-display">
                <i class="fas fa-clock"></i>
                <span id="session-time">00:00:00</span>
            </div>
            <div class="timer-progress">
                <div class="timer-progress-bar" id="session-progress" style="width: 100%"></div>
            </div>
        `;
        
        document.body.appendChild(timerDiv);
        
        this.updateSessionTimer();
    }
    
    updateSessionTimer() {
        if (!this.activeSession) return;
        
        const start = new Date(this.activeSession.start_time).getTime();
        const end = new Date(this.activeSession.end_time).getTime();
        
        const update = () => {
            const now = new Date().getTime();
            const total = end - start;
            const elapsed = now - start;
            const remaining = end - now;
            
            if (remaining <= 0) {
                document.getElementById('session-time').textContent = '00:00:00';
                document.getElementById('session-progress').style.width = '0%';
                this.handleSessionEnd();
                return;
            }
            
            // Format time
            const hours = Math.floor(remaining / (1000 * 60 * 60));
            const minutes = Math.floor((remaining % (1000 * 60 * 60)) / (1000 * 60));
            const seconds = Math.floor((remaining % (1000 * 60)) / 1000);
            
            document.getElementById('session-time').textContent = 
                `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
            
            // Update progress
            const progress = (elapsed / total) * 100;
            document.getElementById('session-progress').style.width = Math.min(100, progress) + '%';
        };
        
        update();
        this.timerInterval = setInterval(update, 1000);
    }
    
    handleSessionEnd() {
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
        }
        
        // Mark absent students
        fetch('/api/attendance-session/end/mark-absent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ session_id: this.activeSession.id })
        })
        .then(response => response.json())
        .then(data => {
            if (data.success) {
                this.showAbsentNotification(data);
            }
        });
    }
    
    showAbsentNotification(data) {
        const notification = document.createElement('div');
        notification.className = 'toast-notification toast-warning';
        notification.innerHTML = `
            <i class="fas fa-user-clock"></i>
            <div>
                <strong>Session Ended</strong>
                <p>${data.absent_count} students marked absent</p>
            </div>
        `;
        
        document.body.appendChild(notification);
        
        setTimeout(() => {
            notification.classList.add('fade-out');
            setTimeout(() => notification.remove(), 300);
        }, 5000);
    }
    
    cleanup() {
        Object.values(this.detectionIntervals).forEach(interval => {
            clearInterval(interval);
        });
        
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
        }
    }
}

// Initialize on pages that need line detection
document.addEventListener('DOMContentLoaded', function() {
    if (document.querySelector('.camera-feed-wrapper')) {
        window.lineDetection = new LineDetectionSystem();
    }
});