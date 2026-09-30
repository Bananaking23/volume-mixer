#pragma once
class Preferences {
 public:
  bool begin(const char*, bool) { return true; }
  void end() {}
  bool isKey(const char*) { return false; }
  int getInt(const char*, int def) { return def; }
  void putInt(const char*, int) {}
};
