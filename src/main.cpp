#include <Arduino.h>
#include <M5Unified.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>

// Allocate two buffers in PSRAM for Double Buffering
// 256KB is plenty for a compressed 1280x720 JPEG frame
static const uint32_t BUFFER_SIZE = 256 * 1024;
uint8_t* bufferA = nullptr;
uint8_t* bufferB = nullptr;

// Pointers for double buffering logic
uint8_t* writeBuffer = nullptr;
uint8_t* drawBuffer = nullptr;
uint32_t drawPayloadSize = 0;

// Synchronization primitives
SemaphoreHandle_t frameReadySem;
SemaphoreHandle_t drawDoneSem;

// Task handle
TaskHandle_t receiveTaskHandle;

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

// Core 0 Task: Handles USB CDC Serial Receiving
void receiveTask(void *pvParameters) {
  static const uint8_t SYNC_HEADER[] = {0xAA, 0xBB, 0xCC, 0xDD};

  while (1) {
    // Determine which buffer to write to for this frame.
    // It should be the opposite of whatever the current drawBuffer is.
    uint8_t* targetWriteBuffer = (drawBuffer == bufferA) ? bufferB : bufferA;

    // 2. Wait for sync header
    uint8_t headerIndex = 0;
    // Set to 500ms in the past so the very first loop iteration instantly broadcasts D:READY
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
        if (millis() - lastReadyTime > 500) {
          Serial.println("D:READY");
          lastReadyTime = millis();
        }
        delay(1);
      }
    }

    // 3. Read 4-byte payload size
    uint8_t sizeBytes[4];
    if (Serial.readBytes(sizeBytes, 4) != 4) {
      continue;
    }

    uint32_t payloadSize = ((uint32_t)sizeBytes[0] << 24) |
                           ((uint32_t)sizeBytes[1] << 16) |
                           ((uint32_t)sizeBytes[2] << 8)  |
                           (uint32_t)sizeBytes[3];

    // Validate size
    if (payloadSize == 0 || payloadSize > BUFFER_SIZE) {
      Serial.write(0x15); // NACK
      Serial.write(0xE2);
      Serial.flush();
      while (Serial.available() > 0) { Serial.read(); }
      continue;
    }

    // 4. Send D:SYNC
    Serial.println("D:SYNC");
    Serial.flush();

    // 5. Read payload into our targeted write buffer
    uint32_t bytesRead = 0;
    bool timeout = false;
    while (bytesRead < payloadSize) {
      size_t chunk = Serial.readBytes(targetWriteBuffer + bytesRead, payloadSize - bytesRead);
      if (chunk == 0) {
        Serial.write(0x15); // NACK
        Serial.write(0xE3);
        Serial.flush();
        while (Serial.available() > 0) { Serial.read(); }
        timeout = true;
        break;
      }
      bytesRead += chunk;
    }

    if (timeout) {
      continue;
    }

    // Clear buffer remainder
    while (Serial.available() > 0) {
      Serial.read();
    }

    // --- TRUE DOUBLE BUFFERING SYNCHRONIZATION ---
    // 1. Wait until the drawing task (Core 1) is absolutely finished with its current `drawBuffer`.
    // We do this wait here, AFTER we've done the slow work of receiving the USB payload.
    // This allows USB Receive and Display Drawing to happen at the exact same time.
    xSemaphoreTake(drawDoneSem, portMAX_DELAY);

    // 2. The drawing task is done. The buffer we just wrote to is now fully ready to be drawn.
    drawPayloadSize = payloadSize;
    drawBuffer = targetWriteBuffer;

    // 3. Trigger Core 1 to wake up and start drawing the new `drawBuffer`
    xSemaphoreGive(frameReadySem);

    // 4. Send ACK to host immediately so it can start sending the NEXT frame
    // Core 1 is already busy drawing, and Core 0 can now loop around and start receiving into the other buffer.
    Serial.write(0x06);
    Serial.flush();
  }
}

void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);

  M5.Display.setRotation(1);
  M5.Display.clear(TFT_BLACK);

  // Allocate Double Buffers
  bufferA = allocateBuffer();
  bufferB = allocateBuffer();

  if (!bufferA || !bufferB) {
    M5.Display.setTextSize(2);
    M5.Display.setTextColor(TFT_RED);
    M5.Display.println("Fatal: Dual Buffer allocation failed!");
    while(1) { delay(100); }
  }

  // Initialize pointers
  drawBuffer = bufferA;
  writeBuffer = bufferB;

  // Create Semaphores
  // frameReadySem: Binary semaphore, triggers when a frame is fully downloaded
  frameReadySem = xSemaphoreCreateBinary();

  // drawDoneSem: Binary semaphore, starts available so the receiveTask can begin downloading immediately
  drawDoneSem = xSemaphoreCreateBinary();
  xSemaphoreGive(drawDoneSem);

  Serial.setRxBufferSize(65536);
  Serial.begin(115200);
  Serial.setTimeout(3000);

  // Create Receive Task pinned to Core 0
  // ESP32 Arduino's main loop() runs on Core 1 by default.
  // We put I/O on Core 0 to prevent blocking the drawing engine.
  xTaskCreatePinnedToCore(
    receiveTask,
    "ReceiveTask",
    8192,
    NULL,
    1,
    &receiveTaskHandle,
    0 // Core 0
  );
}

void loop() {
  // Core 1 Task: Handles display drawing

  // Wait until ReceiveTask signals that a frame is ready
  if (xSemaphoreTake(frameReadySem, portMAX_DELAY) == pdTRUE) {
    // Draw the JPEG from the drawBuffer
    M5.Display.startWrite();
    M5.Display.drawJpg(drawBuffer, drawPayloadSize, 0, 0);
    M5.Display.endWrite();

    // Wait for DMA flush to screen
    M5.Display.waitDisplay();

    // Signal ReceiveTask that we are done with this buffer,
    // so it can be overwritten with the NEXT incoming frame.
    xSemaphoreGive(drawDoneSem);
  }
}
