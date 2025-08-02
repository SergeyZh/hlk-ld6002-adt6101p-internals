# OTA Boot 32k HLK-LD6002
[![en](https://img.shields.io/badge/lang-en-blue.svg)](ota_boot_32k-hlk-ld6002.md)
[![ru](https://img.shields.io/badge/lang-ru-green.svg)](ota_boot_32k-hlk-ld6002.ru.md)

It is located in memory at address '20008000h'.

1. Fills 3 areas of RAM with `FF` values:

    1. `0x20029000 - 0x20029085` (size=0x85)
    2. `0x20011000 - 0x20021000` (size=0x10000)
    3. `0x00008000 - 0x00018000` (size=0x10000)
2. Displays a banner of asterisks via UART0.
3. Reads 8 bytes from Flash `00010000h` - this is the [boot-area HLK-LD6002](boot-area-structure-hlk-ld6002.md).
4. If factoryFg = 2, then:

    1. The Factory Mode App is loaded from Flash `00038010h` into memory at `00008000h`.
    2. Writes 1 byte `FF` to Flash at `00010007h`.
    3. Sets the stack to the value from `08000h`.
    4. Runs the code from the address at `08004h`.
5. If isUpdateFg = 2, no application update is needed, so we simply run AppX:

    1. If loadFg == 2, load App1 and run it.
    2. If loadFg == 4, load App2 and run it.
6. If isUpdateFg = 0xFF, start the application update procedure:

    1. If updateAppxFg == 2, update App1.
    2. If updateAppxFg == 4, update App2.

Application update is done through UART0 and the XMODEM protocol (?).

**!!! It seems that if the update cannot be successfully completed, the radar remains in Update mode permanently.**

