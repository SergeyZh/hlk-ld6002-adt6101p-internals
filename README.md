# HLK-LD6002-ADT6101P-internals
Internals & reverse engineering of the HLK‑LD6002 radar (ADT6101P)

[![en](https://img.shields.io/badge/lang-en-blue.svg)](README.md)
[![ru](https://img.shields.io/badge/lang-ru-green.svg)](README.ru.md)


**Warning:** I am not an employee or affiliate of HiLink or Andar. All information in this project is unofficial and this is not official documentation.

HLK‑LD6002 is a radar sensor from the Chinese company [HiLink](https://www.hlktech.com/en/Goods-227.html), operating in the 60 GHz range.  
It has the following capabilities:
1. Measures heart rate and breathing from a distance. It also reports the distance. HLK-6002
2. Detects if a person has fallen. HLK-6002C
3. Detects up to 4 objects in front and reports their coordinates. HLK-6002B
4. Measures infant heart rate and breathing from a distance. HLK-6002H

All this is possible within 1.5–3 meters from a person.

<img src="hlk-ld6002-x3.png" width="70%" alt="HLK-LD6002 x 3" />

I really liked the idea of an affordable sensor with such broad functionality. I also wanted to better understand how radar technology works at such high frequencies.

## Terminology
- HLK-LD6002 is ready to order product – a sensor manufactured by HiLink based on the ADT6101P chip.
- ADT6101P is a chip from [Andar](http://www.andartechs.com/bk_24853220.html###), designed for developing 60 GHz radar solutions.

The research process involves exploring the internal structure of the ADT6101P and how it is integrated into the HLK-6002 device.

## Project Goals
- [x] [Read firmware from the HLK-LD6002 sensor's flash chip](docs/how-to-read-flash-hlk-ld6002.md).
- [x] [Flash firmware analysis](docs/flash-map-hlk-ld6002.md).
- [x] Connect a JTAG adapter for debugging.
- [x] Read the bootloader from the ADT6101P to understand how firmware is loading.
- [x] [Bootloader ADT6101P analysis](docs/bootloader-adt6101p.md)
- [x] [ota_jump_code analysis](docs/ota_jump_code-hlk-ld6002.md)
- [x] [ota_boot_32k analysis](docs/ota_boot_32k-hlk-ld6002.md)
- [x] [Update the firmware](docs/firmware-update.md) in a simple way, without using the Windows program.
- [ ] Retrieve "raw" radar data from the chip to a host computer.
- [ ] Switch between different firmware versions on the sensor to change functionality on the fly.
- [ ] Develop custom firmware to expand the sensor’s capabilities.

## What inspired me to start this project
1. [Seeed Studio](https://wiki.seeedstudio.com/) and their [MR60BHA2](https://wiki.seeedstudio.com/getting_started_with_mr60bha2_mmwave_kit/) sensor, which can easily connect to Home Assistant. My journey with the HLK-6002 began after buying their sensor. Their website has a lot of information and is a good place to start.
2. The [RTL-SDR](https://www.rtl-sdr.com/) project. Researchers turned a cheap TV tuner into a universal SDR receiver!
3. The nRF24L01 – a common and affordable wireless chip that became the de facto standard for exploring the 2.4 GHz band.
