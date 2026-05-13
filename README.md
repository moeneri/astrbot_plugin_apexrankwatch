# astrbot_plugin_apexrankwatch

Apex Rank Watch 是一个面向 QQ 群使用的 AstrBot 插件，用来查询 Apex Legends 玩家段位、地图轮换、当前赛季结束时间和猎杀线，并支持群内持续监控排位分变化。

## 功能

- 查询玩家段位、RP、等级、在线状态和当前英雄，并输出图片卡片
- 将玩家加入群监控，排位分变化后主动推送图片通知
- 查询排位地图轮换、API 学习确认的排位未来 24 小时地图和三人赛匹配地图轮换
- 查询当前赛季结束时间
- 查询本赛季猎杀线和大师数量（包含猎杀）
- 支持玩家名、`uid:`、`uuid:` 查询
- 支持群白名单、用户黑名单、玩家查询黑名单

## 常用命令效果

下图包含 `/新赛季`、`/猎杀`、`/地图`、`/apexrank moeneri` 和 `/apexrankwatch moeneri pc` 的常用输出效果。

![常用命令效果总览](https://cdn.jsdelivr.net/gh/moeneri/astrbot_plugin_apexrankwatch@main/assets/readme/command_effects_overview.png)

## 命令

| 命令 | 说明 |
| --- | --- |
| `/apexhelp` | 查看帮助 |
| `/apextest` | 测试插件和主动消息能力 |
| `/apexrank <玩家名\|uid:...> [平台]` | 查询玩家段位信息 |
| `/apexrankwatch <玩家名\|uid:...> [平台]` | 将玩家加入当前群监控 |
| `/apexranklist` | 查看当前群监控列表 |
| `/apexrankremove <玩家名\|uid:...> [平台]` | 从当前群移除玩家监控 |
| `/map` | 查询排位地图轮换 |
| `/全天地图` | 查询 API 学习确认的排位未来 24 小时地图 |
| `/匹配地图` | 查询三人赛匹配地图轮换 |
| `/apexseason` | 查询当前赛季信息 |
| `/apexpredator [平台]` | 查询猎杀线和大师数量 |
| `/apexblacklist <add\|remove\|list\|clear> <玩家ID>` | 管理玩家黑名单 |
| `/赛季关闭` | 关闭当前群的“赛季”关键词自动回复 |
| `/赛季开启` | 开启当前群的“赛季”关键词自动回复 |

## 常用别名

- 查询玩家：`/apex查询`、`/视奸`
- 添加监控：`/apex监控`、`/持续视奸`
- 移除监控：`/apex移除`、`/取消持续视奸`
- 赛季查询：`/apex赛季`、`/新赛季`
- 排位地图：`/地图`、`/排位地图`、`/apexmap`、`/apexrankmap`
- 全天地图：`/全天排位地图`、`/今日地图`、`/今日排位地图`、`/dailymap`
- 猎杀线：`/apex猎杀`、`/猎杀`
- 黑名单：`/apex黑名单`、`/不准视奸`、`/apexban`
- 帮助：`/apexrankhelp`、`/apex帮助`

## 使用示例

```text
/apexrank moeneri pc
/apexrank uid:0000000000000 pc
/apexrankwatch moeneri pc
/apexrankremove moeneri pc
/map
/全天地图
/匹配地图
/apexpredator pc
```

支持的平台参数：`pc`、`ps4`、`x1`、`switch`。不填写平台时，插件会按 PC、PS、Xbox、Switch 的顺序尝试查询。

## 安装

1. 在 AstrBot 插件管理页面上传插件压缩包，或将插件目录放入 AstrBot 的插件目录。
2. 在插件配置里填写 `api_key`。
3. 如果你的 Apex Legends API Key 还没有完成验证，请到 `https://portal.apexlegendsapi.com/discord-auth` 绑定 Discord。
4. 重启插件后，在群里发送 `/apexhelp` 查看命令。

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `api_key` | 空 | Apex Legends API Key |
| `check_interval` | `2` | 排位分监控轮询间隔，单位分钟 |
| `timeout_ms` | `10000` | API 请求超时时间，单位毫秒 |
| `max_retries` | `3` | API 请求失败重试次数 |
| `min_valid_score` | `1` | 最小有效分数，低于该值会被视为异常数据 |
| `blacklist` | 空 | 禁止查询和监控的玩家 ID 列表 |
| `query_blocklist` | 空 | 仅禁止查询的玩家 ID 列表 |
| `user_blacklist` | 空 | 禁止使用插件的 QQ 号列表 |
| `owner_qq` | 空 | 插件管理员 QQ 号列表 |
| `whitelist_enabled` | `false` | 是否启用群白名单模式 |
| `whitelist_groups` | 空 | 允许使用插件的群 ID 列表 |
| `allow_private` | `true` | 是否允许私聊使用查询命令 |
| `data_dir` | 空 | 自定义数据目录，留空使用 AstrBot 默认目录 |

## 注意事项

- `/map`、玩家段位和猎杀线来自 Apex Legends API，网络波动或 API 限流时可能查询失败。
- `/全天地图` 当前/下一张以 Apex Legends API 为准；普通学习中会按已观测顺序临时推测未来 24 小时，插件会每小时自动补一次查询来推动闭环形成。观测到闭环后改为已确认地图池推断。新赛季、临近赛季更新或 API 显示地图池变化时会暂时只显示 API 当前/下一张。
- 赛季结束时间来自公开倒计时页面，插件会统一按北京时间展示。
- 同名玩家可能在多个平台存在记录，建议在命令后显式填写平台。
- 监控通知依赖 AstrBot 的主动消息能力；如果当前适配器不支持主动消息，查询命令仍可正常使用。

## 许可

MIT
