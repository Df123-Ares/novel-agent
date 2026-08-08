"""
USR-ISF1005 RTSP 实时预览脚本
功能：拉取相机 RTSP 流，OpenCV 窗口实时显示画面
用法：python 01_rtsp_preview.py
退出：按 q 键
"""

import cv2
import sys
import time

# 相机默认 RTSP 地址
RTSP_URL = "rtsp://admin:admin@192.168.1.100:554/stream"

# 可选的备用地址（不同固件版本可能不同）
ALT_URLS = [
    "rtsp://admin:admin@192.168.1.100:554/h264",
    "rtsp://admin:admin@192.168.1.100:554/stream1",
    "rtsp://192.168.1.100:554/stream",
]


def connect_camera(url: str, retries: int = 3) -> cv2.VideoCapture | None:
    """尝试连接相机 RTSP 流，支持重试"""
    for attempt in range(retries):
        print(f"[尝试 {attempt + 1}/{retries}] 连接 {url} ...")
        cap = cv2.VideoCapture(url)
        # 降低缓冲区减少延迟
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        time.sleep(1)
        if cap.isOpened():
            print(f"✓ 连接成功: {url}")
            return cap
        cap.release()
        print(f"✗ 连接失败，等待 2 秒...")
        time.sleep(2)
    return None


def main():
    print("=" * 60)
    print("USR-ISF1005 RTSP 实时预览")
    print("=" * 60)
    print(f"主地址: {RTSP_URL}")
    print("按 'q' 键退出 | 按 's' 键截图保存")
    print("按 'f' 键切换全屏 | 按 'p' 键暂停/播放")
    print()

    # 尝试连接
    cap = connect_camera(RTSP_URL)

    # 主地址失败则尝试备用地址
    if cap is None:
        print("\n主地址失败，尝试备用地址...")
        for alt_url in ALT_URLS:
            cap = connect_camera(alt_url, retries=1)
            if cap is not None:
                break

    if cap is None:
        print("\n[错误] 所有地址均连接失败，请检查：")
        print("  1. 相机是否已通电（POWER 灯亮）")
        print("  2. 网线是否插在台式机后置网口")
        print("  3. 网卡 IP 是否设为 192.168.1.10")
        print("  4. ping 192.168.1.100 是否通")
        sys.exit(1)

    # 获取视频信息
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"\n视频信息: {width}x{height} @ {fps:.1f} FPS")
    print("=" * 60)

    screenshot_count = 0
    paused = False
    fullscreen = False
    window_name = "USR-ISF1005 Camera Preview"

    cv2.namedWindow(window_name, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window_name, 1280, 720)

    fps_counter = []
    last_time = time.time()

    while True:
        if not paused:
            ret, frame = cap.read()
            if not ret:
                print("[警告] 读取帧失败，尝试重连...")
                cap.release()
                cap = connect_camera(RTSP_URL, retries=1)
                if cap is None:
                    print("[错误] 重连失败，退出")
                    break
                continue

            # FPS 计算
            current_time = time.time()
            fps_counter.append(1 / (current_time - last_time))
            last_time = current_time
            if len(fps_counter) > 30:
                fps_counter.pop(0)
            avg_fps = sum(fps_counter) / len(fps_counter)

            # 叠加 FPS 显示
            cv2.putText(
                frame,
                f"FPS: {avg_fps:.1f}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 255, 0),
                2,
            )

            cv2.imshow(window_name, frame)

        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            print("\n用户退出")
            break
        elif key == ord("s"):
            filename = f"screenshot_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
            cv2.imwrite(filename, frame)
            screenshot_count += 1
            print(f"✓ 截图已保存: {filename} (第 {screenshot_count} 张)")
        elif key == ord("f"):
            fullscreen = not fullscreen
            if fullscreen:
                cv2.setWindowProperty(
                    window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN
                )
            else:
                cv2.setWindowProperty(
                    window_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL
                )
        elif key == ord("p"):
            paused = not paused
            state = "暂停" if paused else "播放"
            print(f"视频已{state}")

    cap.release()
    cv2.destroyAllWindows()
    print(f"\n程序结束，共截图 {screenshot_count} 张")


if __name__ == "__main__":
    main()
