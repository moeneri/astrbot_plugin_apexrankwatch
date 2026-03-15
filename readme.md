# astrbot_plugin_apexrankwatch

声明：本插件在开发过程中使用了 CodeX CLI 进行辅助开发。

AstrBot 插件，用于查询 Apex Legends 玩家排位信息，并在群聊中持续监控 RP 变化。

仓库根目录本身就是插件目录，不需要再额外套一层文件夹。

## 功能 ✨

- 查询玩家段位、RP、等级、在线状态、当前英雄
- 支持玩家名查询，也支持 `uid:` / `uuid:` 查询
- 支持按群添加持续监控，分数变化后主动推送通知
- 支持查询赛季信息
- 支持群白名单、用户黑名单、动态玩家黑名单
- 支持群内“赛季”关键词自动回复，可按群开关

## 目录结构 📁

```text
astrbot_plugin_apexrankwatch/
├─ main.py
├─ apex_service.py
├─ storage.py
├─ _conf_schema.json
├─ metadata.yaml
├─ requirements.txt
├─ logo.png
└─ readme.md
```

## 安装 🚀

### 方式一：通过 AstrBot WebUI 上传 ZIP

1. 将本仓库打包为插件压缩包。
2. 在 AstrBot 插件页面上传 ZIP。
3. 安装依赖：

```bash
pip install -r requirements.txt
```

4. 在 AstrBot WebUI 启用插件。
5. 在插件配置中填写 `api_key`。
6. 发送 `/apexhelp` 检查插件是否可用。

### 方式二：手动放入插件目录

1. 将本目录放入 `AstrBot/data/plugins/astrbot_plugin_apexrankwatch`。
2. 进入插件目录安装依赖：

```bash
pip install -r requirements.txt
```

3. 在 AstrBot WebUI 启用插件并填写配置。

API Key 获取地址：<https://portal.apexlegendsapi.com/>

## 配置项 ⚙️

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `api_key` | 空 | Apex Legends API Key |
| `debug_logging` | `false` | 是否开启调试日志；开启后会在日志里打印请求参数和返回数据摘要 |
| `check_interval` | `2` | 轮询间隔，单位分钟 |
| `timeout_ms` | `10000` | HTTP 请求超时，单位毫秒 |
| `max_retries` | `3` | 请求失败重试次数 |
| `min_valid_score` | `1` | 最小有效分数，低于该值视为异常数据 |
| `blacklist` | 空 | 禁止查询和监控的玩家 ID，支持中英文逗号 |
| `query_blocklist` | 空 | 仅禁止查询的玩家 ID，支持中英文逗号 |
| `user_blacklist` | 空 | 禁止使用插件的 QQ 号，支持中英文逗号 |
| `owner_qq` | 空 | 主人 QQ，拥有最高管理权限，支持中英文逗号 |
| `whitelist_enabled` | `false` | 是否启用群白名单模式 |
| `whitelist_groups` | 空 | 允许使用插件的群号列表，支持中英文逗号 |
| `allow_private` | `true` | 是否允许私聊使用 |
| `data_dir` | 空 | 自定义数据目录；留空时使用 AstrBot 默认插件数据目录 |

## 命令 🧭

| 命令 | 说明 |
| --- | --- |
| `/apexhelp` | 查看帮助 |
| `/apextest` | 测试插件和主动消息能力 |
| `/apexrank <玩家名\|uid:...> [平台]` | 查询玩家信息 |
| `/apexrankwatch <玩家名\|uid:...> [平台]` | 将玩家加入当前群监控 |
| `/apexranklist` | 查看当前群监控列表 |
| `/apexrankremove <玩家名\|uid:...> [平台]` | 从当前群监控中移除玩家 |
| `/apexseason` | 查询赛季信息 |
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

平台参数支持：`PC`、`PS4`、`X1`、`SWITCH`。未指定时会按 `PC -> PS4 -> X1 -> SWITCH` 自动尝试。

## 使用示例 📝

```text
/apexrank moeneri pc
/apexrankwatch moeneri pc
/apexrankremove moeneri pc

/apexrank uid:1010153800824 pc
/apexblacklist add uid:1010153800824
```

## 数据文件 💾

插件会在数据目录下写入以下文件：

- `groups.json`：监控群和玩家快照
- `settings.json`：动态黑名单、赛季关键词开关等运行设置

建议定期备份数据目录，尤其是在长期监控多个群时。

## 注意事项 ⚠️

- 赛季时间来自第三方站点 `apexseasons.online`，仅供参考。
- 同名玩家可能在多个平台存在记录，建议显式指定平台。
- 部分适配器不支持主动消息；查询命令仍可使用，但监控通知可能无法推送。
- 首次部署后建议先执行 `/apextest`，确认当前适配器支持主动消息发送。

## 许可 📄

MIT
