import cv2

# Use encoded password
rtsp_url = "rtsp://admin:Alpha%40123@192.168.1.48:554/"

# 🔥 Force FFmpeg backend (better for RTSP)
cap = cv2.VideoCapture(rtsp_url, cv2.CAP_FFMPEG)

# 🔥 Reduce buffer (CRITICAL to avoid lag)
cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

# 🔽 Try forcing lower resolution (depends on camera support)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

if not cap.isOpened():
    print("Error: Unable to open RTSP stream")
    exit()

while True:
    # 🔥 Grab latest frame (skip old buffered frames)
    cap.grab()
    ret, frame = cap.read()

    if not ret:
        print("Frame drop / reconnecting...")
        continue

    # Optional: resize again (extra safety)
    frame = cv2.resize(frame, (640, 480))

    cv2.imshow("Smooth Stream", frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()