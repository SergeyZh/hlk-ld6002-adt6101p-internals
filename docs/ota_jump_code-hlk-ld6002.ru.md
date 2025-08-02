# OTA Jump Code HLK-LD6002
[![en](https://img.shields.io/badge/lang-en-blue.svg)](ota_jump_code-hlk-ld6002.md)
[![ru](https://img.shields.io/badge/lang-ru-green.svg)](ota_jump_code-hlk-ld6002.ru.md)

1. Читает 9 байт из Flash `00008000h`. Там находится [App Descriptor HLK-LD6002](app-descriptor-hlk-ld6002.md) приложения ota_boot_32k.
2. Загружает ota_boot_32k из Flash `00008010h` в память по адресу `20008000h`.
3. Выводит баннер из звездочек с параметрами кода, который загрузили.
4. Запускает код.
