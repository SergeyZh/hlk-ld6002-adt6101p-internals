# Map of Flash HLK-LD6002

| Name            | Flash Addr  |  Size   | RAM Addr    | Description                                                                    |
|-----------------|-------------|:-------:|-------------|--------------------------------------------------------------------------------|
| ota_jump_code   | `00000000h` | `0C10h` | `00008000h` | Loaded by ROM bootloader                                                       |
| ota_boot_32k    | `00008000h` | `1FB8h` | `20008000h` | Loaded by jump_code, check Boot Area, load AppX/Factory, update AppX           |
| Boot Area       | `00010000h` |    8    |             | Boot configuration                                                             |
| Radar Config    | `00014000h` | `108h`  |             | There are settings like: mounting type, interference and detections areas, etc |
| App1            | `00017FF0h` |         | `00008000h` | Radar App1                                                                     |
| App2            | `00027FF0h` |         | `00008000h` | Radar App2                                                                     |
| Factory Default | `00038000h` |         | `00008000h` |                                                                                |
|                 | `00048000h` |  `3Ch`  |             |                                                                                |


