import cv2

rtsp_url = "rtsp://admin:Alpha2025@192.168.1.19:554/stream1"

cap = cv2.VideoCapture(rtsp_url)

while True:
    ret, frame = cap.read()

    if not ret:
        print("Failed to grab frame")
        break

    cv2.imshow("IP Camera", frame)

    if cv2.waitKey(1) == 27:
        break

cap.release()
cv2.destroyAllWindows()