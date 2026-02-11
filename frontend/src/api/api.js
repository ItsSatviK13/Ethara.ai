import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://127.0.0.1:8000';

const api = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Employee APIs
export const employeeAPI = {
  getAll: () => api.get('/api/employees'),
  getById: (employeeId) => api.get(`/api/employees/${employeeId}`),
  create: (employee) => api.post('/api/employees', employee),
  delete: (employeeId) => api.delete(`/api/employees/${employeeId}`),
};

// Attendance APIs
export const attendanceAPI = {
  getAll: (params = {}) => api.get('/api/attendance', { params }),
  create: (attendance) => api.post('/api/attendance', attendance),
  getStats: () => api.get('/api/attendance/stats'),
};

export default api;