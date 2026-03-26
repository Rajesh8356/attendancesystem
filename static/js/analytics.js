// Analytics JavaScript

class AnalyticsDashboard {
    constructor() {
        this.charts = {};
        this.dateRange = {
            start: moment().subtract(29, 'days'),
            end: moment()
        };
        
        this.initDatePicker();
        this.initCharts();
        this.loadData();
    }
    
    initDatePicker() {
        $('#daterange').daterangepicker({
            startDate: this.dateRange.start,
            endDate: this.dateRange.end,
            ranges: {
                'Today': [moment(), moment()],
                'Yesterday': [moment().subtract(1, 'days'), moment().subtract(1, 'days')],
                'Last 7 Days': [moment().subtract(6, 'days'), moment()],
                'Last 30 Days': [moment().subtract(29, 'days'), moment()],
                'This Month': [moment().startOf('month'), moment().endOf('month')],
                'Last Month': [moment().subtract(1, 'month').startOf('month'), moment().subtract(1, 'month').endOf('month')]
            }
        }, (start, end) => {
            this.dateRange.start = start;
            this.dateRange.end = end;
            this.loadData();
        });
    }
    
    initCharts() {
        // Daily Trend Chart
        const trendCtx = document.getElementById('dailyTrendChart').getContext('2d');
        this.charts.dailyTrend = new Chart(trendCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Attendance Count',
                    data: [],
                    borderColor: '#4e73df',
                    backgroundColor: 'rgba(78, 115, 223, 0.05)',
                    pointBackgroundColor: '#4e73df',
                    pointBorderColor: '#fff',
                    pointHoverBackgroundColor: '#fff',
                    pointHoverBorderColor: '#4e73df',
                    lineTension: 0.3,
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Number of Students'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Date'
                        }
                    }
                },
                plugins: {
                    legend: {
                        display: false
                    }
                }
            }
        });
        
        // Class Chart
        const classCtx = document.getElementById('classChart').getContext('2d');
        this.charts.class = new Chart(classCtx, {
            type: 'bar',
            data: {
                labels: [],
                datasets: [{
                    label: 'Attendance Rate (%)',
                    data: [],
                    backgroundColor: 'rgba(28, 200, 138, 0.7)',
                    borderColor: '#1cc88a',
                    borderWidth: 1
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        max: 100,
                        title: {
                            display: true,
                            text: 'Percentage (%)'
                        }
                    }
                }
            }
        });
        
        // Peak Hours Chart
        const peakCtx = document.getElementById('peakHoursChart').getContext('2d');
        this.charts.peakHours = new Chart(peakCtx, {
            type: 'line',
            data: {
                labels: [],
                datasets: [{
                    label: 'Check-ins',
                    data: [],
                    borderColor: '#f6c23e',
                    backgroundColor: 'rgba(246, 194, 62, 0.05)',
                    pointBackgroundColor: '#f6c23e',
                    pointBorderColor: '#fff',
                    fill: true,
                    lineTension: 0.3,
                    borderWidth: 2
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                scales: {
                    y: {
                        beginAtZero: true,
                        title: {
                            display: true,
                            text: 'Number of Check-ins'
                        }
                    },
                    x: {
                        title: {
                            display: true,
                            text: 'Hour of Day'
                        }
                    }
                }
            }
        });
        
        // Gender Chart
        const genderCtx = document.getElementById('genderChart').getContext('2d');
        this.charts.gender = new Chart(genderCtx, {
            type: 'doughnut',
            data: {
                labels: ['Male', 'Female', 'Other'],
                datasets: [{
                    data: [0, 0, 0],
                    backgroundColor: ['#4e73df', '#1cc88a', '#36b9cc'],
                    hoverBackgroundColor: ['#2e59d9', '#17a673', '#2c9faf'],
                    hoverBorderColor: 'rgba(234, 236, 244, 1)',
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'bottom'
                    }
                }
            }
        });
    }
    
    loadData() {
        const params = new URLSearchParams({
            start_date: this.dateRange.start.format('YYYY-MM-DD'),
            end_date: this.dateRange.end.format('YYYY-MM-DD')
        });
        
        Promise.all([
            fetch(`/api/analytics/daily-trend?${params}`).then(r => r.json()),
            fetch(`/api/analytics/class-attendance?${params}`).then(r => r.json()),
            fetch(`/api/analytics/peak-hours?${params}`).then(r => r.json()),
            fetch(`/api/analytics/gender-distribution`).then(r => r.json()),
            fetch(`/api/analytics/low-attendance?${params}`).then(r => r.json())
        ]).then(([daily, classData, peak, gender, lowAttendance]) => {
            this.updateDailyTrend(daily);
            this.updateClassChart(classData);
            this.updatePeakHours(peak);
            this.updateGenderChart(gender);
            this.updateLowAttendance(lowAttendance);
            this.updateSummaryCards(daily, peak);
        });
    }
    
    updateDailyTrend(data) {
        this.charts.dailyTrend.data.labels = data.dates;
        this.charts.dailyTrend.data.datasets[0].data = data.counts;
        this.charts.dailyTrend.update();
    }
    
    updateClassChart(data) {
        this.charts.class.data.labels = data.classes;
        this.charts.class.data.datasets[0].data = data.percentages;
        this.charts.class.update();
    }
    
    updatePeakHours(data) {
        this.charts.peakHours.data.labels = data.hours.map(h => `${h}:00`);
        this.charts.peakHours.data.datasets[0].data = data.counts;
        this.charts.peakHours.update();
    }
    
    updateGenderChart(data) {
        this.charts.gender.data.datasets[0].data = [data.male, data.female, data.other];
        this.charts.gender.update();
    }
    
    updateLowAttendance(data) {
        const tbody = document.getElementById('lowAttendanceBody');
        tbody.innerHTML = '';
        
        data.forEach(student => {
            const row = tbody.insertRow();
            row.innerHTML = `
                <td>${student.admission_number}</td>
                <td>${student.name}</td>
                <td>${student.class}</td>
                <td>
                    <div class="progress">
                        <div class="progress-bar bg-${student.percentage < 75 ? 'danger' : 'success'}" 
                             style="width: ${student.percentage}%">
                            ${student.percentage}%
                        </div>
                    </div>
                </td>
                <td>
                    <span class="badge bg-${student.percentage < 75 ? 'danger' : 'success'}">
                        ${student.percentage < 75 ? 'Critical' : 'Good'}
                    </span>
                </td>
                <td>
                    <a href="/admin/student/${student.id}" class="btn btn-sm btn-primary">
                        <i class="fas fa-eye"></i> View
                    </a>
                </td>
            `;
        });
    }
    
    updateSummaryCards(daily, peak) {
        // Calculate average daily attendance
        const avg = daily.counts.reduce((a, b) => a + b, 0) / daily.counts.length || 0;
        document.getElementById('avgDaily').textContent = Math.round(avg);
        
        // Find peak attendance
        const maxAttendance = Math.max(...daily.counts);
        document.getElementById('peakAttendance').textContent = maxAttendance;
        
        // Find peak hour
        const maxHourIndex = peak.counts.indexOf(Math.max(...peak.counts));
        document.getElementById('peakHour').textContent = `${peak.hours[maxHourIndex]}:00`;
    }
    
    exportData(format) {
        const params = new URLSearchParams({
            start_date: this.dateRange.start.format('YYYY-MM-DD'),
            end_date: this.dateRange.end.format('YYYY-MM-DD'),
            format: format
        });
        
        window.location.href = `/api/export/analytics?${params}`;
    }
}

// Initialize analytics
document.addEventListener('DOMContentLoaded', function() {
    window.analytics = new AnalyticsDashboard();
});