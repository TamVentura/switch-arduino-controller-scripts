#include <Arduino.h>
#include <HID.h>

static const uint8_t _desc_data[] PROGMEM = {
  0x05, 0x01,
  0x09, 0x05,
  0xa1, 0x01,

  0x15, 0x00,
  0x25, 0x01,
  0x35, 0x00,
  0x45, 0x01,

  0x75, 0x01,
  0x95, 0x10,
  0x05, 0x09,
  0x19, 0x01,
  0x29, 0x10,

  0x81, 0x02,
  0x05, 0x01,
  0x25, 0x07, 0x46,
  0x3b, 0x01,
  0x75, 0x04,
  0x95, 0x01,
  0x65, 0x14,
  0x09, 0x39,

  0x81, 0x42,
  0x65, 0x00,
  0x95, 0x01,

  0x81, 0x01, 0x26,
  0xff, 0x00, 0x46,
  0xff, 0x00,
  0x09, 0x30,
  0x09, 0x31,
  0x09, 0x32,
  0x09, 0x35,
  0x75, 0x08,
  0x95, 0x04,

  0x81, 0x02, 0x06,
  0x00, 0xff,
  0x09, 0x20,
  0x95, 0x01,

  0x81, 0x02, 0x0a,
  0x21, 0x26,
  0x95, 0x08,
  0x91, 0x02,

  0xc0
};
static HIDSubDescriptor _desc{ _desc_data, sizeof(_desc_data) };

class HIDWithoutReportID : public HID_ {
public:
  int send_report(const void *data, size_t len) {
    // the switch does not use report ids
    return USB_Send(pluggedEndpoint | TRANSFER_RELEASE, data, len);
  }
};

typedef enum {
  BUTTON_Y = 0x01,
  BUTTON_B = 0x02,
  BUTTON_A = 0x04,
  BUTTON_X = 0x08,
  BUTTON_L = 0x10,
  BUTTON_R = 0x20,
  BUTTON_ZL = 0x40,
  BUTTON_ZR = 0x80,
  BUTTON_MINUS = 0x100,
  BUTTON_PLUS = 0x200,
  BUTTON_LCLICK = 0x400,
  BUTTON_RCLICK = 0x800,
  BUTTON_HOME = 0x1000,
  BUTTON_CAPTURE = 0x2000,
} Buttons_t;

#define DPAD_TOP 0x00
#define DPAD_TOP_RIGHT 0x01
#define DPAD_RIGHT 0x02
#define DPAD_BOTTOM_RIGHT 0x03
#define DPAD_BOTTOM 0x04
#define DPAD_BOTTOM_LEFT 0x05
#define DPAD_LEFT 0x06
#define DPAD_TOP_LEFT 0x07
#define DPAD_CENTER 0x08

#define STICK_MIN 0
#define STICK_CENTER 128
#define STICK_MAX 255

typedef struct
{
  uint16_t button;
  uint8_t dpad;
  uint8_t lx;
  uint8_t ly;
  uint8_t rx;
  uint8_t ry;
  uint8_t _unused;
} Report_t;

const int PIN_BUZZER = 9;

void update_report(uint16_t *button, bool mode, Buttons_t Button) {
  if (mode) {
    *button |= Button;
  } else {
    *button &= ~Button;
  }
}

void set_value_mode(uint8_t *button, bool mode, uint8_t true_value, uint8_t false_value) {
  if (mode) {
    *button = true_value;
  } else {
    *button = false_value;
  }
}

