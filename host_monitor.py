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
        # Timeout is set to 5.0s to avoid hanging if ACK is lost or device is slow
        ser = serial.Serial(port, baudrate=115200, timeout=5.0)
        # Standard configuration to enable communication on ESP32 USB-CDC
        ser.dtr = True
        ser.rts = True
    except Exception as e:
        print(f"Error opening serial port: {e}")
        sys.exit(1)
        
    print("Serial port opened successfully.")
    print("Waiting 2.0 seconds for M5Stack to boot up...")
    time.sleep(2.0)
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
                print(f"Sending frame: size={size} bytes")
                size_bytes = size.to_bytes(4, byteorder="big")
                
                # 6. Send packet
                ser.write(HEADER)
                ser.write(size_bytes)

                # Send large payload in chunks to prevent USB CDC buffer overflow / Errno 5
                chunk_size = 4096
                for i in range(0, len(jpeg_data), chunk_size):
                    chunk = jpeg_data[i:i+chunk_size]
                    ser.write(chunk)
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
                    err_code = ser.read(1)
                    if err_code:
                        if err_code[0] == 0xE1:
                            print("Warning: Received NACK. Error: M5Stack buffer allocation failed (NULL).")
                        elif err_code[0] == 0xE2:
                            print("Warning: Received NACK. Error: Frame size exceeds device buffer limit.")
                        elif err_code[0] == 0xE3:
                            print("Warning: Received NACK. Error: Device serial read timeout.")
                        else:
                            print(f"Warning: Received NACK with unknown error code: {err_code}")
                    else:
                        print("Warning: Received NACK without error code.")
                else:
                    print(f"Warning: Unexpected response from M5Stack: {ack}")
                    
        except KeyboardInterrupt:
            print("\nStopping monitor host...")
        finally:
            ser.close()
            print("Serial port closed.")

if __name__ == "__main__":
    main()
