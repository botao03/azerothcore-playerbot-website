"""Offline tests: all MySQL calls are mocked, no game records are created."""

from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

import pymysql

from auth import calculate_verifier, check_password
from server import CLASSES, create_app


class FakeStore:
    def __init__(self):
        salt = bytes(range(32))
        self.accounts = {
            7: dict(id=7, username="TESTUSER", salt=salt,
                    verifier=calculate_verifier("TESTUSER", "TESTPASS", salt),
                    totp_secret=None, locked=0, last_ip="127.0.0.1")
        }
        self.statements = []
        self.banned = False
        self.fail_realm = False
        self.committed = 0
        self.rolled_back = 0
        self.pending = None
        self.gm_access = []
        self.bot_accounts = [4]
        self.world_characters = [
            dict(guid=10, account=7, name="测试法师", race=1, **{"class": 8}, level=40, online=1,
                 map=0, zone=12, position_x=1, position_y=2, position_z=3),
            dict(guid=20, account=4, name="测试机器人", race=2, **{"class": 1}, level=50, online=1,
                 map=1, zone=14, position_x=4, position_y=5, position_z=6),
        ]


class FakeConnection:
    def __init__(self, store):
        self.store = store

    def cursor(self):
        return FakeCursor(self.store)

    def begin(self):
        self.store.pending = None

    def commit(self):
        self.store.committed += 1
        if self.store.pending:
            self.store.accounts[8] = self.store.pending
            self.store.pending = None

    def rollback(self):
        self.store.rolled_back += 1
        self.store.pending = None

    def close(self):
        pass


