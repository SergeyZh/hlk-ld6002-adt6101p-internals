# Undocumented commands for HLK-LD6002

## Undocumented types of TinyFrames

| Type   | Description                                                                                                                                                               |
|--------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `0x3000` | Trigger Radar to upload new firmware. **You can’t make the Radar work again** unless you upload the correct firmware image via [xmodem_send.py](../utils/xmodem_send.py)! |
| `0x4000` | Run Factory Default application                                                                                                                                           |
| `0xFFFF` | Request a version of firmware. Return packet 0xFFFF has 4 bytes of data: [type][version.major][version.minor][version.patch]                                              |
