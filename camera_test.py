import cv2

for i in range(5):
    cam = cv2.VideoCapture(i, cv2.CAP_DSHOW)
    if cam.isOpened():
        success, frame = cam.read()
        if success:
            print(f"Camera working at index {i}")
            cv2.imshow("Camera Test", frame)
            cv2.waitKey(3000)
            cam.release()
            cv2.destroyAllWindows()
            break
    cam.release()
else:
    print("No camera working")