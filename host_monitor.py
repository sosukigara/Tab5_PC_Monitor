#!/usr/bin/env python3
import sys
import time
import argparse
import logging
import threading
import serial
from serial.tools import list_ports
import mss
import cv2
import numpy as np
from turbojpeg import TurboJPEG, TJPF_BGR

# Set up logging to file and console
logger = logging.getLogger("HostMonitor")
logger.setLevel(logging.DEBUG)
fh = logging.FileHandler("host_monitor.log")
fh.setLevel(logging.DEBUG)
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
fh.setFormatter(formatter)
ch.setFormatter(formatter)
logger.addHandler(fh)
logger.addHandler(ch)

def parse_args():
    parser = argparse.ArgumentParser(description="M5Stack Tab5 Linux Mirror Monitor Host")
    parser.add_argument("--port", "-p", help="Serial port (e.g. /dev/ttyACM0 or COM4). Auto-detected if not specified.")
    parser.add_argument("--quality", "-q", type=int, default=10, help="JPEG compression quality (1-100, default 10)")
    parser.add_argument("--fps", "-f", type=int, default=30, help="Target maximum FPS (default 30)")
    parser.add_argument("--width", type=int, default=640, help="Capture width (default 640)")
    parser.add_argument("--height", type=int, default=360, help="Capture height (default 360)")
    parser.add_argument("--max-payload", type=int, default=65536, help="Maximum JPEG payload size in bytes (default 65536)")
    parser.add_argument("--ack-timeout", type=float, default=10.0, help="ACK timeout in seconds (default 10)")
    return parser.parse_args()

def auto_detect_port():
    ports = list_ports.comports()
    for p in ports:
        if "USB" in p.description or "ACM" in p.device or "COM" in p.device:
            return p.device
    return None

# Shared variable for the latest captured frame and its lock
latest_jpeg_data = None
frame_lock = threading.Lock()
capture_running = True

def capture_thread_func(args):
    global latest_jpeg_data, capture_running
    WIDTH, HEIGHT = args.width, args.height
    frame_interval = 1.0 / args.fps
    use_turbojpeg = True
    try:
        jpeg = TurboJPEG()
    except Exception as e:
        logger.warning(f"Failed to initialize TurboJPEG: {e}")
        logger.warning("Falling back to OpenCV JPEG encoder (slower but works without libturbojpeg)")
        use_turbojpeg = False
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        logger.info(f"Capture thread started for monitor: {monitor}")
        while capture_running:
            start_time = time.time()
            screenshot = sct.grab(monitor)
            img = np.array(screenshot)
            if img.shape[1] != WIDTH or img.shape[0] != HEIGHT:
                img = cv2.resize(img, (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)
            if img.shape[2] == 4:
                img = img[:, :, :3]
            try:
                if use_turbojpeg:
                    jpeg_data = jpeg.encode(img, quality=args.quality, pixel_format=TJPF_BGR)
                else:
                    _, jpeg_arr = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), args.quality])
                    jpeg_data = jpeg_arr.tobytes()
            except Exception as e:
                logger.error(f"Failed to encode JPEG frame: {e}")
                continue
            with frame_lock:
                latest_jpeg_data = jpeg_data
            elapsed = time.time() - start_time
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

