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
    # Default quality reduced to 60 to keep JPEG size below 128KB for ESP32 SRAM constraints
    parser.add_argument("--quality", "-q", type=int, default=60, help="JPEG compression quality (1-100, default 60)")
    parser.add_argument("--fps", "-f", type=int, default=30, help="Target maximum FPS (default 30)")
    return parser.parse_args()

def auto_detect_port():
    ports = list_ports.comports()
    # Look for USB-CDC / USB-Serial devices
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
    
    # Target resolution for M5Stack Tab5-P4
    WIDTH, HEIGHT = 1280, 720
    frame_interval = 1.0 / args.fps
    
    with mss.mss() as sct:
        # Get primary monitor info
        monitor = sct.monitors[1] # 1 is the primary monitor, 0 is the all-in-one virtual screen
        logger.info(f"Capture thread started for monitor: {monitor}")
        
        while capture_running:
            start_time = time.time()

            # 1. Capture screen
            screenshot = sct.grab(monitor)

            # 2. Convert to numpy array (OpenCV format)
            # mss returns BGRA, OpenCV uses BGR natively
            img = np.array(screenshot)

            # 3. Resize to Tab5 resolution
            if img.shape[1] != WIDTH or img.shape[0] != HEIGHT:
                img = cv2.resize(img, (WIDTH, HEIGHT), interpolation=cv2.INTER_LINEAR)

            # OpenCV encode needs 3 channels, slice off Alpha channel if present
            if img.shape[2] == 4:
                img = img[:, :, :3]

            # 4. Compress to JPEG
            encode_param = [int(cv2.IMWRITE_JPEG_QUALITY), args.quality]
            result, encimg = cv2.imencode('.jpg', img, encode_param)

            if not result:
                logger.error("Failed to encode JPEG frame.")
                continue

            jpeg_data = encimg.tobytes()

            # Update shared variable safely
            with frame_lock:
                latest_jpeg_data = jpeg_data
                
            elapsed = time.time() - start_time
            if elapsed < frame_interval:
                time.sleep(frame_interval - elapsed)

def main():
    global latest_jpeg_data, capture_running
    args = parse_args()

    # Sync header
    HEADER = bytes([0xAA, 0xBB, 0xCC, 0xDD])

    # Start capture thread
    capture_thread = threading.Thread(target=capture_thread_func, args=(args,), daemon=True)
    capture_thread.start()

    try:
        while True:
            port = args.port
            if not port:
                port = auto_detect_port()

            if not port:
                logger.info("No serial port found. Waiting for device...")
                time.sleep(0.5)
                continue

            logger.info(f"Connecting to M5Stack on port: {port}...")
            try:
                # Timeout is set to 5.0s to avoid hanging if ACK is lost or device is slow
                ser = serial.Serial(port, baudrate=115200, timeout=5.0)
                # Standard configuration to enable communication on ESP32 USB-CDC
                ser.dtr = True
                ser.rts = True
            except Exception as e:
                logger.error(f"Error opening serial port {port}: {e}. Retrying in 0.5 seconds...")
                time.sleep(0.5)
                continue

            logger.info("Serial port opened successfully.")
            logger.info("Waiting for M5Stack to boot and send D:READY signal...")

            try:
                while True:
                    # Wait for D:READY from device before starting next frame
                    ready = False
                    # Try reading until we hit the ready signal, to clear out garbage or catch traces
                    while not ready:
                        # Strip any hidden garbage characters like \x00 that might interfere with exact string matching
                        line = ser.readline().decode('ascii', errors='ignore').replace('\x00', '').strip()
                        if line == "D:READY":
                            ready = True
                        elif line.startswith("D:"):
                            logger.debug(f"Device Trace: {line}")
                        elif line:
                            logger.info(f"Device Output: {line}")

                    # Clear any lingering data right before sending
                    ser.reset_input_buffer()
                    ser.reset_output_buffer()

                    # Get latest frame - block until first frame is ready to avoid dropping D:READY
                    jpeg_data = None
                    while jpeg_data is None:
                        with frame_lock:
                            jpeg_data = latest_jpeg_data
                        if not jpeg_data:
                            time.sleep(0.005)

                    # 5. Build packet
                    size = len(jpeg_data)
                    logger.info(f"Sending frame: size={size} bytes")
                    size_bytes = size.to_bytes(4, byteorder="big")

                    # 6. Send packet (Two-Stage Handshake)
                    # First, send header and size
                    ser.write(HEADER)
                    ser.write(size_bytes)
                    ser.flush()

                    # Wait for device to acknowledge header/size (D:SYNC)
                    sync_ok = False
                    while not sync_ok:
                        line = ser.readline().decode('ascii', errors='ignore').replace('\x00', '').strip()
                        if line == "D:SYNC":
                            sync_ok = True
                        elif line.startswith("D:"):
                            logger.debug(f"Device Trace: {line}")
                        elif line:
                            logger.info(f"Device Output: {line}")
                        if not line: # Timeout
                            logger.warning("Timeout waiting for D:SYNC. Aborting frame.")
                            break

                    if not sync_ok:
                        continue

                    # Send large payload in chunks to prevent USB CDC buffer overflow / Errno 5
                    chunk_size = 4096
                    for i in range(0, len(jpeg_data), chunk_size):
                        chunk = jpeg_data[i:i+chunk_size]
                        ser.write(chunk)
                        ser.flush()

                    # 7. Wait for final ACK (0x06)
                    while True:
                        ack = ser.read(1)
                        if not ack:
                            # Timeout
                            logger.warning("ACK timeout. Retrying...")
                            break

                        if ack[0] == 0x06:
                            # Success
                            break
                        elif ack[0] == 0x15:
                            # NACK
                            err_code = ser.read(1)
                            if err_code:
                                if err_code[0] == 0xE1:
                                    logger.warning("Received NACK. Error: M5Stack buffer allocation failed (NULL).")
                                elif err_code[0] == 0xE2:
                                    logger.warning("Received NACK. Error: Frame size exceeds device buffer limit.")
                                elif err_code[0] == 0xE3:
                                    logger.warning("Received NACK. Error: Device serial read timeout.")
                                else:
                                    logger.warning(f"Received NACK with unknown error code: {err_code}")
                            else:
                                logger.warning("Received NACK without error code.")
                            break
                        elif ack == b'D':
                            # Debug Trace String from M5Stack
                            trace_msg = ser.readline().decode('ascii', errors='ignore').replace('\x00', '').strip()
                            logger.debug(f"Device Trace: D{trace_msg}")
                        elif ack == b'\r' or ack == b'\n':
                            pass # Ignore rogue newlines
                        else:
                            logger.warning(f"Unexpected response from M5Stack: {ack}")

            except serial.SerialException as e:
                logger.error(f"Serial communication error: {e}. Reconnecting...")
            except OSError as e:
                logger.error(f"OS error: {e}. Device might have been disconnected. Reconnecting...")
            except Exception as e:
                logger.error(f"Unexpected error in communication loop: {e}. Reconnecting...")
            finally:
                if 'ser' in locals() and ser.is_open:
                    ser.close()
                    logger.info("Serial port closed for reconnection.")

    except KeyboardInterrupt:
        logger.info("\nStopping monitor host...")
        capture_running = False
        capture_thread.join(timeout=1.0)

if __name__ == "__main__":
    main()
