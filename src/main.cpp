#include <Arduino.h>
#include <M5Unified.h>

const int NUM_TABS = 6; // 0..5
String tabContent[NUM_TABS];
int currentTab = 0;

int parseTabToken(String s) {
  s.trim();
  s.toUpperCase();
  if (s.startsWith("TAB")) {
    String rest = s.substring(3);
    if (rest.startsWith("_")) rest = rest.substring(1);
    int n = rest.toInt();
    return n;
  }
  return -1;
}

void drawTabs();
void drawTabContent(int tab);

void setup() {
  M5.begin();
  Serial.begin(115200);
  M5.Display.clear();
  M5.Display.setTextSize(2);
  for (int i = 0; i < NUM_TABS; ++i) tabContent[i] = "(empty)";
  drawTabs();
  drawTabContent(currentTab);
  Serial.println("M5Stack Tab writer ready. Send lines like: TAB_5:Hello world");
}

void loop() {
  M5.update();

  if (M5.BtnA.wasPressed()) {
    currentTab = (currentTab > 0) ? (currentTab - 1) : 0;
    drawTabs();
    drawTabContent(currentTab);
  }
  if (M5.BtnC.wasPressed()) {
    currentTab = (currentTab + 1 < NUM_TABS) ? (currentTab + 1) : (NUM_TABS - 1);
    drawTabs();
    drawTabContent(currentTab);
  }

  auto td = M5.Touch.getDetail();
  if (td.isPressed() && td.y < 40) {
      int w = M5.Display.width();
      int tabw = w / NUM_TABS;
      int t = td.x / tabw;
      t = (t < 0) ? 0 : (t >= NUM_TABS ? NUM_TABS - 1 : t);
      if (t != currentTab) {
        currentTab = t;
        drawTabs();
        drawTabContent(currentTab);
      }
  }

  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() == 0) return;
    int colon = line.indexOf(':');
    if (colon <= 0) {
      Serial.println("ERR: bad format. Use TAB_5:your text");
    } else {
      String left = line.substring(0, colon);
      String msg = line.substring(colon + 1);
      left.trim(); msg.trim();
      int tab = parseTabToken(left);
      if (tab >= 0 && tab < NUM_TABS) {
        tabContent[tab] = msg;
        if (tab == currentTab) drawTabContent(tab);
        Serial.println("OK");
      } else {
        Serial.println("ERR: tab out of range");
      }
    }
  }
}

void drawTabs() {
  M5.Display.fillRect(0, 0, M5.Display.width(), 40, TFT_NAVY);
  int w = M5.Display.width();
  int tabw = w / NUM_TABS;
  for (int i = 0; i < NUM_TABS; ++i) {
    int x = i * tabw;
    uint32_t color = (i == currentTab) ? TFT_ORANGE : TFT_LIGHTGREY;
    M5.Display.fillRect(x + 1, 1, tabw - 2, 38, color);
    M5.Display.setTextColor(TFT_BLACK, color);
    M5.Display.setCursor(x + 6, 10);
    M5.Display.printf("TAB_%d", i);
  }
}

void drawTabContent(int tab) {
  M5.Display.fillRect(0, 40, M5.Display.width(), M5.Display.height() - 40, TFT_WHITE);
  M5.Display.setTextColor(TFT_BLACK, TFT_WHITE);
  M5.Display.setCursor(10, 60);
  M5.Display.setTextSize(2);
  M5.Display.printf("Tab %d", tab);
  M5.Display.setCursor(10, 90);
  M5.Display.setTextSize(2);
  String s = tabContent[tab];
  int lineY = 90;
  int maxWidth = M5.Display.width() - 20;
  String cur = "";
  for (int i = 0; i < s.length(); ++i) {
    cur += s[i];
    if (M5.Display.textWidth(cur) > maxWidth) {
      M5.Display.setCursor(10, lineY);
      M5.Display.print(cur.substring(0, cur.length() - 1));
      lineY += 24;
      cur = String(s[i]);
    }
  }
  if (cur.length()) {
    M5.Display.setCursor(10, lineY);
    M5.Display.print(cur);
  }
}
