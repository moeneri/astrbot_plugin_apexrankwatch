# astrbot_plugin_apexrankwatch

Apex Rank Watch 是一个面向 QQ 群使用的 AstrBot 插件，用来查询 Apex Legends 玩家段位、地图轮换、当前赛季结束时间和猎杀线，并支持群内持续监控与记录排位分变化。

本插件使用 CODEX+GPT5.5 辅助编程与维护。

## 功能

- 查询玩家段位、RP、等级、在线状态和当前英雄，并输出图片卡片。
- 将玩家加入群监控，排位分变化后主动推送图片通知。
- 支持 `/持续记录` 将玩家加入统计列表，只记录不通报；`/持续视奸` 会同时记录并通报。
- 支持 `/分数变化` 生成最近分数变化竖直高清长图，默认最近 20 次，最多 50 次。
- 查询排位地图轮换、API 学习确认的排位未来 24 小时地图和三人赛匹配地图轮换。
- 查询当前赛季结束时间。
- 查询本赛季猎杀线和大师数量，包含猎杀。
- 支持玩家名、`uid:`、`uuid:` 查询。
- 支持全局查询别名和个人默认查询绑定；别名是全局查询映射，绑定是个人默认查询目标。
- 支持群白名单、用户黑名单、玩家查询黑名单。

## 示例图

### 常用命令效果总览

下图包含 `/新赛季`、`/猎杀`、`/地图`、`/apexrank moeneri` 和 `/apexrankwatch moeneri pc` 的常用输出效果。

