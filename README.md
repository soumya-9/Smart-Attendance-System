# Smart-Attendance-System
AI-based Smart Attendance System using Face Recognition, Anti-Spoofing, Emotion Detection, and GPS-Based Validation.
This project is an intelligent attendance management system that uses multiple AI technologies to ensure secure and accurate attendance marking. It integrates facial recognition, emotion detection, anti-spoofing techniques, and GPS-based validation to prevent fraud and improve reliability.

🚀 Features
✅ Face Recognition for identity verification
✅ Anti-Spoofing to prevent fake attendance (photo/video attacks)
✅ Emotion Detection for real-time facial analysis
✅ GPS-Based Validation (location-based attendance control)
✅ Time-Based Attendance System
✅ Admin & Student Dashboard
✅ AI Assistant for attendance analytics
✅ Attendance Reports & Data Visualization
🧠 Technologies Used
Python
Flask
OpenCV (cv2)
DeepFace
face_recognition (dlib)
SQLite Database
HTML, CSS, JavaScript
⚙️ System Workflow
Admin creates a class session with time and GPS location
Student logs into the system
Face recognition verifies identity
Anti-spoofing checks for real person
Emotion detection analyzes facial state
Attendance is marked (temporary)
GPS tracking starts after attendance window
If student remains within location → attendance finalized
