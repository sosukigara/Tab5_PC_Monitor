#!/usr/bin/env python3
"""Quick benchmark: connects to M5Stack, streams frames for 10s, reports FPS."""
import sys, time
from io import BytesIO
import serial
from PIL import Image
import mss

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM4"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 1500000

ser = serial.Serial(PORT, baudrate=BAUD, timeout=5.0)
ser.dtr = True
ser.rts = True
time.sleep(1.5)
ser.reset_input_buffer()
ser.reset_output_buffer()

WIDTH, HEIGHT = 1280, 720
HEADER = bytes([0xAA, 0xBB, 0xCC, 0xDD])

frame_count = 0
total_bytes = 0
start = time.time()
last_log = start

print(f"Streaming for 10 seconds on {PORT} @ {BAUD} baud...")

with mss.mss() as sct:
    monitor = sct.monitors[1]
    try:
        while time.time() - start < 10:
            screenshot = sct.grab(monitor)
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            if img.size != (WIDTH, HEIGHT):
                img = img.resize((WIDTH, HEIGHT), Image.Resampling.BILINEAR)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80)
            jpeg_data = buf.getvalue()

            ser.write(HEADER + len(jpeg_data).to_bytes(4, "big") + jpeg_data)
            ser.flush()
            ack = ser.read(1)

            if ack and ack[0] == 0x06:
                frame_count += 1
                total_bytes += len(jpeg_data)
                now = time.time()
                if now - last_log >= 1.0:
                    fps = frame_count / (now - start)
                    kb_s = total_bytes / (now - start) / 1024
                    print(f"\r{fps:.1f} FPS, {kb_s:.0f} KB/s, {len(jpeg_data)} B/frame", end="")
                    last_log = now
            elif not ack:
                print("\n[TIMEOUT]")
            elif ack[0] == 0x15:
                print("\n[NACK]")
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.time() - start
        avg_fps = frame_count / elapsed if elapsed > 0 else 0
        avg_kb = total_bytes / elapsed / 1024 if elapsed > 0 else 0
        print(f"\n{'='*40}")
        print(f"Total: {frame_count} frames in {elapsed:.1f}s")
        print(f"Average: {avg_fps:.2f} FPS, {avg_kb:.0f} KB/s")
        print(f"Per frame: {total_bytes/frame_count/1024:.1f} KB avg" if frame_count else "No frames")
        ser.close()
