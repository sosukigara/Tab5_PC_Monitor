#include <Arduino.h>
#include <M5Unified.h>

// Allocate a buffer in SRAM or PSRAM for the incoming JPEG image
// 256KB is plenty for a compressed 1280x720 JPEG frame
static const uint32_t BUFFER_SIZE = 256 * 1024;
uint8_t* jpegBuffer = nullptr;

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);

  // Set rotation if needed (Default should be correct for landscape)
  M5.Display.setRotation(0);
  M5.Display.clear(TFT_BLACK);

  // Use PSRAM if available, otherwise fallback to SRAM
  if (psramInit()) {
    jpegBuffer = (uint8_t*)ps_malloc(BUFFER_SIZE);
  }
  if (!jpegBuffer) {
    jpegBuffer = (uint8_t*)malloc(BUFFER_SIZE);
  }

  // Native USB-CDC CDC-ACM ignores baudrate, but set high for compatibility
  Serial.begin(115200);
  Serial.setTimeout(1000); // 1 second read timeout

  M5.Display.setTextSize(2);
  M5.Display.setTextColor(TFT_GREEN);
  M5.Display.setCursor(10, 10);
  M5.Display.println("M5Stack Linux Monitor Ready");
  M5.Display.printf("Width: %d, Height: %d\n", M5.Display.width(), M5.Display.height());
  M5.Display.println("Waiting for host stream...");
}

void loop() {
  // 1. Wait for sync header (0xAA, 0xBB, 0xCC, 0xDD)
  static const uint8_t SYNC_HEADER[] = {0xAA, 0xBB, 0xCC, 0xDD};
  uint8_t headerIndex = 0;

  while (headerIndex < 4) {
    if (Serial.available()) {
      uint8_t c = Serial.read();
      if (c == SYNC_HEADER[headerIndex]) {
        headerIndex++;
      } else {
        // Reset sync state if mismatch, check if it matches first byte of header
        headerIndex = (c == SYNC_HEADER[0]) ? 1 : 0;
      }
    }
  }

  // 2. Read 4-byte payload size (Big-Endian)
  uint8_t sizeBytes[4];
  if (Serial.readBytes(sizeBytes, 4) != 4) {
    return; // Timeout or read failure
  }

  uint32_t payloadSize = ((uint32_t)sizeBytes[0] << 24) |
                         ((uint32_t)sizeBytes[1] << 16) |
                         ((uint32_t)sizeBytes[2] << 8)  |
                         (uint32_t)sizeBytes[3];

  // Validate payload size
  if (payloadSize == 0 || payloadSize > BUFFER_SIZE) {
    // Write NACK (0x15) to notify the host of an error
    Serial.write(0x15);
    return;
  }

  // 3. Read JPEG payload bytes
  uint32_t bytesRead = 0;
  while (bytesRead < payloadSize) {
    size_t chunk = Serial.readBytes(jpegBuffer + bytesRead, payloadSize - bytesRead);
    if (chunk == 0) {
      // Timeout occurred
      Serial.write(0x15); // NACK
      return;
    }
    bytesRead += chunk;
  }

  // 4. Draw JPEG to screen
  // ESP32-P4 and M5GFX will hardware/software decode JPEG very fast
  M5.Display.startWrite();
  M5.Display.drawJpg(jpegBuffer, payloadSize, 0, 0);
  M5.Display.endWrite();

  // 5. Send ACK (0x06) to host to signal readiness for the next frame
  Serial.write(0x06);
}
