# astrbot_plugin_apexrankwatch

AstrBot 插件，用于查询 Apex Legends 玩家排位信息，并在群聊中持续监控 RP 变化。

## 功能

- 查询玩家段位、RP、等级、在线状态、当前英雄
- 支持玩家名查询，也支持 `uid:` / `uuid:` 查询
- 支持按群添加持续监控，分数变化后主动推送通知
- 支持查询当前赛季与指定历史赛季
- 支持识别上下半赛季，并区分公开时间表与推导时间
- 面向 Bot 用户输出时统一转换为北京时间
- 支持群白名单、用户黑名单、动态玩家黑名单
- 支持群内“赛季”关键词自动回复

## 配置项

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `api_key` | 空 | Apex Legends API Key |
| `debug_logging` | `false` | 是否输出调试日志 |
| `check_interval` | `2` | 轮询间隔，单位分钟 |
| `timeout_ms` | `10000` | HTTP 请求超时，单位毫秒 |
| `max_retries` | `3` | 请求失败重试次数 |
| `min_valid_score` | `1` | 最小有效分数，低于该值视为异常数据 |
| `blacklist` | 空 | 禁止查询和监控的玩家 ID 列表 |
| `query_blocklist` | 空 | 仅禁止查询的玩家 ID 列表 |
| `user_blacklist` | 空 | 禁止使用插件的用户列表 |
| `owner_qq` | 空 | 插件管理者列表 |
| `whitelist_enabled` | `false` | 是否启用群白名单模式 |
| `whitelist_groups` | 空 | 允许使用插件的群列表 |
| `allow_private` | `true` | 是否允许私聊使用 |
| `data_dir` | 空 | 自定义数据目录 |

## 命令

| 命令 | 说明 |
| --- | --- |
| `/apexhelp` | 查看帮助 |
| `/apextest` | 测试插件与主动消息能力 |
| `/apexrank <玩家名\|uid:...> [平台]` | 查询玩家信息 |
| `/apexrankwatch <玩家名\|uid:...> [平台]` | 将玩家加入当前群监控 |
| `/apexranklist` | 查看当前群监控列表 |
| `/apexrankremove <玩家名\|uid:...> [平台]` | 从当前群监控中移除玩家 |
| `/apexseason [赛季号]` | 查询当前赛季或指定赛季信息 |
| `/apexblacklist <add\|remove\|list\|clear> <玩家ID>` | 管理动态黑名单 |
| `/赛季关闭` | 关闭当前群的“赛季”关键词自动回复 |
| `/赛季开启` | 开启当前群的“赛季”关键词自动回复 |

### 常用别名

- 查询：`/apex查询`、`/视奸`
- 监控：`/apex监控`、`/持续视奸`
- 移除：`/apex移除`、`/取消持续视奸`
- 赛季：`/apex赛季`、`/新赛季`
- 黑名单：`/apex黑名单`、`/不准视奸`、`/apexban`
- 帮助：`/apex帮助`

## 赛季说明

- 当前赛季起止时间优先使用 `apexseasons.online` 的结构化数据。
- 历史赛季 split 信息优先使用 `Esports Tales` 的公开时间表。
- 如果网站没有提供精确 split 时刻，插件会按北京时间周三凌晨 `01:00` 结合赛季区间推导，并在输出中明确提示。
- 部分历史赛季本身没有上下半赛季重置，插件会直接说明该赛季无 split 重置。

## 注意事项

- 赛季时间来自第三方站点，仅供参考。
- 上下半赛季时间可能来自公开资料整合或推导结果，输出会标明来源与说明。
- 同名玩家可能在多个平台存在记录，建议显式指定平台。
- 部分适配器不支持主动消息；查询命令仍可使用，但监控通知可能无法推送。
- API Key 申请后通常还需要在 `https://portal.apexlegendsapi.com/discord-auth` 绑定 Discord 完成验证。

## 许可

MIT
