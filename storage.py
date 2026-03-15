from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from utils import coerce_bool


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

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "PlayerRecord":
        return PlayerRecord(
            player_name=data.get("player_name", ""),
            platform=data.get("platform", "PC"),
            lookup_id=data.get("lookup_id", data.get("player_name", "")),
            use_uid=coerce_bool(data.get("use_uid", False)),
            rank_score=int(data.get("rank_score", 0) or 0),
            rank_name=data.get("rank_name", ""),
            rank_div=int(data.get("rank_div", 0) or 0),
            last_checked=int(data.get("last_checked", 0) or 0),
            global_rank_percent=data.get("global_rank_percent", "未知"),
            selected_legend=data.get("selected_legend", ""),
            legend_kills_percent=data.get("legend_kills_percent", ""),
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
            k: PlayerRecord.from_dict(v)
            for k, v in players_raw.items()
            if isinstance(v, dict)
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
            if not isinstance(raw, dict):
                return
            self._groups = {
                k: GroupRecord.from_dict(k, v)
                for k, v in raw.items()
                if isinstance(v, dict)
            }
        except Exception as exc:
            self._logger.error(f"加载群订阅数据失败: {exc}")

    def save(self) -> None:
        self._data_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self._data_file.with_suffix(".tmp")
        payload = {k: v.to_dict() for k, v in self._groups.items()}
        tmp_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp_file.replace(self._data_file)

    def get_group(self, group_id: str) -> GroupRecord | None:
        return self._groups.get(group_id)

    def ensure_group(self, group_id: str, origin: str) -> GroupRecord:
        if group_id not in self._groups:
            self._groups[group_id] = GroupRecord(group_id=group_id, origin=origin, players={})
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
        if not group:
            return False
        if player_key not in group.players:
            return False
        del group.players[player_key]
        self.remove_group_if_empty(group_id)
        return True
