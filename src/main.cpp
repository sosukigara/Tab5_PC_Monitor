#include <Arduino.h>
#include <M5Unified.h>

// Allocate a buffer in SRAM or PSRAM for the incoming JPEG image
// 256KB is plenty for a compressed 1280x720 JPEG frame
static const uint32_t BUFFER_SIZE = 256 * 1024;
uint8_t* jpegBuffer = nullptr;

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);

  // Set rotation to 1 (Landscape) so the 1280x720 frame fits correctly
  M5.Display.setRotation(1);
  M5.Display.clear(TFT_BLACK);

  // 1st priority: Try to allocate in PSRAM (SPIRAM) which has plenty of space
  jpegBuffer = (uint8_t*)heap_caps_malloc(BUFFER_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!jpegBuffer) {
    // 2nd priority: Fallback to internal SRAM if PSRAM is unavailable
    jpegBuffer = (uint8_t*)heap_caps_malloc(BUFFER_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  }
  if (!jpegBuffer) {
    // 3rd priority: Allocate whatever is available (minimum 150KB)
    jpegBuffer = (uint8_t*)malloc(150 * 1024);
  }

  if (!jpegBuffer) {
    M5.Display.setTextSize(2);
    M5.Display.setTextColor(TFT_RED);
    M5.Display.println("Fatal: Buffer allocation failed!");
    while(1) { delay(100); }
  }

  // Expand the serial RX buffer to prevent USB CDC packet dropping during high-speed image transfer
  Serial.setRxBufferSize(65536);
  // Native USB-CDC CDC-ACM ignores baudrate, but set high for compatibility
  Serial.begin(115200);
  Serial.setTimeout(3000); // Increased to 3 seconds to avoid timeout during chunked receiving
}

void loop() {
  // 1. Wait for sync header (0xAA, 0xBB, 0xCC, 0xDD)
  static const uint8_t SYNC_HEADER[] = {0xAA, 0xBB, 0xCC, 0xDD};
  uint8_t headerIndex = 0;
  uint32_t lastReadyTime = 0;

  while (headerIndex < 4) {
    if (Serial.available()) {
      uint8_t c = Serial.read();
      if (c == SYNC_HEADER[headerIndex]) {
        headerIndex++;
      } else {
        headerIndex = (c == SYNC_HEADER[0]) ? 1 : 0;
      }
    } else {
      // Broadcast READY signal periodically (every 500ms) to prevent deadlock
      // if the host clears its buffer right after connecting.
      if (millis() - lastReadyTime > 500) {
        Serial.println("D:READY");
        lastReadyTime = millis();
      }
      // Yield to prevent Watchdog Timeout while waiting for host
      delay(1);
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

  // Validate payload size and buffer
  if (!jpegBuffer) {
    Serial.write(0x15); // NACK
    Serial.write(0xE1); // Error code: Buffer is NULL
    Serial.flush();
    while (Serial.available() > 0) { Serial.read(); }
    return;
  }
  if (payloadSize == 0 || payloadSize > BUFFER_SIZE) {
    Serial.write(0x15); // NACK
    Serial.write(0xE2); // Error code: Payload size invalid or too large
    Serial.flush();
    while (Serial.available() > 0) { Serial.read(); }
    return;
  }

  // Two-stage handshake: Tell the host we successfully processed the header/size
  // and are now ready to stream the actual image data payload.
  Serial.println("D:SYNC");
  Serial.flush();

  // 3. Read JPEG payload bytes
  uint32_t bytesRead = 0;
  while (bytesRead < payloadSize) {
    size_t chunk = Serial.readBytes(jpegBuffer + bytesRead, payloadSize - bytesRead);
    if (chunk == 0) {
      // Timeout occurred
      Serial.write(0x15); // NACK
      Serial.write(0xE3); // Error code: Serial read timeout
      Serial.flush();
      while (Serial.available() > 0) { Serial.read(); }
      return;
    }
    bytesRead += chunk;
  }

  // 4. Draw JPEG to screen
  M5.Display.startWrite();
  M5.Display.drawJpg(jpegBuffer, payloadSize, 0, 0);
  M5.Display.endWrite();

  // Wait for DMA / display update to complete before allowing new data
  M5.Display.waitDisplay();

  // 5. Send ACK (0x06) to host to signal readiness for the next frame
  Serial.write(0x06);
  Serial.flush();

  // Clear serial buffer to ensure we start reading next frame from sync header cleanly
  while (Serial.available() > 0) {
    Serial.read();
  }
}
