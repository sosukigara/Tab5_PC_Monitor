#include <Arduino.h>
#include <M5Unified.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

// Allocate a single buffer in PSRAM
static const uint32_t BUFFER_SIZE = 256 * 1024;
uint8_t* bufferA = nullptr;

// Helper to allocate memory
uint8_t* allocateBuffer() {
  uint8_t* buf = (uint8_t*)heap_caps_malloc(BUFFER_SIZE, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (!buf) {
    buf = (uint8_t*)heap_caps_malloc(BUFFER_SIZE, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
  }
  if (!buf) {
    buf = (uint8_t*)malloc(150 * 1024);
  }
  return buf;
}

// Parse JPEG SOF marker to get image dimensions
bool getJpegDimensions(const uint8_t* data, uint32_t len, uint16_t &w, uint16_t &h) {
  for (uint32_t i = 0; i + 8 < len; i++) {
    if (data[i] == 0xFF && (data[i+1] == 0xC0 || data[i+1] == 0xC2)) {
      h = (data[i+5] << 8) | data[i+6];
      w = (data[i+7] << 8) | data[i+8];
      return true;
    }
  }
  return false;
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);

  M5.Display.setRotation(1);
  M5.Display.clear(TFT_BLACK);

  // Allocate Single Buffer
  bufferA = allocateBuffer();

  if (!bufferA) {
    M5.Display.setTextSize(2);
    M5.Display.setTextColor(TFT_RED);
    M5.Display.println("Fatal: Buffer allocation failed!");
    while(1) { delay(100); }
  }

  Serial.setRxBufferSize(131072);
  Serial.begin(3000000); 
  Serial.setTimeout(100);
}

void loop() {
  static const uint8_t SYNC_HEADER[] = {0xAA, 0xBB, 0xCC, 0xDD};
  
  // 1. Wait for sync header
  uint8_t headerIndex = 0;
  uint32_t lastReadyTime = millis() - 500;

  while (headerIndex < 4) {
    if (Serial.available()) {
      uint8_t c = Serial.read();
      if (c == SYNC_HEADER[headerIndex]) {
        headerIndex++;
      } else {
        headerIndex = (c == SYNC_HEADER[0]) ? 1 : 0;
      }
    } else {
      if (millis() - lastReadyTime > 200) {
        Serial.println("D:READY");
        lastReadyTime = millis();
      }
      delay(1);
    }
  }

  // 2. Read 4-byte payload size
  uint8_t sizeBytes[4];
  if (Serial.readBytes(sizeBytes, 4) != 4) {
    return;
  }

  uint32_t payloadSize = ((uint32_t)sizeBytes[0] << 24) |
                         ((uint32_t)sizeBytes[1] << 16) |
                         ((uint32_t)sizeBytes[2] << 8)  |
                         (uint32_t)sizeBytes[3];

  // Validate size
  if (payloadSize == 0 || payloadSize > BUFFER_SIZE) {
    Serial.println("D:NACK:E2");
    Serial.flush();
    while (Serial.available() > 0) { Serial.read(); delay(1); }
    return;
  }

  // 3. Send D:SYNC
  Serial.println("D:SYNC");
  Serial.flush();

  // 4. Read payload into buffer
  uint32_t bytesRead = 0;
  bool timeout = false;
  uint32_t lastReadTime = millis();
  while (bytesRead < payloadSize) {
    if (Serial.available() > 0) {
      uint32_t available = Serial.available();
      uint32_t toRead = (available < (payloadSize - bytesRead)) ? available : (payloadSize - bytesRead);
      size_t chunk = Serial.readBytes(bufferA + bytesRead, toRead);
      bytesRead += chunk;
      lastReadTime = millis();
    } else {
      if (millis() - lastReadTime > 1000) {
        Serial.println("D:NACK:E3");
        Serial.flush();
        while (Serial.available() > 0) { Serial.read(); delay(1); }
        timeout = true;
        break;
      }
      delay(1);
    }
  }

  if (timeout) {
    return;
  }

  // 5. Auto-scale JPEG to fill display
  uint16_t jpgW = 0, jpgH = 0;
  float scaleX = 1.0f, scaleY = 1.0f;
  
  if (getJpegDimensions(bufferA, payloadSize, jpgW, jpgH) && jpgW > 0 && jpgH > 0) {
    scaleX = (float)M5.Display.width()  / (float)jpgW;
    scaleY = (float)M5.Display.height() / (float)jpgH;
  }

  M5.Display.startWrite();
  M5.Display.drawJpg(bufferA, payloadSize, 0, 0,
                     M5.Display.width(), M5.Display.height(),
                     0, 0, scaleX, scaleY);
  M5.Display.endWrite();
  M5.Display.waitDisplay();

  // 6. Send ACK only AFTER drawing is completely done
  Serial.write(0x06);
  Serial.flush();

  // Clear any stray bytes in the serial buffer
  while (Serial.available() > 0) {
    Serial.read();
    delay(1);
  }
}
