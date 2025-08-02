# Bootloader ADT6101P
[![en](https://img.shields.io/badge/lang-en-blue.svg)](bootloader-adt6101p.md)
[![ru](https://img.shields.io/badge/lang-ru-green.svg)](bootloader-adt6101p.ru.md)

Взаимодействие с bootloader возможно только через UART1. Он выводит туда отладочные сообщения и есть возможность закачать через него новую прошивку.

1. Настраиваем UART1 на скорость 115200
2. Устанавливаем GPIO режим 0xB0 (input + pullup) для pin12 и pin19 (boot0 и boot1)
3. Если boot0 = 0 и boot1 = 0, то загружаем прошивку через HyperTerminal (UART1) по адресу 00008000h и запускаем.
4. Если boot0 = 1 и boot1 = 0, то загружаем прошивку из Flash, с адреса 0 (там находится ota_jump_code) в 00008000h и запускаем.
5. Если boot0 = 0 и boot1 = 1, то загружаем прошивку из EEPROM I2C0 (40009000h) и запускаем
6. Если boot0 = 1 и boot1 = 1, то сразу запускаем код из RAM 00008000h. Это Debug Mode, полезен, если вы можете сделать Reset без выключения питания.