void make_report(Report_t *report, bool mode, uint8_t c, uint8_t x, uint8_t y) {
  switch (c) {
    case 'A':
      update_report(&report->button, mode, BUTTON_A);
      break;

    case 'B':
      update_report(&report->button, mode, BUTTON_B);
      break;

    case 'X':
      update_report(&report->button, mode, BUTTON_X);
      break;

    case 'Y':
      update_report(&report->button, mode, BUTTON_Y);
      break;

    case 'H':
      update_report(&report->button, mode, BUTTON_HOME);
      break;

    case '+':
      update_report(&report->button, mode, BUTTON_PLUS);
      break;

    case '-':
      update_report(&report->button, mode, BUTTON_MINUS);
      break;

    case 'L':
      update_report(&report->button, mode, BUTTON_L);
      break;

    case 'R':
      update_report(&report->button, mode, BUTTON_R);
      break;
    case 'l':
      update_report(&report->button, mode, BUTTON_ZL);
      break;

    case 'r':
      update_report(&report->button, mode, BUTTON_ZR);
      break;

    case 'w':
      set_value_mode(&report->lx, mode, STICK_CENTER, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_MIN, STICK_CENTER);
      break;

    case 'a':
      set_value_mode(&report->lx, mode, STICK_MIN, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_CENTER, STICK_CENTER);
      break;

    case 's':
      set_value_mode(&report->lx, mode, STICK_CENTER, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_MAX, STICK_CENTER);
      break;
    case 'd':
      set_value_mode(&report->lx, mode, STICK_MAX, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_CENTER, STICK_CENTER);
      break;

    case 'q':
      set_value_mode(&report->lx, mode, STICK_MIN, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_MIN, STICK_CENTER);
      break;

    case 'e':
      set_value_mode(&report->lx, mode, STICK_MAX, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_MIN, STICK_CENTER);
      break;

    case 'z':
      set_value_mode(&report->lx, mode, STICK_MIN, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_MAX, STICK_CENTER);
      break;

    case 'c':
      set_value_mode(&report->lx, mode, STICK_MAX, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_MAX, STICK_CENTER);
      break;
    case 'u':
      set_value_mode(&report->rx, mode, STICK_CENTER, STICK_CENTER);
      set_value_mode(&report->ry, mode, STICK_MIN, STICK_CENTER);
      break;

    case 'h':
      set_value_mode(&report->rx, mode, STICK_MIN, STICK_CENTER);
      set_value_mode(&report->ry, mode, STICK_CENTER, STICK_CENTER);
      break;

    case 'j':
      set_value_mode(&report->rx, mode, STICK_CENTER, STICK_CENTER);
      set_value_mode(&report->ry, mode, STICK_MAX, STICK_CENTER);
      break;

    case 'k':
      set_value_mode(&report->rx, mode, STICK_MAX, STICK_CENTER);
      set_value_mode(&report->ry, mode, STICK_CENTER, STICK_CENTER);
      break;

    case 'y':
      set_value_mode(&report->rx, mode, STICK_MIN, STICK_CENTER);
      set_value_mode(&report->ry, mode, STICK_MIN, STICK_CENTER);
      break;

    case 'i':
      set_value_mode(&report->rx, mode, STICK_MAX, STICK_CENTER);
      set_value_mode(&report->ry, mode, STICK_MIN, STICK_CENTER);
      break;

    case 'n':
      set_value_mode(&report->rx, mode, STICK_MIN, STICK_CENTER);
      set_value_mode(&report->ry, mode, STICK_MAX, STICK_CENTER);
      break;

    case 'm':
      set_value_mode(&report->rx, mode, STICK_MAX, STICK_CENTER);
      set_value_mode(&report->ry, mode, STICK_MAX, STICK_CENTER);
      break;

    case '~':
      update_report(&report->button, mode, BUTTON_L | BUTTON_R);
      break;

    case '@':
      update_report(&report->button, mode, BUTTON_A);
      set_value_mode(&report->lx, mode, STICK_CENTER, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_MAX, STICK_CENTER);
      break;

    case '#':
      set_value_mode(&report->lx, mode, STICK_MIN, STICK_CENTER);
      set_value_mode(&report->ly, mode, STICK_CENTER, STICK_CENTER);
      set_value_mode(&report->rx, mode, STICK_MIN, STICK_CENTER);
      set_value_mode(&report->ry, mode, STICK_CENTER, STICK_CENTER);
      break;

    case '$':
      set_value_mode(&report->lx, mode, (STICK_MAX - STICK_MIN) * 3 / 5, STICK_CENTER);
      set_value_mode(&report->ly, mode, (STICK_MAX - STICK_MIN) * 3 / 5, STICK_CENTER);
      break;

    case '{':
      update_report(&report->button, mode, BUTTON_LCLICK);
      break;
    case '}':
      update_report(&report->button, mode, BUTTON_RCLICK);
      break;

    case '<':
      report->lx = x;
      report->ly = y;
      break;
    case '>':
      report->rx = x;
      report->ry = y;
      break;
    case 'C':
      update_report(&report->button, mode, BUTTON_CAPTURE);
      break;
    case '1':
      set_value_mode(&report->dpad, mode, DPAD_RIGHT, DPAD_CENTER);
      break;
    case '2':
      set_value_mode(&report->dpad, mode, DPAD_BOTTOM, DPAD_CENTER);
      break;
    case '3':
      set_value_mode(&report->dpad, mode, DPAD_LEFT, DPAD_CENTER);
      break;
    case '4':
      set_value_mode(&report->dpad, mode, DPAD_TOP, DPAD_CENTER);
      break;
  }
}

uint8_t serial_read_blocking() {
  while (!Serial1.available())
    ;
  return Serial1.read();
}

int main() {
  init();

  USBDevice.attach();

  HIDWithoutReportID hid;
  hid.AppendDescriptor(&_desc);

  Serial1.begin(115200);
  Serial1.println("hello hello world");

  pinMode(PIN_BUZZER, OUTPUT);

  Report_t report;
  memset(&report, 0, sizeof(Report_t));

  bool verbose = false;
  uint8_t c = '.';
  uint8_t x = 0;
  uint8_t y = 0;
  while (true) {
    if (Serial1.available()) {
      bool mode = false;
      char read = Serial1.read();
      Serial1.println("read" + read);
      if (read == 'V') {
        verbose = true;
        Serial1.println("enabling verbose mode");
      } else if (read == 'v') {
        verbose = false;
        Serial1.println("disabling verbose mode");
      } else if (read == '<' || read == '>') {
        c = read;
        x = serial_read_blocking();
        y = serial_read_blocking();
        mode = true;
      } else {
        c = read;
        mode = (serial_read_blocking() == '1');
        if (verbose) {
          Serial1.println("recv: ");
          Serial1.println(c);
          Serial1.print(mode);
          Serial1.print('\n');
        }
      }

      digitalWrite(PIN_BUZZER, c == '!' ? HIGH : LOW);
      make_report(&report, mode, c, x, y);
      hid.send_report(&report, sizeof(Report_t));
    }
  }
}