def main():
    global latest_jpeg_data, capture_running
    args = parse_args()
    HEADER = bytes([0xAA, 0xBB, 0xCC, 0xDD])
    capture_thread = threading.Thread(target=capture_thread_func, args=(args,), daemon=True)
    capture_thread.start()
    fps_frame_count = 0
    fps_window_start = time.time()
    current_quality = args.quality
    consecutive_timeouts = 0
    try:
        while True:
            port = args.port or auto_detect_port()
            if not port:
                logger.info("No serial port found. Waiting for device...")
                time.sleep(0.5)
                continue
            logger.info(f"Connecting to M5Stack on port: {port}...")
            try:
                ser = serial.Serial(port, baudrate=3000000, timeout=15.0)
                ser.dtr = True
                ser.rts = True
            except Exception as e:
                logger.error(f"Error opening serial port {port}: {e}. Retrying in 0.5 seconds...")
                time.sleep(0.5)
                continue
            logger.info("Serial port opened successfully.")
            logger.info("Waiting for M5Stack to boot and send D:READY signal...")
            ready = False
            while not ready:
                line = ser.readline().decode('ascii', errors='ignore').replace('\\x00', '').strip()
                if any(k in line for k in ("READY", "ADY", "EADY", "RDY", "DY")):
                    ready = True
                elif line:
                    logger.debug(f"Device Output: {line}")
            # No dummy read needed. Rely on active retry handshake.
            try:
                while True:
                    jpeg_data = None
                    while jpeg_data is None:
                        if not capture_running:
                            logger.error("Capture thread is dead. Exiting application.")
                            sys.exit(1)
                        with frame_lock:
                            jpeg_data = latest_jpeg_data
                        if not jpeg_data:
                            time.sleep(0.005)
                    if len(jpeg_data) > args.max_payload:
                        logger.warning(f"JPEG payload {len(jpeg_data)} exceeds max {args.max_payload} bytes – skipping frame.")
                        continue
                    size = len(jpeg_data)
                    logger.debug(f"Sending frame: size={size} bytes, quality={current_quality}")
                    size_bytes = size.to_bytes(4, byteorder="big")
                    ser.write(HEADER + size_bytes)
                    ser.flush()
                    # Wait for D:SYNC
                    sync_ok = False
                    while not sync_ok:
                        line = ser.readline().decode('ascii', errors='ignore').replace('\\x00', '').strip()
                        if line == "D:SYNC":
                            sync_ok = True
                        elif "D:NACK" in line:
                            logger.warning(f"Received NACK from M5Stack: {line}. Retrying frame...")
                            raise serial.SerialException(f"NACK received: {line}")
                        elif any(k in line for k in ("READY", "ADY", "EADY", "RDY", "DY")):
                            logger.debug("M5Stack is in READY state. Resending header...")
                            ser.write(HEADER + size_bytes)
                            ser.flush()
                        elif line.startswith("D:"):
                            logger.debug(f"Device Trace: {line}")
                        elif line:
                            logger.info(f"Device Output: {line}")
                    CHUNK_SIZE = 65536
                    for i in range(0, size, CHUNK_SIZE):
                        ser.write(jpeg_data[i:i+CHUNK_SIZE])
                    ser.flush()
                    # ACK now comes immediately after M5Stack receives (before draw!)
                    ack_start = time.time()
                    ack_deadline = ack_start + args.ack_timeout
                    while True:
                        if time.time() > ack_deadline:
                            logger.warning("ACK timeout. Reconnecting...")
                            consecutive_timeouts += 1
                            raise serial.SerialException("ACK timeout")
                        ack = ser.read(1)
                        if not ack:
                            continue
                        if ack[0] == 0x06:
                            fps_frame_count += 1
                            now = time.time()
                            elapsed_window = now - fps_window_start
                            if elapsed_window >= 5.0:
                                actual_fps = fps_frame_count / elapsed_window
                                logger.info(f"[FPS] Actual throughput: {actual_fps:.1f} fps (last {elapsed_window:.0f}s, {fps_frame_count} frames)")
                                fps_frame_count = 0
                                fps_window_start = now
                            consecutive_timeouts = 0
                            break
                        elif ack[0] == 0x15:
                            err_code = ser.read(1)
                            if err_code:
                                logger.warning(f"Received NACK with error code: {err_code.hex()}")
                            else:
                                logger.warning("Received NACK without error code.")
                            break
                        elif ack == b'D':
                            trace_msg = ser.readline().decode('ascii', errors='ignore').replace('\\x00', '').strip()
                            logger.debug(f"Device Trace: D{trace_msg}")
                        elif ack in (b'\r', b'\n'):
                            pass
                        else:
                            logger.warning(f"Unexpected response from M5Stack: {ack}. Aborting frame to retry...")
                            raise serial.SerialException("Unexpected response during ACK wait")
                    if consecutive_timeouts >= 3 and current_quality > 5:
                        current_quality = max(5, current_quality - 5)
                        logger.info(f"Reducing JPEG quality to {current_quality} after {consecutive_timeouts} consecutive timeouts")
                        consecutive_timeouts = 0
            except (serial.SerialException, OSError) as e:
                logger.error(f"Serial communication error: {e}. Reconnecting...")
            finally:
                if ser.is_open:
                    ser.close()
                    logger.info("Serial port closed for reconnection.")
    except KeyboardInterrupt:
        logger.info("\nStopping monitor host...")
        capture_running = False
        capture_thread.join(timeout=1.0)

if __name__ == "__main__":
    main()
