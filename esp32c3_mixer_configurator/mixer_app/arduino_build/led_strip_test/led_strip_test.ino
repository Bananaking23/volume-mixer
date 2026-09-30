/*
 * Standalone LED strip diagnostic sketch.
 * This tests only the WS2812/NeoPixel strip: no OLED, pots, buttons, or mixer code.
 */
#include <Adafruit_NeoPixel.h>

#define LED_DATA_PIN 5
#define LED_COUNT 2
#define LED_BRIGHTNESS 64

Adafruit_NeoPixel strip(LED_COUNT, LED_DATA_PIN, NEO_GRB + NEO_KHZ800);


void showColor(uint8_t red, uint8_t green, uint8_t blue) {
  for (int i = 0; i < LED_COUNT; i++) strip.setPixelColor(i, strip.Color(red, green, blue));
  strip.show();
  delay(700);
}

void chaseColor(uint8_t red, uint8_t green, uint8_t blue) {
  for (int i = 0; i < LED_COUNT; i++) {
    strip.clear();
    strip.setPixelColor(i, strip.Color(red, green, blue));
    strip.show();
    delay(120);
  }
}

void setup() {
  strip.begin();
  strip.setBrightness(LED_BRIGHTNESS);
  strip.clear();
  strip.show();
}

void loop() {
  showColor(255, 0, 0);
  showColor(0, 255, 0);
  showColor(0, 0, 255);
  showColor(0, 255, 0);
  showColor(255, 0, 0);
  chaseColor(255, 255, 255);
}
