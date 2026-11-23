LAMP (ARDUINO + XY-MOS MODULE) — the «Lamp (Arduino)» plugin: a brightness slider in the dock, the scenario step «lamp 60».

Install: Settings → «Plugins» → «Catalog» → «Lamp (Arduino)» → «Install». pyserial installs itself on the first start.

1. Flash ClaudeLamp/ClaudeLamp.ino with the Arduino IDE (board Arduino Nano; for Chinese Nano clones — processor
   «ATmega328P (Old Bootloader)»).
2. Wiring:  D9 → PWM/TRIG of the module,  GND → GND of the module (next to PWM),
            12 V PSU → VIN+/VIN−,  lamp → OUT+/OUT−,  a 10 kΩ resistor between D9 and GND.
3. Plug the board into the PC's USB — the deck finds it by itself (the port is remembered), the slider comes alive.

Slider all the way left — off; the bulb button — on/off at the previous brightness.
The brightness is stored in the board itself and restored after power-on. «Check» in the plugin settings — find the board now.
Flashing: the deck releases the port 10 s after the last slider touch — then press «Upload».
It blinks on the first touch after a pause — the board resets when the port opens: a 10 µF capacitor between RESET and GND
(remove it while flashing). It whines — raise PWM_HZ in the sketch (up to 20000); no light at 1% — increase MIN_DUTY.
Linux: port access — the dialout group (sudo ./system-setup.sh in the deck folder).
