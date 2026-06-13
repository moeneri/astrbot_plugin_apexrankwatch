from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

if __package__:
    from .utils import coerce_bool, coerce_int
else:
    from utils import coerce_bool, coerce_int


@dataclass
class PlayerRecord:
    player_name: str
    platform: str
    lookup_id: str
    use_uid: bool
    rank_score: int
    rank_name: str
    rank_div: int
    last_checked: int
    global_rank_percent: str = "未知"
    selected_legend: str = ""
    legend_kills_percent: str = ""
    display_alias: str = ""
    alias_target: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "PlayerRecord":
        return PlayerRecord(
            player_name=str(data.get("player_name", "") or ""),
            platform=str(data.get("platform", "PC") or "PC"),
            lookup_id=str(data.get("lookup_id", data.get("player_name", "")) or ""),
            use_uid=coerce_bool(data.get("use_uid", False)),
            rank_score=coerce_int(data.get("rank_score", 0), 0),
            rank_name=str(data.get("rank_name", "") or ""),
            rank_div=coerce_int(data.get("rank_div", 0), 0),
            last_checked=coerce_int(data.get("last_checked", 0), 0),
            global_rank_percent=str(data.get("global_rank_percent", "未知") or "未知"),
            selected_legend=str(data.get("selected_legend", "") or ""),
            legend_kills_percent=str(data.get("legend_kills_percent", "") or ""),
            display_alias=str(data.get("display_alias", "") or ""),
            alias_target=str(data.get("alias_target", "") or ""),
        )


@dataclass
class GroupRecord:
    group_id: str
    origin: str
    players: dict[str, PlayerRecord]

    def to_dict(self) -> dict:
        return {
            "group_id": self.group_id,
            "origin": self.origin,
            "players": {k: v.to_dict() for k, v in self.players.items()},
        }

    @staticmethod
    def from_dict(group_id: str, data: dict) -> "GroupRecord":
        players_raw = data.get("players", {}) if isinstance(data, dict) else {}
        players = {
            key: PlayerRecord.from_dict(value)
            for key, value in players_raw.items()
            if isinstance(value, dict)
        }
        return GroupRecord(
            group_id=group_id,
            origin=data.get("origin", ""),
            players=players,
        )


class GroupStore:
    def __init__(self, data_file: Path, logger) -> None:
        self._data_file = data_file
        self._logger = logger
        self._groups: dict[str, GroupRecord] = {}

    def load(self) -> None:
        if not self._data_file.exists():
            return

        try:
            content = self._data_file.read_text(encoding="utf-8")
            raw = json.loads(content)
        except (OSError, json.JSONDecodeError) as exc:
            self._logger.error(f"加载群订阅数据失败: {exc}")
            return

        if not isinstance(raw, dict):
            self._logger.error("加载群订阅数据失败: 根节点不是对象")
            return

        groups: dict[str, GroupRecord] = {}
        for group_id, value in raw.items():
            if not isinstance(value, dict):
                self._logger.warning(f"忽略非法群记录 {group_id}: 数据不是对象")
                continue

            origin = str(value.get("origin", "") or "")
            players: dict[str, PlayerRecord] = {}
            players_raw = value.get("players", {})
            if not isinstance(players_raw, dict):
                self._logger.warning(f"群 {group_id} 的 players 字段不是对象，已跳过")
                players_raw = {}

            for player_key, player_value in players_raw.items():
                if not isinstance(player_value, dict):
                    self._logger.warning(
                        f"群 {group_id} 的玩家记录 {player_key} 不是对象，已跳过"
                    )
                    continue
                try:
                    players[str(player_key)] = PlayerRecord.from_dict(player_value)
                except Exception as exc:
                    self._logger.warning(
                        f"群 {group_id} 的玩家记录 {player_key} 已损坏，已跳过: {exc}"
                    )

            groups[str(group_id)] = GroupRecord(
                group_id=str(group_id),
                origin=origin,
                players=players,
            )

        self._groups = groups

    def save(self) -> None:
        tmp_file = self._data_file.with_suffix(".tmp")
        try:
            self._data_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {key: value.to_dict() for key, value in self._groups.items()}
            tmp_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            tmp_file.replace(self._data_file)
        except OSError as exc:
            self._logger.error(f"保存群订阅数据失败: {exc}")
            try:
                if tmp_file.exists():
                    tmp_file.unlink()
            except OSError:
                pass

    def get_group(self, group_id: str) -> GroupRecord | None:
        return self._groups.get(group_id)

    def ensure_group(self, group_id: str, origin: str) -> GroupRecord:
        if group_id not in self._groups:
            self._groups[group_id] = GroupRecord(
                group_id=group_id,
                origin=origin,
                players={},
            )
        if origin:
            self._groups[group_id].origin = origin
        return self._groups[group_id]

    def remove_group_if_empty(self, group_id: str) -> None:
        group = self._groups.get(group_id)
        if group and not group.players:
            del self._groups[group_id]

    def iter_groups(self):
        return list(self._groups.items())

    def set_player(self, group_id: str, player_key: str, record: PlayerRecord) -> None:
        group = self.ensure_group(group_id, "")
        group.players[player_key] = record

    def remove_player(self, group_id: str, player_key: str) -> bool:
        group = self._groups.get(group_id)
        if not group or player_key not in group.players:
            return False
        del group.players[player_key]
        self.remove_group_if_empty(group_id)
        return True
