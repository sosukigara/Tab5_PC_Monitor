#!/usr/bin/env python3
import sys
import time
import argparse
from io import BytesIO
import serial
from serial.tools import list_ports
from PIL import Image
import mss

def parse_args():
    parser = argparse.ArgumentParser(description="M5Stack Tab5 Linux Mirror Monitor Host")
    parser.add_argument("--port", "-p", help="Serial port (e.g. /dev/ttyACM0 or COM4). Auto-detected if not specified.")
    parser.add_argument("--quality", "-q", type=int, default=80, help="JPEG compression quality (1-100, default 80)")
    parser.add_argument("--fps", "-f", type=int, default=60, help="Target maximum FPS (default 60)")
    parser.add_argument("--baud", "-b", type=int, default=1500000, help="Serial baud rate (default 1500000)")
    return parser.parse_args()

def auto_detect_port():
    ports = list_ports.comports()
    # Prefer Espressif TinyUSB CDC (Native USB), fallback to USB-Serial-JTAG
    for p in ports:
        if "303A" in p.hwid and "1002" in p.hwid:
            return p.device
    for p in ports:
        if "303A" in p.hwid and "1001" in p.hwid:
            return p.device
    for p in ports:
        if "USB" in p.description or "ACM" in p.device:
            return p.device
    return None

def main():
    args = parse_args()
    
    port = args.port
    if not port:
        port = auto_detect_port()
        if not port:
            print("Error: No serial port specified and auto-detection failed.")
            sys.exit(1)
            
    print(f"Connecting to M5Stack on port: {port}...")
    try:
        ser = serial.Serial(port, baudrate=args.baud, timeout=1.0)
    except Exception as e:
        print(f"Error opening serial port: {e}")
        sys.exit(1)
        
    print("Serial port opened successfully.")
    print("Waiting 1.5 seconds for M5Stack to boot up...")
    time.sleep(1.5)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    
    # Target resolution for M5Stack Tab5-P4
    WIDTH, HEIGHT = 1280, 720
    
    # Sync header
    HEADER = bytes([0xAA, 0xBB, 0xCC, 0xDD])
    
    frame_interval = 1.0 / args.fps
    
    with mss.mss() as sct:
        # Get primary monitor info
        monitor = sct.monitors[1] # 1 is the primary monitor, 0 is the all-in-one virtual screen
        print(f"Capturing monitor: {monitor}")
        
        last_frame_time = time.time()
        
        try:
            while True:
                now = time.time()
                elapsed = now - last_frame_time
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
                last_frame_time = time.time()
                
                # 1. Capture screen
                screenshot = sct.grab(monitor)
                
                # 2. Convert to PIL Image
                # mss returns raw BGRA bytes
                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                
                # 3. Resize to Tab5 resolution (1280x720)
                if img.size != (WIDTH, HEIGHT):
                    img = img.resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)
                
                # 4. Compress to JPEG in memory
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=args.quality)
                jpeg_data = buf.getvalue()
                
                # 5. Build packet
                size = len(jpeg_data)
                size_bytes = size.to_bytes(4, byteorder="big")
                
                # 6. Send packet (single write to minimize USB transactions)
                ser.write(HEADER + size_bytes + jpeg_data)
                ser.flush()
                
                # 7. Wait for ACK (0x06)
                ack = ser.read(1)
                if not ack:
                    # Timeout
                    print("Warning: ACK timeout. Retrying...")
                elif ack[0] == 0x06:
                    # Success
                    pass
                elif ack[0] == 0x15:
                    # NACK
                    print("Warning: Received NACK. Frame might have been too large or corrupted.")
                else:
                    print(f"Warning: Unexpected response from M5Stack: {ack}")
                    
        except KeyboardInterrupt:
            print("\nStopping monitor host...")
        finally:
            ser.close()
            print("Serial port closed.")

if __name__ == "__main__":
    main()