![常用命令效果总览](https://cdn.jsdelivr.net/gh/moeneri/astrbot_plugin_apexrankwatch@ca0b02396c6b3f0abc6350087a99a43401656ad5/assets/readme/command_effects_overview.png)

### 分数变化长图示例

下图展示 `/分数变化` 生成的竖直高清长图效果。

备注：统计图需要先使用 `/持续视奸` 或 `/持续记录` 添加玩家，并在后续轮询中采集到有效分数变化后才会生效。

![分数变化长图示例](https://cdn.jsdelivr.net/gh/moeneri/astrbot_plugin_apexrankwatch@main/assets/readme/score_change_chart_example.png)

## 常用命令

```text
/apexhelp
/apexrank <玩家名|uid:...> [平台]
/持续视奸 <玩家名|uid:...> [平台]
/持续记录 <玩家名|uid:...> [平台]
/持续视奸列表
/分数变化 [玩家名|uid:...] [平台] [场次]
/apexrankremove <玩家名|uid:...> [平台]
/map
/全天地图
/匹配地图
/apexseason
/猎杀
/apex绑定 <玩家名|uid:...> [平台]
/apexalias add <别名> <玩家名|uid:...> [平台]
```

平台参数支持 `pc`、`ps4`、`x1`、`switch`。不填写平台时，插件会按 PC、PS、Xbox、Switch 的顺序尝试查询。

## 命令列表

| 命令 | 说明 |
| --- | --- |
| `/apexhelp` | 查看帮助 |
| `/apextest` | 测试插件和主动消息能力 |
| `/apexrank <玩家名|uid:...> [平台]` | 查询玩家段位信息 |
| `/apex查询 <玩家名|uid:...> [平台]` / `/视奸 <玩家名|uid:...> [平台]` | 中文查询别名 |
| `/apexrankwatch <玩家名|uid:...> [平台]` | 将玩家加入当前群监控，记录并主动通报分数变化 |
| `/持续视奸 <玩家名|uid:...> [平台]` / `/apex监控 <玩家名|uid:...> [平台]` | 添加监控中文别名 |
| `/持续记录 <玩家名|uid:...> [平台]` / `/apexrankrecord` | 加入统计列表，只记录不通报 |
| `/apexranklist` / `/apex列表` / `/持续视奸列表` | 查看当前群监控与记录列表 |
| `/分数变化 [玩家名|uid:...] [平台] [场次]` | 生成最近分数变化高清长图，默认 20 次，最多 50 次 |
| `/apex分数变化` / `/分数图` | 分数变化图中文别名 |
| `/apexrankremove <玩家名|uid:...> [平台]` | 从当前群移除玩家监控或记录 |
| `/取消持续视奸 <玩家名|uid:...> [平台]` / `/apex移除 <玩家名|uid:...> [平台]` | 移除监控中文别名 |
| `/map` / `/地图` / `/排位地图` | 查询排位地图轮换 |
| `/全天地图` / `/今日地图` / `/dailymap` | 查询 API 学习确认的排位未来 24 小时地图 |
| `/匹配地图` | 查询三人赛匹配地图轮换 |
| `/apexseason` / `/新赛季` | 查询当前赛季信息 |
| `/apexpredator [平台]` / `/猎杀` | 查询猎杀线和大师数量 |
| `/apexblacklist <add|remove|list|clear> <玩家ID>` | 管理玩家黑名单 |
| `/apexalias add <别名> <玩家名|uid:...> [平台]` | 添加全局查询别名 |
| `/apexalias list` | 查看全局查询别名；列表不包含个人绑定 |
| `/apexalias remove <别名>` / `/apex取消别名 <别名>` | 移除动态查询别名 |
| `/apex绑定 <玩家名|uid:...> [平台]` | 绑定自己的默认查询目标 |
| `/apex绑定 list` / `/apex绑定列表` | 查看 QQ 用户与默认查询目标的绑定列表 |
| `/apex绑定 remove` / `/apex解绑` | 取消自己的默认查询绑定 |
| `/apex_download` | 检测中文字体状态，并在缺少字体时下载插件字体缓存 |
| `/赛季关闭` | 关闭当前群的“赛季”关键词自动回复 |
| `/赛季开启` | 开启当前群的“赛季”关键词自动回复 |

## 插件行为与指令组

- 查询指令组：`/apexrank <玩家名|uid:...> [平台]`、`/apex查询`、`/视奸`
  查询玩家段位、RP、等级、在线状态和当前英雄；支持玩家名、`uid:`、`uuid:`、全局查询别名和个人绑定。
- 监控与统计指令组：`/apexrankwatch <玩家名|uid:...> [平台]`、`/持续记录 <玩家名|uid:...> [平台]`、`/apexranklist`、`/持续视奸列表`、`/分数变化 [玩家名|uid:...] [平台] [场次]`、`/apexrankremove <玩家名|uid:...> [平台]`
  `/持续视奸` 会记录并主动通报排位分变化，`/持续记录` 只记录不通报；两种模式都会进入统计列表，`/分数变化` 可生成默认 20 次、最多 50 次的高清长图。
- 信息指令组：`/map`、`/全天地图`、`/匹配地图`、`/apexseason`、`/apexpredator [平台]`、`/猎杀`
  查询排位地图、未来 24 小时地图排期、三人赛地图、赛季时间和猎杀线。
- 别名指令组：`/apexalias add <别名> <玩家名|uid:...> [平台]`、`/apexalias list`、`/apexalias remove <别名>`、`/apex取消别名 <别名>`
  别名是全局查询映射，适合管理员把群里常用称呼映射到真实玩家名或 UID；`/apexalias list` 图片模式只展示配置别名和动态别名，不展示个人绑定。
- 绑定指令组：`/apex绑定 <玩家名|uid:...> [平台]`、`/apex绑定 list`、`/apex绑定列表`、`/apex绑定 remove`、`/apex解绑`
  绑定是个人默认查询目标，用户绑定后直接发送 `/apex查询` 就会查询自己的绑定目标；绑定列表与别名列表分开展示。
- 管理指令组：`/apexblacklist <add|remove|list|clear> <玩家ID>`、`/赛季关闭`、`/赛季开启`、`/apex_download`
  管理玩家黑名单、赛季关键词回复和中文字体缓存。

## 使用示例

```text
/apexrank moeneri pc
/apexrank uid:0000000000000 pc
/apexalias add 测试 uid:0000000000000
/apexalias list
/apexrank 测试
/apex绑定 uid:0000000000000
/apex查询
/apex绑定 list
/apex解绑
/apexrankwatch moeneri pc
/持续记录 moeneri pc
/持续视奸列表
/分数变化 moeneri pc 50
/apexrankremove moeneri pc
/map
/全天地图
/匹配地图
/apexpredator pc
/猎杀
/apex_download
```

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
| `player_aliases` | 空 | 全局查询别名映射，多个用逗号分隔，例如 `测试=uid:0000000000000,小明=EaName pc`；不包含个人绑定 |
| `alias_enabled` | `true` | 是否启用查询别名和 `/apex绑定` 个人默认查询 |
| `alias_admin_only` | `true` | 是否仅允许主人/管理员通过 QQ 命令管理查询别名 |
| `user_blacklist` | 空 | 禁止使用插件的 QQ 号列表 |
| `owner_qq` | 空 | 插件管理员 QQ 号列表 |
| `whitelist_enabled` | `false` | 是否启用群白名单模式 |
| `whitelist_groups` | 空 | 允许使用插件的群 ID 列表 |
| `allow_private` | `true` | 是否允许私聊使用查询命令 |
| `data_dir` | 空 | 自定义数据目录，留空使用 AstrBot 默认目录 |
| `font_auto_download` | `true` | Linux/Docker 缺少中文字体时自动下载字体缓存 |
| `font_download_url` | 空 | 自定义中文字体下载地址，留空使用官方资源仓库 |
| `output_mode` | `image` | 输出模式，`image` 为图片模式，`text` 为文字模式 |

## 注意事项

- `/map`、玩家段位和猎杀线来自 Apex Legends API，网络波动或 API 限流时可能查询失败。
- `/全天地图` 当前/下一张以 Apex Legends API 为准；普通学习中会按已观测顺序临时推测未来 24 小时，插件会每小时自动补一次查询来推动闭环形成。观测到闭环后改为已确认地图池推断。新赛季、临近赛季更新或 API 显示地图池变化时会优先用 API 当前/下一张；如果公开网页排期能通过 API 当前/下一张校验，会用网页地图池继续补足预测。
- 赛季结束时间来自公开倒计时页面，插件会统一按北京时间展示。
- `/分数变化` 的记录来自 `/持续视奸` 和 `/持续记录` 采集到的有效排位分变化；默认展示最近 20 次，最多 50 次，超过会按 50 次处理。
- 同名玩家可能在多个平台存在记录，建议在命令后显式填写平台。
- 监控通知依赖 AstrBot 的主动消息能力；如果当前适配器不支持主动消息，查询命令仍可正常使用。
- Linux/Docker 环境如果没有中文字体，插件会从 `moeneri/apexrankwatch-assets` 下载 Noto Sans CJK 字体到插件数据目录，并用 SHA256 校验后再加载；插件 zip 内不包含完整字体文件。
- 第一次使用插件命令时如果检测不到中文字体，会先提示字体下载方式；也可以随时发送 `/apex_download` 手动检测和下载。

## 数据来源

- 玩家段位、RP、猎杀线、大师数量、地图轮换等接口数据来自 Apex Legends API（`api.mozambiquehe.re`）。
- 赛季倒计时和部分赛季信息参考 `apexlegendsstatus.com` 的公开信息。
- 插件仅做查询、整理和图片展示，数据准确性以对应来源实时返回为准。

## 许可

MIT

## 开发说明

本插件使用 CODEX+GPT5.5 协助编写。
