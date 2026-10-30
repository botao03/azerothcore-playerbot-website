#!/usr/bin/env python3
"""Local AzerothCore account portal. Never serves server configuration or credentials."""

import argparse
from collections import defaultdict, deque
from contextlib import closing
from functools import wraps
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time

from flask import Flask, g, jsonify, request, send_from_directory, session
import pymysql
from werkzeug.exceptions import HTTPException

from auth import check_password, registration_data
from monitoring import WorldMonitor, config_value
from playerbots_ai import PlayerbotsAISettings

ROOT = Path(__file__).resolve().parent
CLASSES = {
    1: ("战士", "#C79C6E"),
    2: ("圣骑士", "#F58CBA"),
    3: ("猎人", "#ABD473"),
    4: ("潜行者", "#FFF569"),
    5: ("牧师", "#FFFFFF"),
    6: ("死亡骑士", "#C41F3B"),
    7: ("萨满祭司", "#0070DE"),
    8: ("法师", "#69CCF0"),
    9: ("术士", "#9482C9"),
    11: ("德鲁伊", "#FF7D0A"),
}
RACES = {
    1: "人类", 2: "兽人", 3: "矮人", 4: "暗夜精灵", 5: "亡灵", 6: "牛头人",
    7: "侏儒", 8: "巨魔", 10: "血精灵", 11: "德莱尼",
}
ALLIANCE_RACES = {1, 3, 4, 7, 11}


class PortalError(Exception):
    def __init__(self, message, status=400):
        self.message = message
        self.status = status


def connection_config(value):
    parts = value.split(";")
    if len(parts) != 5:
        raise ValueError("Invalid database configuration format")
    host, port, user, password, database = parts
    connection = dict(user=user, password=password, database=database, charset="utf8mb4",
                      autocommit=True, connect_timeout=5, read_timeout=5, write_timeout=5,
                      cursorclass=pymysql.cursors.DictCursor)
    if port.isdigit():
        connection.update(host=host, port=int(port))
    else:
        connection["unix_socket"] = port
    return connection


def read_database_config(path):
    text = path.read_text(encoding="utf-8")
    databases = {}
    for kind, option in (("auth", "LoginDatabaseInfo"), ("characters", "CharacterDatabaseInfo")):
        match = re.search(rf'^\s*{option}\s*=\s*"([^"\n]+)"', text, re.MULTILINE)
        if not match:
            raise ValueError(f"Missing {option} in worldserver configuration")
        databases[kind] = connection_config(match.group(1))
    expansion = re.search(r"^\s*Expansion\s*=\s*(\d+)", text, re.MULTILINE)
    return databases, int(expansion.group(1)) if expansion else 2


