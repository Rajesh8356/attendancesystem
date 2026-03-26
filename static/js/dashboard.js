// Dashboard specific JavaScript

// Real-time dashboard updates
class DashboardUpdater {
    constructor() {
        this.statsInterval = null;
        this.charts = {};
    }
    
    start() {
        // Update stats every 10 seconds
        this.statsInterval = setInterval(() => this.updateStats(), 10000);
        
        // Initialize charts
        this.initCharts();
    }
    
    stop() {
        if (this.statsInterval) {
            clearInterval(this.statsInterval);
        }
    }
    
    updateStats() {
        fetch('/api/attendance/stats')
            .then(response => response.json())
            .then(data => {
                this.updateStatCards(data);
                this.updateCharts(data);
            });
    }
    
    updateStatCards(data) {
        // Update stat cards with animation
        this.animateValue('totalStudents', data.total_students);
        this.animateValue('checkinsToday', data.checkins_today);
        this.animateValue('checkoutsToday', data.checkouts_today);
        this.animateValue('activeNow', data.active_now);
        this.animateValue('attendanceRate', data.attendance_rate + '%');
    }
    
    animateValue(elementId, newValue) {
        const element = document.getElementById(elementId);
        if (!element) return;
        
        const oldValue = parseFloat(element.textContent) || 0;
        const duration = 500;
        const startTime = performance.now();
        
        const animate = (currentTime) => {
            const elapsed = currentTime - startTime;
            const progress = Math.min(elapsed / duration, 1);
            
            if (typeof newValue === 'number') {
                const current = oldValue + (newValue - oldValue) * progress;
                element.textContent = Math.round(current);
            } else {
                element.textContent = newValue;
            }
            
            if (progress < 1) {
                requestAnimationFrame(animate);
            }
        };
        
        requestAnimationFrame(animate);
    }
    
    initCharts() {
        // Daily trend chart
        const trendCtx = document.getElementById('dailyTrendChart')?.getContext('2d');
        if (trendCtx) {
            this.charts.dailyTrend = new Chart(trendCtx, {
                type: 'line',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Attendance',
                        data: [],
                        borderColor: '#4e73df',
                        backgroundColor: 'rgba(78, 115, 223, 0.05)',
                        tension: 0.3
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        y: {
                            beginAtZero: true
                        }
                    }
                }
            });
        }
        
        // Class chart
        const classCtx = document.getElementById('classChart')?.getContext('2d');
        if (classCtx) {
            this.charts.class = new Chart(classCtx, {
                type: 'bar',
                data: {
                    labels: [],
                    datasets: [{
                        label: 'Present',
                        data: [],
                        backgroundColor: '#1cc88a'
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false
                }
            });
        }
    }
    
    updateCharts(data) {
        // Update daily trend chart
        if (this.charts.dailyTrend) {
            this.charts.dailyTrend.data.labels = data.dates || [];
            this.charts.dailyTrend.data.datasets[0].data = data.counts || [];
            this.charts.dailyTrend.update();
        }
        
        // Update class chart
        if (this.charts.class) {
            this.charts.class.data.labels = data.classes || [];
            this.charts.class.data.datasets[0].data = data.classCounts || [];
            this.charts.class.update();
        }
    }
}

// Initialize dashboard
document.addEventListener('DOMContentLoaded', function() {
    const updater = new DashboardUpdater();
    updater.start();
    
    // Clean up on page unload
    window.addEventListener('beforeunload', function() {
        updater.stop();
    });
});