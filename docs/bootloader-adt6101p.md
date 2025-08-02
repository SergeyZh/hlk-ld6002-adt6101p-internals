# Bootloader ADT6101P
[![en](https://img.shields.io/badge/lang-en-blue.svg)](bootloader-adt6101p.md)
[![ru](https://img.shields.io/badge/lang-ru-green.svg)](bootloader-adt6101p.ru.md)

Interaction with the bootloader is only possible through UART1. It outputs debug messages to this port, and there is the possibility to upload new firmware through it.

1. Set UART1 to a baud rate of 115200.
2. Set GPIO mode to 0xB0 (input + pull-up) for pin12 and pin19 (boot0 and boot1).
3. If boot0 = 0 and boot1 = 0, upload the firmware via HyperTerminal (UART1) to address 00008000h and run it.
4. If boot0 = 1 and boot1 = 0, upload the firmware from Flash, from address 0 (where ota_jump_code is located), to 00008000h and run it.
5. If boot0 = 0 and boot1 = 1, upload the firmware from EEPROM I2C0 (40009000h) and run it.
6. If boot0 = 1 and boot1 = 1, immediately run the code from RAM 00008000h. This is Debug Mode, useful if you can reset the radar without turning off the power.