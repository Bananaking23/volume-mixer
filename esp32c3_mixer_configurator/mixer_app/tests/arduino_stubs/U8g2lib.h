#pragma once

#define U8G2_R0 0
#define U8G2_R1 1
#define U8G2_R2 2
#define U8G2_R3 3
#define U8X8_PIN_NONE (-1)

// Font "symbols" -- the real library exposes these as extern uint8_t
// arrays; a plain int is enough to syntax-check code that only ever
// passes them straight to setFont().
static const int u8g2_font_6x10_tf = 0;
static const int u8g2_font_5x7_tf = 0;
static const int u8g2_font_4x6_tf = 0;

class U8G2_SSD1306_128X64_NONAME_F_HW_I2C {
 public:
  U8G2_SSD1306_128X64_NONAME_F_HW_I2C(int rotation, int reset) {}
  void begin() {}
  void setI2CAddress(int) {}
  void setPowerSave(int) {}
  void clearBuffer() {}
  void sendBuffer() {}
  void setFont(int) {}
  void drawStr(int x, int y, const char* s) {}
  void drawHLine(int x, int y, int len) {}
  void drawFrame(int x, int y, int w, int h) {}
  void drawBox(int x, int y, int w, int h) {}
};