def create_app(overrides=None):
    settings_file = ROOT / "config.json"
    if not settings_file.is_file():
        settings_file = ROOT / "config.example.json"
    settings = json.loads(settings_file.read_text(encoding="utf-8"))
    if overrides:
        settings.update(overrides)
    world_config = (ROOT / settings["world_config"]).resolve()
    databases, expansion = read_database_config(world_config)
    bot_config = world_config.parent / "modules/playerbots.conf"
    if bot_config.exists():
        value = config_value(bot_config.read_text(encoding="utf-8"), "PlayerbotsDatabaseInfo")
        if value:
            databases["bots"] = connection_config(value)
    monitor = WorldMonitor(world_config, settings)
    state = Path(settings.get("state_dir", ROOT / "state"))
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(state, 0o700)
    secret_file = state / "secret.key"
    try:
        with open(secret_file, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as file:
            file.write(secrets.token_hex(32))
    except FileExistsError:
        pass
    secret = secret_file.read_text().strip()
    if len(secret) < 64:
        raise ValueError("Invalid website session secret")
    session_db = state / "sessions.sqlite3"
    with closing(sqlite3.connect(session_db)) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS sessions "
                           "(token TEXT PRIMARY KEY, account_id INTEGER NOT NULL, expires INTEGER NOT NULL)")
        connection.commit()
    os.chmod(session_db, 0o600)
    app = Flask(__name__, static_folder=None)
    app.config.update(SECRET_KEY=secret, MAX_CONTENT_LENGTH=8192, SESSION_COOKIE_NAME="ac_portal_playerbot",
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=settings["secure_cookies"])
    app.config["PORTAL_SETTINGS"] = settings
    app.config["PORTAL_DATABASES"] = databases
    app.extensions["world_monitor"] = monitor
    app.extensions["playerbots_ai"] = PlayerbotsAISettings(state / "playerbots-ai.json")
    attempts = defaultdict(deque)
    attempts_lock = threading.Lock()

    def database(kind):
        name = f"db_{kind}"
        if name not in g:
            setattr(g, name, pymysql.connect(**app.config["PORTAL_DATABASES"][kind]))
        return getattr(g, name)

    def query(kind, statement, values=(), one=False):
        if kind not in app.config["PORTAL_DATABASES"]:
            raise PortalError("机器人数据库尚未配置。", 503)
        with database(kind).cursor() as cursor:
            cursor.execute(statement, values)
            return cursor.fetchone() if one else cursor.fetchall()

    def revoke_session():
        token = session.get("sid")
        if isinstance(token, str):
            with closing(sqlite3.connect(session_db)) as connection:
                connection.execute("DELETE FROM sessions WHERE token = ?",
                                   (hashlib.sha256(token.encode()).hexdigest(),))
                connection.commit()
        session.clear()

    def begin_session(account_id):
        revoke_session()
        token = secrets.token_urlsafe(32)
        expires = int(time.time()) + int(settings["session_hours"] * 3600)
        with closing(sqlite3.connect(session_db)) as connection:
            connection.execute("DELETE FROM sessions WHERE expires < ?", (int(time.time()),))
            connection.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                               (hashlib.sha256(token.encode()).hexdigest(), account_id, expires))
            connection.commit()
        session["sid"] = token
        session["csrf"] = secrets.token_urlsafe(32)

    def account_by_id(account_id):
        return query("auth", "SELECT id, username, totp_secret, locked, last_ip FROM account WHERE id = %s",
                     (account_id,), one=True)

    def account_gmlevel(account_id):
        access = query("auth", "SELECT gmlevel FROM account_access WHERE id = %s "
                       "AND RealmID IN (-1, %s) ORDER BY RealmID DESC LIMIT 1",
                       (account_id, monitor.realm_id), one=True)
        return int(access["gmlevel"]) if access else 0

    def check_account_access(account):
        if account["totp_secret"]:
            raise PortalError("此账号已启用游戏双重验证，暂不支持网站登录。", 403)
        if account["locked"] and account["last_ip"] != request.remote_addr:
            raise PortalError("账号启用了 IP 锁定，请从允许的地址登录。", 403)
        banned = query("auth", "SELECT 1 FROM account_banned WHERE id = %s AND active = 1 "
                       "AND (unbandate > UNIX_TIMESTAMP() OR unbandate = bandate) LIMIT 1",
                       (account["id"],), one=True)
        if banned:
            raise PortalError("此账号处于封禁状态。", 403)

    def current_account():
        token = session.get("sid")
        if not isinstance(token, str):
            return None
        with closing(sqlite3.connect(session_db)) as connection:
            result = connection.execute("SELECT account_id FROM sessions WHERE token = ? AND expires > ?",
                                        (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
        if not result:
            revoke_session()
            return None
        account = account_by_id(result[0])
        if not account:
            revoke_session()
            return None
        try:
            check_account_access(account)
        except PortalError:
            revoke_session()
            raise
        account["gmlevel"] = account_gmlevel(account["id"])
        return account

    def require_login(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            account = current_account()
            if not account:
                raise PortalError("请先登录账号。", 401)
            return function(account, *args, **kwargs)
        return wrapped

    def require_monitoring(function):
        @require_login
        @wraps(function)
        def wrapped(account, *args, **kwargs):
            if account["gmlevel"] < 1:
                raise PortalError("此页面需要 GM 等级 1 或以上。", 403)
            return function(account, *args, **kwargs)
        return wrapped

    def require_bot_settings(function):
        @require_login
        @wraps(function)
        def wrapped(account, *args, **kwargs):
            if account["gmlevel"] < 3:
                raise PortalError("机器人对话设置需要 GM 等级 3 或以上。", 403)
            return function(account, *args, **kwargs)
        return wrapped

    def throttle(action, username=""):
        now = time.monotonic()
        buckets = [(f"{action}:ip:{request.remote_addr}", 20 if action == "login" else 5)]
        if action == "login":
            buckets.append((f"login:account:{username}", 10))
        with attempts_lock:
            for key in list(attempts):
                while attempts[key] and attempts[key][0] < now - 900:
                    attempts[key].popleft()
                if not attempts[key]:
                    del attempts[key]
            if len(attempts) > 10000:
                raise PortalError("请求过于频繁，请稍后再试。", 429)
            for key, limit in buckets:
                if len(attempts[key]) >= limit:
                    raise PortalError("操作过于频繁，请 15 分钟后再试。", 429)
            for key, _ in buckets:
                attempts[key].append(now)

    def credentials(register=False):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise PortalError("请求格式不正确。")
        username, password = payload.get("username"), payload.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise PortalError("请输入账号和密码。")
        username = username.strip()
        if register:
            if not re.fullmatch(r"[A-Za-z0-9_]{3,17}", username):
                raise PortalError("账号需为 3–17 位英文字母、数字或下划线。")
            if not 6 <= len(password) <= 16:
                raise PortalError("密码需为 6–16 位。")
            if password != payload.get("confirm_password"):
                raise PortalError("两次输入的密码不一致。")
        if not 1 <= len(username) <= 17 or not username.isascii():
            raise PortalError("账号格式不正确。")
        if not 1 <= len(password) <= 16 or not re.fullmatch(r"[\x21-\x7e]+", password):
            raise PortalError("密码需为不含空格的英文字符、数字或符号，最多 16 位。")
        return username.upper(), password

    def public_account(account):
        return {"username": account["username"], "gmlevel": account.get("gmlevel", 0)}

    def describe_character(row):
        class_name, color = CLASSES.get(row["class"], ("未知职业", "#B8C1C8"))
        return dict(id=row["guid"], name=row["name"], level=row["level"], online=bool(row["online"]),
                    class_id=row["class"], class_name=class_name, class_color=color,
                    race_name=RACES.get(row["race"], "未知种族"),
                    faction="联盟" if row["race"] in ALLIANCE_RACES else "部落")

    @app.before_request
    def protect_api():
        if not request.path.startswith("/api/"):
            return
        if request.method == "POST":
            expected = session.get("csrf", "")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not hmac.compare_digest(expected, supplied):
                raise PortalError("页面验证已过期，请刷新后重试。", 403)
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                raise PortalError("不允许跨站请求。", 403)
        if request.path == "/api/logout":
            return
        banned_ip = query("auth", "SELECT 1 FROM ip_banned WHERE ip = %s "
                          "AND (unbandate > UNIX_TIMESTAMP() OR unbandate = bandate) LIMIT 1",
                          (request.remote_addr,), one=True)
        if banned_ip:
            raise PortalError("当前地址处于封禁状态。", 403)

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        if settings["secure_cookies"]:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.teardown_appcontext
    def close_databases(exception):
        for kind in app.config["PORTAL_DATABASES"]:
            connection = g.pop(f"db_{kind}", None)
            if connection:
                connection.close()

    @app.errorhandler(PortalError)
    def portal_error(error):
        response = jsonify(error=error.message)
        if error.status == 429:
            response.headers["Retry-After"] = "900"
        return response, error.status

    @app.errorhandler(pymysql.MySQLError)
    def database_error(error):
        # MySQL error text may include credentials or SQL values; log only its numeric code.
        app.logger.error("Database operation failed (code %s)", error.args[0] if error.args else "unknown")
        return jsonify(error="数据库暂时不可用，请稍后重试。"), 503

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error="请求无效或页面不存在。"), error.code

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.error("Website error: %s", type(error).__name__)
        return jsonify(error="服务暂时不可用，请稍后重试。"), 500

    @app.get("/")
    def index():
        return send_from_directory(ROOT / "static", "index.html")

    @app.get("/assets/<path:filename>")
    def assets(filename):
        return send_from_directory(ROOT / "static", filename)

    @app.get("/api/session")
    def session_info():
        account = current_account()
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return jsonify(authenticated=bool(account), account=public_account(account) if account else None,
                       csrf_token=session["csrf"], registration_enabled=settings["registration_enabled"])

    @app.post("/api/login")
    def login():
        username, password = credentials()
        throttle("login", username)
        account = query("auth", "SELECT id, username, salt, verifier, totp_secret, locked, last_ip "
                        "FROM account WHERE username = %s", (username,), one=True)
        if not account:
            # Perform the same expensive operation even when the username does not exist.
            check_password(username, password, bytes(32), bytes(32))
            raise PortalError("账号或密码错误。", 401)
        if not check_password(account["username"], password, account["salt"], account["verifier"]):
            raise PortalError("账号或密码错误。", 401)
        check_account_access(account)
        account["gmlevel"] = account_gmlevel(account["id"])
        begin_session(account["id"])
        return jsonify(account=public_account(account), csrf_token=session["csrf"])

    @app.post("/api/register")
    def register():
        if not settings["registration_enabled"]:
            raise PortalError("注册暂未开放。", 403)
        username, password = credentials(register=True)
        throttle("register")
        salt, verifier = registration_data(username, password)
        connection = database("auth")
        connection.begin()
        try:
            with connection.cursor() as cursor:
                cursor.execute("INSERT INTO account (username, salt, verifier, expansion, locale) "
                               "VALUES (%s, %s, %s, %s, %s)", (username, salt, verifier, expansion, 4))
                account_id = cursor.lastrowid
                cursor.execute("INSERT INTO realmcharacters (realmid, acctid, numchars) "
                               "SELECT id, %s, 0 FROM realmlist", (account_id,))
            connection.commit()
        except pymysql.IntegrityError as error:
            connection.rollback()
            if error.args[0] == 1062:
                raise PortalError("此账号已被注册，请换一个账号名称。", 409) from None
            raise
        except Exception:
            connection.rollback()
            raise
        begin_session(account_id)
        return jsonify(account={"username": username, "gmlevel": 0}, csrf_token=session["csrf"]), 201

    @app.post("/api/logout")
    def logout():
        revoke_session()
        session["csrf"] = secrets.token_urlsafe(32)
        return jsonify(csrf_token=session["csrf"])

    @app.get("/api/characters")
    @require_login
    def characters(account):
        rows = query("characters", "SELECT guid, name, race, class, gender, level, online FROM characters "
                     "WHERE account = %s AND deleteDate IS NULL ORDER BY level DESC, name ASC", (account["id"],))
        result = [describe_character(row) for row in rows]
        realms = query("auth", "SELECT name FROM realmlist ORDER BY id LIMIT 1", one=True)
        return jsonify(characters=result, realm=realms["name"] if realms else "AzerothCore",
                       account=public_account(account))

    @app.get("/api/monitor/bots")
    @require_monitoring
    def monitor_bots(account):
        data = monitor.roster(query, describe_character)
        return jsonify(bots=data["bots"], telemetry=data["telemetry"], queried_at=data["queried_at"],
                       account=public_account(account))

    @app.get("/api/monitor/players")
    @require_monitoring
    def monitor_players(account):
        data = monitor.roster(query, describe_character)
        return jsonify(players=data["players"], telemetry=data["telemetry"], queried_at=data["queried_at"],
                       account=public_account(account))

    @app.get("/api/settings/playerbots-ai")
    @require_bot_settings
    def bot_ai_settings(account):
        try:
            data = app.extensions["playerbots_ai"].status()
        except (OSError, ValueError):
            raise PortalError("暂时无法读取对话设置，请稍后重试。", 503) from None
        return jsonify(settings=data, account=public_account(account))

    @app.post("/api/settings/playerbots-ai")
    @require_bot_settings
    def update_bot_ai_settings(account):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict) or set(payload) not in ({"api_key"}, {"clear"}):
            raise PortalError("请求格式不正确。")
        if "clear" in payload:
            if payload["clear"] is not True:
                raise PortalError("请求格式不正确。")
            api_key = ""
        else:
            api_key = payload["api_key"]
            if not isinstance(api_key, str) or not api_key:
                raise PortalError("请输入新的 API key；如需停用，请使用清除按钮。")
        try:
            data = app.extensions["playerbots_ai"].update(api_key, account["id"])
        except ValueError:
            raise PortalError("API key 需为 16–512 位不含空格的英文字符、数字或符号。") from None
        except OSError:
            raise PortalError("暂时无法保存对话设置，请稍后重试。", 503) from None
        return jsonify(settings=data, account=public_account(account))

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Read-only database and schema check")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app()
    if args.check:
        try:
            for kind, statement in (
                ("auth", "SELECT id, username, salt, verifier, totp_secret, locked, last_ip FROM account LIMIT 0"),
                ("auth", "SELECT id, active, bandate, unbandate FROM account_banned LIMIT 0"),
                ("auth", "SELECT ip, bandate, unbandate FROM ip_banned LIMIT 0"),
                ("auth", "SELECT realmid, acctid, numchars FROM realmcharacters LIMIT 0"),
                ("auth", "SELECT id, name FROM realmlist LIMIT 0"),
                ("auth", "SELECT id, gmlevel, RealmID FROM account_access LIMIT 0"),
                ("characters", "SELECT guid, account, name, race, class, gender, level, online, deleteDate "
                 "FROM characters LIMIT 0"),
                ("characters", "SELECT guid, map, zone, position_x, position_y, position_z FROM characters LIMIT 0"),
                ("characters", "SELECT guid, memberGuid FROM group_member LIMIT 0"),
                ("bots", "SELECT account_id FROM playerbots_account_type LIMIT 0"),
            ):
                with closing(pymysql.connect(**app.config["PORTAL_DATABASES"][kind])) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(statement)
            print("OK: AzerothCore account and character database schemas are compatible.")
        except pymysql.MySQLError as error:
            raise SystemExit(f"Database check failed (code {error.args[0]}). Check the server configuration.") from None
    else:
        from waitress import serve
        config = app.config["PORTAL_SETTINGS"]
        print(f"AzerothCore portal: http://127.0.0.1:{config['port']}", flush=True)
        serve(app, host=config["host"], port=config["port"], threads=8, connection_limit=100,
              channel_timeout=30, max_request_body_size=8192, clear_untrusted_proxy_headers=True)
