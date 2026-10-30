"""Read-only world monitoring. Missing runtime samples never masquerade as live data."""

import csv
import math
import os
from pathlib import Path
import re
import struct
import threading
import time


ACTIVITIES = {
    "combat": "战斗", "dead": "死亡", "quest": "做任务", "patrol": "巡逻",
    "grind": "打怪练级", "rest": "休息", "travel": "赶路", "flight": "飞行",
    "battleground": "战场", "gather": "采集", "outdoor_pvp": "野外 PvP",
    "idle": "待机", "offline": "离线", "unknown": "暂无实时状态",
}


def config_value(text, key, default=""):
    match = re.search(rf'^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(?:"([^"\n]*)"|([^#\r\n]*))',
                      text, re.MULTILINE)
    return (match.group(1) if match.group(1) is not None else match.group(2)).strip() if match else default


def dbc_names(path, name_field):
    """WotLK WDBC string fields; prefer zhCN, then enUS, then another available locale."""
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("Incomplete DBC header")
    magic, count, fields, size, strings_size = struct.unpack_from("<4s4I", data)
    strings_start = 20 + count * size
    if (magic != b"WDBC" or size != fields * 4 or fields < name_field + 16 or
            strings_start + strings_size != len(data)):
        raise ValueError("Invalid DBC format")
    strings = data[strings_start:]
    result = {}
    for index in range(count):
        row = struct.unpack_from(f"<{fields}I", data, 20 + index * size)
        for locale in (4, 0, 1, 2, 3, *range(5, 16)):
            offset = row[name_field + locale]
            if not 0 < offset < strings_size:
                continue
            end = strings.find(b"\0", offset)
            if end < 0:
                raise ValueError("Invalid DBC string")
            result[row[0]] = strings[offset:end].decode("utf-8")
            break
    return result


