/*
  ClaudeLamp — диммер настольной лампы для Noryx
  =====================================================
  Плата:    Arduino Nano / Uno / Pro Mini (ATmega328P); Leonardo / Pro Micro тоже подойдут.
  Силовая:  модуль XY-MOS (два MOSFET, вход 3.3–20 В, ШИМ до 20 кГц).

  Подключение:
    D9   ───────── PWM / TRIG модуля
    GND  ───────── GND модуля (сигнальный, рядом с PWM)
    БП 12 В  ───── VIN+ / VIN−  модуля  (на некоторых платах DC+ / DC−)
    Лампа    ───── OUT+ / OUT−  модуля
    Резистор 10 кОм между D9 и GND — чтобы при прошивке / перезагрузке платы лампа не «плавала».

  Связь с ПК — USB, 115200 бод, команды строкой:
    ?       → CLAUDE_LAMP <уровень 0..1000> <вкл 0/1>
    L <n>   → уровень n (0..1000); 0 — выключить (прошлый уровень запоминается)
    T       → включить / выключить
  На L и T плата отвечает той же строкой состояния. Уровень — «на глаз» (с гамма-коррекцией),
  меняется плавно, хранится в EEPROM и восстанавливается после включения.
*/
#include <EEPROM.h>

// ---------- настройки ----------
const uint16_t PWM_HZ   = 4000;  // частота ШИМ 1000–20000 Гц. Лампа пищит — подними; греются MOSFET — опусти
const uint16_t MIN_DUTY = 6;     // нижний предел ШИМ (в шагах из PWM_TOP): на 1% не светится — увеличь, ярко — уменьши
const float    GAMMA    = 2.2;   // кривая яркости: больше — мягче низ шкалы
const uint8_t  LAMP_PIN = 9;     // на Nano / Uno / Leonardo это выход таймера 1 (OC1A)

#if defined(__AVR_ATmega328P__) || defined(__AVR_ATmega328PB__) || defined(__AVR_ATmega168__) || defined(__AVR_ATmega32U4__)
  #define HW_PWM 1
  const uint16_t PWM_TOP = F_CPU / 2UL / PWM_HZ;   // phase-correct: f = F_CPU / (2 · TOP); 16 МГц, 4 кГц → 2000 шагов
  const uint16_t MIN_D   = MIN_DUTY;
#else
  #define HW_PWM 0                                   // другие платы — обычный analogWrite (8 бит)
  const uint16_t PWM_TOP = 255;
  const uint16_t MIN_D   = 1;
#endif

const uint8_t  EE_MAGIC = 0xC7;   // признак, что в EEPROM лежат наши данные
const uint16_t SAVE_AFTER = 2000; // писать в EEPROM через 2 с после последнего изменения (бережём ресурс)

uint16_t level = 600;             // 0..1000 «на глаз» — запоминается, даже когда лампа выключена
bool     lampOn = false;
uint16_t cur16 = 0;               // текущая яркость ×16 (для плавного перехода)
uint32_t tFade = 0, tChange = 0;
bool     dirty = false;
char     buf[24];
uint8_t  blen = 0;

void pwmBegin() {
  pinMode(LAMP_PIN, OUTPUT);
  digitalWrite(LAMP_PIN, LOW);
#if HW_PWM
  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1  = 0;
  ICR1   = PWM_TOP;
  OCR1A  = 0;
  TCCR1A = _BV(COM1A1) | _BV(WGM11);   // ШИМ на OC1A, режим 10: phase-correct, TOP = ICR1
  TCCR1B = _BV(WGM13) | _BV(CS10);     // без делителя
#endif
}

void pwmWrite(uint16_t d) {
#if HW_PWM
  OCR1A = d;
#else
  analogWrite(LAMP_PIN, d);
#endif
}

// яркость «на глаз» (×16, 0..16000) → скважность ШИМ
uint16_t dutyFor(uint16_t x16) {
  if (x16 == 0) return 0;
  if (x16 >= 16000) return PWM_TOP;
  float k = pow(x16 / 16000.0, GAMMA);
  uint32_t d = MIN_D + (uint32_t)(k * (PWM_TOP - MIN_D) + 0.5);
  return d > PWM_TOP ? PWM_TOP : d;
}

void report(bool boot) {
  Serial.print(boot ? F("CLAUDE_LAMP_BOOT ") : F("CLAUDE_LAMP "));
  Serial.print(level);
  Serial.print(' ');
  Serial.println(lampOn ? 1 : 0);
}

void changed() {
  dirty = true;
  tChange = millis();
  report(false);
}

void handle(char *s) {
  while (*s == ' ') s++;
  char c = toupper(*s);
  if (c == '?') {
    report(false);
  } else if (c == 'L') {
    char *p = s + 1;
    while (*p == ' ') p++;
    if (!isdigit(*p)) return;               // мусор вида «L abc» не выключает лампу
    long v = atol(p);
    if (v < 0) v = 0;
    if (v > 1000) v = 1000;
    if (v > 0) { level = v; lampOn = true; }
    else lampOn = false;
    changed();
  } else if (c == 'T') {
    lampOn = !lampOn;
    if (lampOn && level == 0) level = 1000;
    changed();
  }
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      buf[blen] = 0;
      if (blen) handle(buf);
      blen = 0;
    } else if (blen < sizeof(buf) - 1) {
      buf[blen++] = c;
    }
  }
}

void load() {
  if (EEPROM.read(0) != EE_MAGIC) return;   // первый запуск: 60%, выключена
  uint16_t v;
  EEPROM.get(1, v);
  if (v <= 1000) level = v;
  lampOn = EEPROM.read(3) == 1;
}

void save() {
  EEPROM.update(0, EE_MAGIC);
  EEPROM.put(1, level);                     // put пишет только изменившиеся байты
  EEPROM.update(3, lampOn ? 1 : 0);
}

void setup() {
  pwmBegin();
  load();
  Serial.begin(115200);
  report(true);                             // сервер по этой строке понимает, что плата перезагрузилась
}

void loop() {
  readSerial();
  uint32_t now = millis();
  if (now - tFade >= 4) {                   // плавность: каждые 4 мс подходим к цели на 1/8 остатка
    tFade = now;
    uint16_t target = lampOn ? level * 16 : 0;
    if (cur16 != target) {
      int32_t diff = (int32_t)target - cur16;
      int32_t step = diff / 8;
      if (step == 0) step = diff > 0 ? 1 : -1;
      cur16 += step;
      pwmWrite(dutyFor(cur16));
    }
  }
  if (dirty && now - tChange >= SAVE_AFTER) {
    save();
    dirty = false;
  }
}
