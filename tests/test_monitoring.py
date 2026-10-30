import csv
import os
from pathlib import Path
import tempfile
import time
import unittest

from monitoring import WorldMonitor, dbc_names


FIELDS = "sampled_at guid name race class level map zone area x y z is_bot is_player group_id activity action".split()


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        config = root / "world.conf"
        config.write_text(f'RealmID = 2\nDataDir = "{root}"\n')
        self.path = root / "world.csv"
        self.monitor = WorldMonitor(config, {"world_snapshot": str(self.path)})
        self.rows = [dict(guid=1, account=10, name="玩家", race=1, **{"class": 8}, level=40, online=1,
                          map=0, zone=12, position_x=1, position_y=2, position_z=3),
                     dict(guid=2, account=20, name="机器人", race=2, **{"class": 1}, level=50, online=1,
                          map=1, zone=14, position_x=4, position_y=5, position_z=6)]

    def tearDown(self):
        self.temp.cleanup()

    def sample(self, identity, **overrides):
        return dict(sampled_at=int(time.time()), guid=identity, name=f"角色{identity}", race=1,
                    **{"class": 8}, level=60, map=0, zone=12, area=12, x=100.5, y=200.5, z=30,
                    is_bot=identity == 2, is_player=identity == 1, group_id=42,
                    activity="quest", action='quest, "目标"', **overrides)

    def write(self, *samples):
        with self.path.open("w", newline="") as file:
            writer = csv.DictWriter(file, FIELDS)
            writer.writeheader()
            for sample in samples:
                writer.writerow({key: int(value) if isinstance(value, bool) else value
                                 for key, value in sample.items()})

    def query(self, kind, sql):
        if kind == "bots":
            return [{"account_id": 20}]
        if "FROM group_member" in sql:
            return [{"guid": 99, "memberGuid": identity} for identity in (1, 2)]
        return self.rows

    def roster(self):
        return self.monitor.roster(self.query, lambda row: dict(id=row["guid"], name=row["name"], level=row["level"]))

    def test_runtime_positions_activity_and_real_player_grouping(self):
        self.write(self.sample(1), self.sample(2))
        data = self.roster()
        self.assertTrue(data["telemetry"]["available"])
        bot = data["bots"][0]
        self.assertEqual(bot["activity"], "quest")
        self.assertEqual(bot["activity_label"], "做任务")
        self.assertEqual(bot["action"], 'quest, "目标"')
        self.assertEqual(bot["x"], 100.5)
        self.assertEqual(bot["level"], 60)
        self.assertEqual(bot["location_source"], "live")
        self.assertTrue(bot["grouped_with_player"])
        self.assertEqual(bot["player_teammates"], ["角色1"])

    def test_bot_only_group_is_not_reported_as_player_group(self):
        first = self.sample(1)
        first["group_id"] = 43
        self.write(first, self.sample(2))
        self.assertFalse(self.roster()["bots"][0]["grouped_with_player"])

    def test_altbot_on_player_account_is_not_a_real_player(self):
        alt = self.sample(1)
        alt.update(is_bot=True, is_player=False)
        self.write(alt, self.sample(2))
        data = self.roster()
        self.assertEqual(data["players"], [])
        self.assertEqual(len(data["bots"]), 2)
        self.assertFalse(any(bot["grouped_with_player"] for bot in data["bots"]))

    def test_stale_snapshot_never_claims_current_behavior_or_group(self):
        self.write(self.sample(1), self.sample(2))
        old = time.time() - 120
        os.utime(self.path, (old, old))
        data = self.roster()
        self.assertFalse(data["telemetry"]["available"])
        self.assertEqual(data["bots"][0]["activity"], "unknown")
        self.assertIsNone(data["bots"][0]["grouped_with_player"])

    def test_saved_player_membership_cannot_confirm_a_live_group(self):
        sample = self.sample(2)
        sample["group_id"] = 99
        self.write(sample)
        self.assertIsNone(self.roster()["bots"][0]["grouped_with_player"])

    def test_stale_individual_sample_is_not_revived_by_new_file(self):
        sample = self.sample(2)
        sample["sampled_at"] -= 120
        self.write(sample)
        samples, _ = self.monitor.snapshot()
        self.assertEqual(samples, {})

    def test_offline_bot_and_players_are_distinct(self):
        self.rows[1]["online"] = 0
        data = self.roster()
        self.assertEqual(data["bots"][0]["activity"], "offline")
        self.assertEqual([row["id"] for row in data["players"]], [1])

    def test_invalid_snapshot_and_missing_dbc_are_safe(self):
        self.path.write_text("unexpected,data\n1,2\n")
        self.assertFalse(self.monitor.snapshot()[1]["available"])
        self.path.write_bytes(b"not a dbc")
        with self.assertRaises(ValueError):
            dbc_names(self.path, 11)

    def test_truncated_snapshot_header_is_not_a_live_empty_world(self):
        self.path.write_text("sampled_at,guid\n")
        self.assertFalse(self.monitor.snapshot()[1]["available"])

    def test_truncated_snapshot_record_falls_back_safely(self):
        self.path.write_text(",".join(FIELDS) + f"\n{int(time.time())},2,机器人\n")
        self.assertFalse(self.monitor.snapshot()[1]["available"])


if __name__ == "__main__":
    unittest.main()
