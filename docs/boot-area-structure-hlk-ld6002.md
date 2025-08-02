# Flash Boot Area HLK-LD6002

**Flash Address**: `010000h`

| Index | Name         | Description                                    |
|:-----:|--------------|------------------------------------------------|
|   0   |              |                                                |
|   1   | isUpdateFg   | `FFh` - Run Update mode. `2` - normal mode     |
|   2   |              |                                                |
|   3   | updateAppxFg | Which App to update. `2` or `4`                |
|   4   |              |                                                |
|   5   | loadFg       | Active App. `2` is `18000h` or `4` is `28000h` |
|   6   |              |                                                |
|   7   | factoryFg    | `2` - run Factory Mode. `FFh` - normal mode    |
