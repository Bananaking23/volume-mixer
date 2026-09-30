// Minimal stand-in for the ESP32 Arduino core, just enough surface area to
// syntax-check generated sketches with a desktop C++ compiler. Not used on
// the real device -- the real ESP32 Arduino core provides all of this.
#pragma once
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <string>
#include <algorithm>

#define LOW 0
#define HIGH 1
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2

#define F(x) (x)

inline unsigned long millis() { return 0; }
inline void pinMode(int, int) {}
inline int digitalRead(int) { return HIGH; }
inline void digitalWrite(int, int) {}
inline int analogRead(int) { return 2048; }
inline void analogReadResolution(int) {}

// The real Arduino core defines constrain()/map() as macros (not typed
// functions), so mixed integer types are allowed without an explicit
// cast. Mirror that here for a faithful syntax check.
#define constrain(amt, low, high) ((amt) < (low) ? (low) : ((amt) > (high) ? (high) : (amt)))

inline long map(long x, long in_min, long in_max, long out_min, long out_max) {
  if (in_max == in_min) return out_min;
  return (x - in_min) * (out_max - out_min) / (in_max - in_min) + out_min;
}

class String {
 public:
  String() {}
  String(const char* s) : s_(s) {}
  String(int v) : s_(std::to_string(v)) {}
  String operator+(const String& o) const { return String((s_ + o.s_).c_str()); }
  String& operator+=(const String& o) { s_ += o.s_; return *this; }
  const char* c_str() const { return s_.c_str(); }
 private:
  std::string s_;
};

class SerialClass {
 public:
  void begin(unsigned long) {}
  void print(const char*) {}
  void print(String) {}
  void println(String) {}
  void println(const char*) {}
};
extern SerialClass Serial;
