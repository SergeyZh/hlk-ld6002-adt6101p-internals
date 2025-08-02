# OTA Jump Code HLK-LD6002
[![en](https://img.shields.io/badge/lang-en-blue.svg)](ota_jump_code-hlk-ld6002.md)
[![ru](https://img.shields.io/badge/lang-ru-green.svg)](ota_jump_code-hlk-ld6002.ru.md)

1. Reads 9 bytes from Flash `00008000h`. It contains the [App Descriptor HLK-LD6002](app-descriptor-hlk-ld6002.md) for the ota\_boot\_32k application.
2. Loads ota\_boot\_32k from Flash `00008010h` into memory at address `20008000h`.
3. Displays a banner of asterisks with the parameters of the loaded code.
4. Runs the code.