class WorldMonitor:
    def __init__(self, world_config, settings):
        config = world_config.read_text(encoding="utf-8")
        self.realm_id = int(config_value(config, "RealmID", "1"))
        data_dir = Path(config_value(config, "DataDir", str(world_config.parent.parent / "data")))
        self.snapshot_path = Path(settings.get("world_snapshot", "../run/website-world.csv"))
        if not self.snapshot_path.is_absolute():
            self.snapshot_path = (Path(__file__).resolve().parent / self.snapshot_path).resolve()
        self.max_age = int(settings.get("snapshot_max_age", 30))
        self.areas, self.maps = {}, {}
        for filename, field, destination in (("AreaTable.dbc", 11, self.areas), ("Map.dbc", 5, self.maps)):
            try:
                destination.update(dbc_names(data_dir / "dbc" / filename, field))
            except (OSError, ValueError, UnicodeError):
                pass
        self._lock = threading.Lock()
        self._cache = None
        self._cache_at = 0

    def snapshot(self):
        try:
            # Open before stat so an atomic replacement cannot mix two snapshots.
            with self.snapshot_path.open(encoding="utf-8", newline="") as file:
                stamp = os.fstat(file.fileno()).st_mtime
                age = time.time() - stamp
                if age < -5 or age > self.max_age:
                    return {}, {"available": False, "updated_at": stamp, "reason": "实时快照已过期"}
                rows = {}
                reader = csv.DictReader(file)
                required = {"sampled_at", "guid", "name", "race", "class", "level", "map", "zone", "area",
                            "x", "y", "z", "is_bot", "is_player", "group_id", "activity", "action"}
                if not required.issubset(reader.fieldnames or []):
                    raise ValueError("Invalid snapshot header")
                for row in reader:
                    identity = int(row["guid"])
                    sampled_at = float(row["sampled_at"])
                    if not 0 <= time.time() - sampled_at <= self.max_age:
                        continue
                    for key in ("guid", "race", "class", "level", "map", "zone", "area", "group_id"):
                        row[key] = int(row[key])
                    for key in ("x", "y", "z", "sampled_at"):
                        row[key] = float(row[key])
                        if not math.isfinite(row[key]):
                            raise ValueError("Invalid position")
                    for key in ("is_bot", "is_player"):
                        if row[key] not in ("0", "1"):
                            raise ValueError("Invalid session classification")
                        row[key] = row[key] == "1"
                    rows[identity] = row
                return rows, {"available": True, "updated_at": stamp, "reason": None}
        except (OSError, ValueError, TypeError, KeyError, csv.Error, UnicodeError):
            return {}, {"available": False, "updated_at": None, "reason": "实时状态采集尚未连接"}

    def location(self, row, live=None):
        map_id = int(live["map"] if live else row["map"])
        zone_id = int(live["zone"] if live else row["zone"])
        area_id = int(live["area"]) if live else zone_id
        zone = self.areas.get(zone_id, "")
        area = self.areas.get(area_id, "")
        place = " · ".join(dict.fromkeys(name for name in (zone, area) if name))
        return dict(map_id=map_id, zone_id=zone_id,
                    location=place or self.maps.get(map_id, f"地图 {map_id}"),
                    map_name=self.maps.get(map_id, f"地图 {map_id}"),
                    x=round(float(live["x"] if live else row["position_x"]), 1),
                    y=round(float(live["y"] if live else row["position_y"]), 1),
                    z=round(float(live["z"] if live else row["position_z"]), 1),
                    location_source="live" if live else "saved")

    def roster(self, query, describe):
        # Share the expensive roster queries across tabs and viewers; permissions are checked before this call.
        with self._lock:
            if self._cache is not None and time.monotonic() - self._cache_at < 5:
                return self._cache
            bot_ids = {row["account_id"] for row in query("bots", "SELECT account_id FROM playerbots_account_type")}
            rows = query("characters", "SELECT guid, account, name, race, class, level, online, map, zone, "
                         "position_x, position_y, position_z FROM characters WHERE deleteDate IS NULL "
                         "ORDER BY online DESC, level DESC, name ASC")
            groups = query("characters", "SELECT guid, memberGuid FROM group_member")
            group_by_member = {row["memberGuid"]: row["guid"] for row in groups}
            online_humans = {row["guid"]: row["name"] for row in rows if row["online"] and row["account"] not in bot_ids}
            samples, telemetry = self.snapshot()
            # Runtime sessions distinguish altbots/selfbots from real players on shared accounts.
            for identity, sample in samples.items():
                online_humans.pop(identity, None)
                if sample["is_player"]:
                    online_humans[identity] = sample["name"]
            humans_by_group = {}
            unconfirmed_players = any(identity not in samples for identity in online_humans)
            for identity, name in online_humans.items():
                group_id = samples[identity]["group_id"] if identity in samples else 0
                if group_id:
                    humans_by_group.setdefault(group_id, []).append(name)
            bots, players = [], []
            for row in rows:
                live = samples.get(row["guid"])
                online = bool(row["online"]) or live is not None
                is_bot = live["is_bot"] if live else row["account"] in bot_ids
                is_player = live["is_player"] if live else row["account"] not in bot_ids
                item = describe({**row, **live} if live else row)
                item.update(online=online, **self.location(row, live))
                activity = live["activity"] if live else "unknown" if online else "offline"
                item.update(activity=activity, activity_label=ACTIVITIES.get(activity, "其他活动"),
                            action=live.get("action", "") if live else "",
                            sampled_at=live["sampled_at"] if live else None)
                if live:
                    group_id = live["group_id"]
                    grouped = bool(humans_by_group.get(group_id))
                    if group_id and not grouped and unconfirmed_players:
                        grouped = None
                    item.update(group_id=group_id, grouped_with_player=grouped,
                                player_teammates=humans_by_group.get(group_id, []), group_source="live")
                else:
                    # Database groups are saved membership, not proof of current live membership.
                    group_id = group_by_member.get(row["guid"], 0)
                    item.update(group_id=group_id, grouped_with_player=None, player_teammates=[],
                                group_source="saved" if group_id else "unknown")
                if is_bot:
                    bots.append(item)
                if is_player and online:
                    players.append(item)
            self._cache = {"bots": bots, "players": players, "telemetry": telemetry,
                           "queried_at": time.time()}
            self._cache_at = time.monotonic()
            return self._cache