class FakeCursor:
    def __init__(self, store):
        self.store = store
        self.rows = []
        self.lastrowid = 8

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, statement, values):
        self.store.statements.append((statement, values))
        if "FROM ip_banned" in statement:
            self.rows = []
        elif "FROM account_banned" in statement:
            self.rows = [{"1": 1}] if self.store.banned else []
        elif "FROM account WHERE username" in statement:
            self.rows = [row for row in self.store.accounts.values() if row["username"] == values[0]]
        elif "FROM account WHERE id" in statement:
            self.rows = [self.store.accounts[values[0]]] if values[0] in self.store.accounts else []
        elif "FROM account_access" in statement:
            matches = [row for row in self.store.gm_access
                       if row["id"] == values[0] and row["RealmID"] in (-1, values[1])]
            self.rows = sorted(matches, key=lambda row: row["RealmID"], reverse=True)[:1]
        elif "FROM playerbots_account_type" in statement:
            self.rows = [{"account_id": identity} for identity in self.store.bot_accounts]
        elif "FROM group_member" in statement:
            self.rows = [{"guid": 100, "memberGuid": identity} for identity in (10, 20)]
        elif "FROM characters" in statement:
            if "WHERE deleteDate IS NULL" in statement and "WHERE account" not in statement:
                self.rows = self.store.world_characters
                return
            if "WHERE account = %s AND deleteDate IS NULL" not in statement or values != (7,):
                raise AssertionError("Character query is not scoped to the logged-in account")
            self.rows = [dict(guid=10, name="测试法师", race=1, **{"class": 8}, gender=0, level=40, online=1)]
        elif "SELECT name FROM realmlist" in statement:
            self.rows = [{"name": "Test Realm"}]
        elif "INSERT INTO account" in statement:
            if any(row["username"] == values[0] for row in self.store.accounts.values()):
                raise pymysql.IntegrityError(1062, "Duplicate username")
            self.store.pending = dict(id=8, username=values[0], salt=values[1], verifier=values[2],
                                      expansion=values[3], locale=values[4], totp_secret=None,
                                      locked=0, last_ip="127.0.0.1")
        elif "INSERT INTO realmcharacters" in statement:
            if self.store.fail_realm:
                raise pymysql.OperationalError(1044, "Access denied")
        else:
            raise AssertionError(f"Unexpected statement: {statement}")

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class PortalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        fixture_root = Path(self.temp.name)
        world_config = fixture_root / "worldserver.conf"
        world_config.write_text(
            '[worldserver]\n'
            'LoginDatabaseInfo = "127.0.0.1;3306;fixture;fixture;fresh_auth"\n'
            'CharacterDatabaseInfo = "127.0.0.1;3306;fixture;fixture;fresh_characters"\n'
            'WorldDatabaseInfo = "127.0.0.1;3306;fixture;fixture;fresh_world"\n'
            'RealmID = 1\nExpansion = 2\n', encoding="utf-8")
        (fixture_root / "modules").mkdir()
        (fixture_root / "modules/playerbots.conf").write_text(
            'PlayerbotsDatabaseInfo = "127.0.0.1;3306;fixture;fixture;fresh_playerbots"\n',
            encoding="utf-8")
        self.store = FakeStore()
        self.db_patch = patch("server.pymysql.connect", side_effect=lambda **kwargs: FakeConnection(self.store))
        self.db_patch.start()
        self.app = create_app({"state_dir": self.temp.name, "world_snapshot": self.temp.name + "/world.csv",
                               "world_config": str(world_config)})
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.csrf = self.client.get("/api/session").json["csrf_token"]

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def post(self, route, body):
        result = self.client.post(route, json=body, headers={"X-CSRF-Token": self.csrf})
        if result.json and "csrf_token" in result.json:
            self.csrf = result.json["csrf_token"]
        return result

    def login(self, password="testpass"):
        return self.post("/api/login", dict(username="testuser", password=password))

    def test_srp_little_endian_vector(self):
        # Fixed vector independently cross-checked using Node.js crypto + BigInt modular exponentiation.
        expected = bytes.fromhex("388aa0fa07b5252db2f75c032b20fd11d63e417277a0e566cf79acf642ceb771")
        self.assertEqual(calculate_verifier("testuser", "testpass", bytes(range(32))), expected)
        self.assertTrue(check_password("TESTUSER", "TESTPASS", bytes(range(32)), expected))
        self.assertFalse(check_password("TESTUSER", "incorrect", bytes(range(32)), expected))

    def test_login_and_character_ownership(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.json["csrf_token"], "")
        data = self.client.get("/api/characters").json
        self.assertEqual(data["characters"][0]["name"], "测试法师")
        self.assertEqual(data["characters"][0]["class_name"], "法师")
        self.assertEqual(data["characters"][0]["class_color"], "#69CCF0")
        self.assertEqual(data["realm"], "Test Realm")
        self.assertNotIn("salt", response.json["account"])
        self.assertNotIn("verifier", response.json["account"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", response.headers["Set-Cookie"])

    def test_unauthenticated_cannot_read_characters(self):
        self.assertEqual(self.client.get("/api/characters").status_code, 401)

    def test_incorrect_password(self):
        self.assertEqual(self.login("wrongpassword").status_code, 401)
        self.assertFalse(self.client.get("/api/session").json["authenticated"])

    def test_csrf_and_origin(self):
        body = dict(username="testuser", password="testpass")
        self.assertEqual(self.client.post("/api/login", json=body).status_code, 403)
        response = self.client.post("/api/login", json=body,
                                    headers={"X-CSRF-Token": self.csrf, "Origin": "https://other.example"})
        self.assertEqual(response.status_code, 403)

    def test_session_logout_revokes_cookie_replay(self):
        self.login()
        cookie_name = self.app.config["SESSION_COOKIE_NAME"]
        stolen_cookie = self.client.get_cookie(cookie_name).value
        self.assertEqual(self.post("/api/logout", {}).status_code, 200)
        self.client.set_cookie(cookie_name, stolen_cookie)
        self.assertEqual(self.client.get("/api/characters").status_code, 401)

    def test_expired_session(self):
        self.login()
        with patch("server.time.time", return_value=10**12):
            self.assertEqual(self.client.get("/api/characters").status_code, 401)

    def test_banned_and_totp_accounts(self):
        self.store.banned = True
        self.assertEqual(self.login().status_code, 403)
        self.store.banned = False
        self.store.accounts[7]["totp_secret"] = b"secret"
        self.assertEqual(self.login().status_code, 403)

    def test_account_banned_after_login(self):
        self.login()
        self.store.banned = True
        self.assertEqual(self.client.get("/api/characters").status_code, 403)
        self.store.banned = False
        self.assertFalse(self.client.get("/api/session").json["authenticated"])

    def test_ip_lock(self):
        self.store.accounts[7].update(locked=1, last_ip="192.0.2.1")
        self.assertEqual(self.login().status_code, 403)

    def test_registration_creates_game_credentials(self):
        response = self.post("/api/register", dict(username="newhero", password="Strong123",
                                                    confirm_password="Strong123"))
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.store.committed, 1)
        account = self.store.accounts[8]
        self.assertEqual(account["username"], "NEWHERO")
        self.assertTrue(check_password("NEWHERO", "strong123", account["salt"], account["verifier"]))
        self.assertEqual(len(account["salt"]), 32)
        self.assertEqual(account["expansion"], 2)
        self.assertEqual(account["locale"], 4)
        self.assertTrue(self.client.get("/api/session").json["authenticated"])
        self.assertFalse(any("account_access" in statement and not statement.startswith("SELECT")
                             for statement, _ in self.store.statements))

    def test_monitoring_requires_login_and_gmlevel(self):
        for route in ("/api/monitor/bots", "/api/monitor/players"):
            self.assertEqual(self.client.get(route).status_code, 401)
        self.assertEqual(self.login().json["account"]["gmlevel"], 0)
        for route in ("/api/monitor/bots", "/api/monitor/players"):
            self.assertEqual(self.client.get(route).status_code, 403)
        self.assertFalse(any("FROM playerbots_account_type" in sql for sql, _ in self.store.statements))

    def test_other_realm_privileges_do_not_grant_monitoring(self):
        self.store.gm_access = [{"id": 7, "gmlevel": 3, "RealmID": 999}]
        self.assertEqual(self.login().json["account"]["gmlevel"], 0)
        self.assertEqual(self.client.get("/api/monitor/bots").status_code, 403)

    def test_gm_monitoring_and_immediate_demotion(self):
        self.store.gm_access = [{"id": 7, "gmlevel": 1, "RealmID": -1}]
        self.assertEqual(self.login().json["account"]["gmlevel"], 1)
        bots = self.client.get("/api/monitor/bots")
        self.assertEqual(bots.status_code, 200)
        self.assertEqual([row["name"] for row in bots.json["bots"]], ["测试机器人"])
        self.assertEqual(bots.json["bots"][0]["activity"], "unknown")
        self.assertIsNone(bots.json["bots"][0]["grouped_with_player"])
        self.assertEqual(bots.json["bots"][0]["location_source"], "saved")
        self.assertFalse(bots.json["telemetry"]["available"])
        players = self.client.get("/api/monitor/players")
        self.assertEqual([row["name"] for row in players.json["players"]], ["测试法师"])
        self.assertEqual(self.client.get("/api/characters").json["characters"][0]["name"], "测试法师")
        self.store.gm_access = []
        self.assertEqual(self.client.get("/api/monitor/bots").status_code, 403)
        self.assertEqual(self.client.get("/api/monitor/players").status_code, 403)
        self.assertEqual(self.client.get("/api/characters").status_code, 200)

    def test_realm_specific_privileges_override_global(self):
        self.store.gm_access = [{"id": 7, "gmlevel": 3, "RealmID": -1},
                                {"id": 7, "gmlevel": 0, "RealmID": 1}]
        self.assertEqual(self.login().json["account"]["gmlevel"], 0)
        self.assertEqual(self.client.get("/api/monitor/bots").status_code, 403)

    def test_registration_rolls_back_when_realm_initialization_fails(self):
        self.store.fail_realm = True
        response = self.post("/api/register", dict(username="newhero", password="Strong123",
                                                    confirm_password="Strong123"))
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(8, self.store.accounts)
        self.assertEqual(self.store.rolled_back, 1)

    def test_duplicate_account_and_password_confirmation(self):
        response = self.post("/api/register", dict(username="testuser", password="testpass",
                                                    confirm_password="testpass"))
        self.assertEqual(response.status_code, 409)
        response = self.post("/api/register", dict(username="newhero", password="Strong123",
                                                    confirm_password="different"))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(8, self.store.accounts)

    def test_login_rate_limit(self):
        for _ in range(10):
            self.assertEqual(self.login("wrongpassword").status_code, 401)
        self.assertEqual(self.login("wrongpassword").status_code, 429)

    def test_injection_is_only_a_parameter(self):
        self.post("/api/login", dict(username="' OR 1=1 --", password="testpass"))
        statement, values = next((s, v) for s, v in self.store.statements if "WHERE username" in s)
        self.assertNotIn("OR 1=1", statement)
        self.assertEqual(values, ("' OR 1=1 --",))

    def test_class_palette_matches_frontend(self):
        stylesheet = (Path(__file__).resolve().parents[1] / "static/style.css").read_text()
        for class_id, (_, color) in CLASSES.items():
            self.assertIn(f".class-{class_id} {{ --class-color: {color}; }}", stylesheet)

    def test_private_files_not_served(self):
        for url in ("/config.json", "/state/secret.key", "/assets/../server.py", "/assets/../../etc/worldserver.conf"):
            self.assertEqual(self.client.get(url).status_code, 404)

    def test_bot_settings_require_gm_three(self):
        route = "/api/settings/playerbots-ai"
        self.assertEqual(self.client.get(route).status_code, 401)
        self.assertEqual(self.post(route, {"api_key": "sk-test-1234567890"}).status_code, 401)
        self.login()
        for level in range(3):
            self.store.gm_access = [{"id": 7, "gmlevel": level, "RealmID": -1}]
            self.assertEqual(self.client.get(route).status_code, 403)
            self.assertEqual(self.post(route, {"api_key": "sk-test-1234567890"}).status_code, 403)
        self.assertFalse((Path(self.temp.name) / "playerbots-ai.json").exists())

    def test_bot_key_rotation_clear_and_no_disclosure(self):
        route = "/api/settings/playerbots-ai"
        self.store.gm_access = [{"id": 7, "gmlevel": 3, "RealmID": -1}]
        self.login()
        self.assertFalse(self.client.get(route).json["settings"]["configured"])
        key_file = Path(self.temp.name) / "playerbots-ai.json"
        for key in ("sk-test-first-123456789", "sk-test-second-123456789"):
            saved = self.post(route, {"api_key": key})
            self.assertEqual(saved.status_code, 200)
            self.assertTrue(saved.json["settings"]["configured"])
            self.assertEqual(saved.headers["Cache-Control"], "no-store")
            self.assertNotIn(key, saved.get_data(as_text=True))
            self.assertNotIn(key, self.client.get(route).get_data(as_text=True))
            self.assertEqual(json.loads(key_file.read_text())["api_key"], key)
            self.assertEqual(key_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.post(route, {"api_key": ""}).status_code, 400)
        self.assertTrue(self.client.get(route).json["settings"]["configured"])
        self.assertFalse(self.post(route, {"clear": True}).json["settings"]["configured"])
        self.assertNotIn("sk-test-", key_file.read_text())

    def test_bot_settings_recheck_demotion_and_realm(self):
        route = "/api/settings/playerbots-ai"
        self.store.gm_access = [{"id": 7, "gmlevel": 3, "RealmID": -1}]
        self.login()
        self.assertEqual(self.post(route, {"api_key": "sk-test-1234567890"}).status_code, 200)
        key_file = Path(self.temp.name) / "playerbots-ai.json"
        saved = key_file.read_bytes()
        for access in (
                [{"id": 7, "gmlevel": 2, "RealmID": -1}],
                [{"id": 7, "gmlevel": 3, "RealmID": 999}],
                [{"id": 7, "gmlevel": 3, "RealmID": -1}, {"id": 7, "gmlevel": 2, "RealmID": 1}]):
            self.store.gm_access = access
            self.assertEqual(self.client.get(route).status_code, 403)
            self.assertEqual(self.post(route, {"clear": True}).status_code, 403)
            self.assertEqual(key_file.read_bytes(), saved)

    def test_bot_key_validation_and_csrf(self):
        route = "/api/settings/playerbots-ai"
        self.store.gm_access = [{"id": 7, "gmlevel": 3, "RealmID": 1}]
        self.login()
        for body in ({}, [], {"api_key": None}, {"api_key": 123}, {"api_key": "short"},
                     {"api_key": "sk-test-1234567890\n"}, {"api_key": "sk-测试密钥1234567890"},
                     {"api_key": "x" * 513}, {"clear": False}, {"clear": 1},
                     {"api_key": "sk-test-1234567890", "clear": True}):
            self.assertEqual(self.post(route, body).status_code, 400)
        body = {"api_key": "sk-test-1234567890"}
        self.assertEqual(self.client.post(route, json=body).status_code, 403)
        self.assertEqual(self.client.post(route, json=body, headers={
            "X-CSRF-Token": self.csrf, "Origin": "https://other.example"}).status_code, 403)
        self.assertFalse((Path(self.temp.name) / "playerbots-ai.json").exists())

    def test_bot_key_failed_write_preserves_previous_value(self):
        route = "/api/settings/playerbots-ai"
        self.store.gm_access = [{"id": 7, "gmlevel": 3, "RealmID": -1}]
        self.login()
        self.post(route, {"api_key": "sk-test-original-123456789"})
        key_file = Path(self.temp.name) / "playerbots-ai.json"
        saved = key_file.read_bytes()
        with patch("playerbots_ai.os.replace", side_effect=OSError("sk-secret-must-not-leak")):
            result = self.post(route, {"api_key": "sk-test-replacement-123456789"})
        self.assertEqual(result.status_code, 503)
        self.assertNotIn("sk-", result.get_data(as_text=True))
        self.assertEqual(key_file.read_bytes(), saved)
        self.assertEqual(list(Path(self.temp.name).glob(".playerbots-ai-*")), [])


if __name__ == "__main__":
    unittest.main()
