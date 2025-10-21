from datetime import datetime, timedelta, timezone  # 標準庫：處理時間/日期。datetime 取得當前時間，timedelta 表示時間差（例如 10 分鐘），timezone 用於建立具時區資訊的 datetime（避免天真時間造成誤差）
import os                                           # 標準庫：作業系統互動（環境變數、路徑、系統指令等）。本專案主要用於讀取環境變數與啟動前清屏
import re                                           # 標準庫：正規表達式，用於輸入格式驗證（帳號、密碼、名稱安全字元白名單）
import secrets                                      # 標準庫：密碼學等級的隨機生成器，用於產生 6 碼驗證碼等安全隨機值（優於 random）
import hmac                                         # 標準庫：HMAC 演算法（以金鑰 + 雜湊），用於二次驗證碼的安全雜湊與驗證
import hashlib                                      # 標準庫：SHA-256 等雜湊演算法，與 hmac 搭配計算訊息摘要
import base64                                       # 標準庫：Base64 編碼/解碼，將雜湊位元組以可儲存/傳輸的字串形式表示
from functools import wraps                         # 標準庫：裝飾器輔助，保留被裝飾函式的原始屬性（名稱、docstring），用於 login_required 等裝飾器
from flask import (                                 # Flask 核心：Web 框架與請求/回應工具
    Flask,                                          # 建立 Flask 應用實例
    request,                                        # 取得 HTTP 請求物件（headers、form、json、cookies、remote_addr 等）
    jsonify,                                        # 將字典/列表安全轉為 JSON 回應
    render_template,                                # 使用 Jinja2 渲染模板（HTML）
    redirect,                                       # 發送 302/3xx 重新導向
    url_for,                                        # 由端點名稱反推 URL（避免硬編路徑）
    session,                                        # 對應使用者的 session 存取（伺服器端/簽章 cookie）
    flash,                                           # 對使用者顯示一次性的提示訊息（成功/錯誤/警告）
    g,
    get_flashed_messages
)
from flask_sqlalchemy import SQLAlchemy             # Flask-SQLAlchemy：SQLAlchemy 與 Flask 整合的 ORM 便利封裝
from passlib.hash import argon2                     # passlib 的 Argon2 密碼雜湊演算法（推薦用於安全儲存密碼）
from sqlalchemy.orm import relationship             # SQLAlchemy：宣告式 ORM 關聯（如一對多、一對一）
from sqlalchemy import func, select, exists         # SQLAlchemy：資料庫聚合/函式（COUNT、MAX、NOW 等）
import threading                                    # 標準庫：多執行緒（此專案中目前未用於關鍵路徑，保留可能的背景任務用途）
from werkzeug.exceptions import BadRequest          # Werkzeug HTTP 例外：用於回報 400 錯誤（輸入驗證失敗等）
from modules.player_generator import generate_and_persist, load_name_texts  # 若 generate_and_persist 內需要 name cache
from flask_login import current_user
from flask_login import LoginManager
from flask_login import UserMixin
from sqlalchemy.dialects.mysql import TINYINT, SMALLINT, INTEGER, BIGINT, ENUM, insert as mysql_insert
from sqlalchemy import CheckConstraint, ForeignKey
from typing import List, Dict, Any

try:
    from dotenv import load_dotenv                  # python-dotenv：從 .env 檔載入環境變數（開發環境方便管理設定）
    load_dotenv()                                   # 容錯載入，正式環境建議由系統環境變數注入，不依賴 .env 檔
    secret_key = os.getenv("SECRET_KEY")
except Exception:
    pass                                            # 若未安裝或讀取失敗，略過不影響正式環境（需確保必填環境變數仍由系統提供）

app = Flask(__name__)                                  # 建立 Flask 應用主體。__name__ 用於定位資源與相對路徑（例如模板、靜態檔）
app.config["JSON_AS_ASCII"] = False                    # JSON 回應允許非 ASCII（UTF-8），避免中文被轉為 \uXXXX 轉義

login_manager = LoginManager()
login_manager.init_app(app)      # 或者 LoginManager(app)
login_manager.login_view = "login"  # 可選：未登入時導向的 endpoint 名稱

# 讀取資料庫與安全相關環境變數（優先由系統注入；開發環境可用 .env）
MYSQL_HOST = os.getenv("MYSQL_HOST")                   # MySQL 主機位址（IP/主機名）
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")           # MySQL 連線埠，預設 3306（字串型態，組 URI 時較方便）
MYSQL_USER = os.getenv("MYSQL_USER")                   # MySQL 使用者
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")           # MySQL 密碼（請以秘密管理工具或環境變數提供）
MYSQL_DB = os.getenv("MYSQL_DB")                       # 目標資料庫名稱
SECRET_KEY = os.getenv("SECRET_KEY")                   # Flask SECRET_KEY（用於 session 簽章等），必須高熵且保密
APP_SALT = os.getenv("APP_SALT")                       # 應用層 HMAC 雜湊的 salt（用以 second code 等），不可公開且需高熵

# 檢查必填環境變數是否齊備，避免啟動後才因設定缺失出錯
missing = [k for k, v in {
    "MYSQL_HOST": MYSQL_HOST,
    "MYSQL_USER": MYSQL_USER,
    "MYSQL_PASSWORD": MYSQL_PASSWORD,
    "MYSQL_DB": MYSQL_DB,
    "SECRET_KEY": SECRET_KEY,
    "APP_SALT": APP_SALT,
}.items() if not v]
if missing:
    raise RuntimeError(f"缺少環境變數：{', '.join(missing)}。請在環境或 .env 設定。")  # 立即終止並提示缺失項，提升部署可觀測性

# 基礎設定
app.config["SECRET_KEY"] = SECRET_KEY                  # 以環境變數覆蓋 SECRET_KEY；不要使用硬編字串防止泄漏與重放攻擊
DATABASE_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"  # 組裝 SQLAlchemy 連線 URI（PyMySQL 驅動）
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL   # 設定 ORM 連線字串
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False   # 關閉事件追蹤以降低額外開銷（未使用 signal 功能時建議關閉）

IDLE_MINUTES = 10                                      # 閒置逾時的邏輯常數（與 PERMANENT_SESSION_LIFETIME 對應），便於在程式其他區塊使用

# 伺服器端閒置登出：10 分鐘滑動過期
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=IDLE_MINUTES)    # 設定 session 的永久存活時間（滑動過期），閒置超過此值觸發登出

# 名稱與輸入驗證相關常數
NAME_MAX = 8                                           # 應用層對玩家相關名稱的最大字數限制（以 len 計算；表情符號可能須改為 grapheme 計數）
SAFE_PATTERN = re.compile(r'^[\u4e00-\u9fffA-Za-z0-9\s._\-]{1,}$')  # 名稱白名單：中英文、數字、空白與 . _ -；避免注入與奇異字元

# 帳密與驗證碼格式規則
PASSWORD_REGEX = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[^\s]{8,12}$") # 密碼需 8–12 字元、至少一個英文字母與數字、不可含空白（可依需求加強特殊字元）
ACCOUNT_REGEX = re.compile(r"^[A-Za-z0-9_.-]{3,100}$")              # 帳號允許英數與 . _ -，長度 3–100（避免過短或過長造成管理困難）
SECOND_CODE_REGEX = re.compile(r"^\d{6}$")                          # 二次驗證碼為純數字 6 碼（前端/後端一致驗證）

db = SQLAlchemy(app)                                 # 初始化 Flask‑SQLAlchemy，綁定至 app；提供 ORM Session、BaseModel 等
TAIPEI_TZ = timezone(timedelta(hours=8))             # 台北時區物件（UTC+8）；用於產生帶時區的 aware datetime

# ========= 新增：讀取 .env 初始值的工具與常數 =========
def env_int(name: str, default: int) -> int:
    """安全讀取整數環境變數，非法或負值回預設"""
    val = os.getenv(name)
    if val is None:
        return default
    try:
        n = int(val)
        return n if n >= 0 else default
    except ValueError:
        return default

PLAYER_DEFAULTS = {
    "team_money": env_int("PLAYER_TEAM_MONEY", 1_000_000),
    "team_player_count": env_int("PLAYER_TEAM_PLAYER_COUNT", 0),
    "team_salary": env_int("PLAYER_TEAM_SALARY", 0),
    "arena_seat_count": env_int("PLAYER_ARENA_SEAT_COUNT", 2000),
    "fanclub_staff_count": env_int("PLAYER_FANCLUB_STAFF_COUNT", 0),
    "fanclub_member_count": env_int("PLAYER_FANCLUB_MEMBER_COUNT", 0),
    "scouting_chances_left": env_int("PLAYER_SCOUTING_CHANCES_LEFT", 100),
    "league_results_win": env_int("PLAYER_LEAGUE_RESULTS_WIN", 0),
    "league_results_lose": env_int("PLAYER_LEAGUE_RESULTS_LOSE", 0),
    "rookie_league_results_win": env_int("PLAYER_ROOKIE_LEAGUE_RESULTS_WIN", 0),
    "rookie_league_results_lose": env_int("PLAYER_ROOKIE_LEAGUE_RESULTS_LOSE", 0),
}
# ========= 新增段落結束 =========

def now_taipei():
    # 傳回台北時區的 aware datetime，避免 naive datetime 帶來的跨時區/夏令時問題
    return datetime.now(TAIPEI_TZ)

class User(db.Model, UserMixin):
    __tablename__ = "users"  # 使用者基本資料與憑證表（帳號、雜湊密碼、2FA 等）
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)  # 主鍵，自動遞增
    account = db.Column(db.String(100), unique=True, nullable=False, index=True)  # 帳號：唯一、必填、加索引以加速登入/查詢
    password_hash = db.Column(db.String(255), nullable=False)  # 密碼雜湊值（建議使用 bcrypt/argon2，切勿存純文字）
    second_code = db.Column("second_code_hash", db.String(255), nullable=False)  # 二次驗證碼的雜湊值欄，欄位實名 second_code_hash，模型屬性名為 second_code
    register_ip = db.Column(db.String(45), nullable=False)  # 註冊時的來源 IP，支援 IPv6（最長 45 字元）
    register_at = db.Column(
        db.TIMESTAMP,
        nullable=False,
        server_default=db.text("CURRENT_TIMESTAMP")
    )  # 註冊時間（由資料庫端預設當前時間，降低應用與 DB 時鐘偏差）
    logs = relationship(
        "LoginLog",
        back_populates="user",
        cascade="all, delete-orphan"
    )  # 關聯到 LoginLog：一對多。當 User 被刪除，相關 LoginLog 一併刪除（避免孤兒資料）

    # 安全與實務建議：
    # - account 建議後端也做正則驗證（你已有 ACCOUNT_REGEX），防止特殊字元或空白。
    # - password_hash/second_code_hash 請使用強雜湊（argon2id 或 bcrypt），並存含參數的雜湊字串（含 salt）。
    # - register_ip 建議保存原始字串，不做反查；若需統計可另建聚合表。
    # - 若有需要可考慮加入 is_active、is_locked、failed_attempts、last_login_at 等欄位。

class LoginLog(db.Model):
    __tablename__ = "login_log"  # 登入事件日誌（成功/失敗）
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)  # 主鍵
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False
    )  # 對應的使用者。若使用者刪除，相關登入記錄一併刪除（視稽核需求調整是否保留）
    ip = db.Column(db.String(45), nullable=False)  # 登入來源 IP（IPv4/IPv6）
    success = db.Column(db.Boolean, nullable=False)  # 是否登入成功（True=成功，False=失敗）
    user_agent = db.Column(db.String(512))  # 用戶端 UA，適度加長（有些瀏覽器/設備 UA 較長）
    created_at = db.Column(
        db.TIMESTAMP,
        server_default=db.text("CURRENT_TIMESTAMP")
    )  # 記錄時間（DB 端時間戳，避免應用與 DB 時差）
    user = relationship(
        "User",
        back_populates="logs"
    )  # 反向關聯到 User.logs，便於 user.logs 查詢該使用者的登入歷史

    # 實務建議：
    # - 可加索引：user_id, created_at, success，用於報表與風控分析（例如同 IP 多次失敗）。
    # - 若需地理定位或裝置指紋，可另建維度表，避免在此表塞入過多欄位。

class LogoutLog(db.Model):
    __tablename__ = "logout_log"  # 登出事件日誌（紀錄登出原因）
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)  # 主鍵
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False
    )  # 對應的使用者。若使用者刪除，一併刪除此登出紀錄（依稽核政策調整）
    ip = db.Column(db.String(45), nullable=False)  # 登出時的來源 IP
    reason = db.Column(
        db.Enum("manual", "idle_timeout", "forced", "session_invalid", "other", name="logout_reason"),
        nullable=False,
        server_default="manual"
    )  # 登出原因：manual=使用者主動、idle_timeout=閒置逾時、forced=管理端強制、session_invalid=會話失效、other=其他
    user_agent = db.Column(db.String(512))  # 用戶端 UA
    created_at = db.Column(
        db.TIMESTAMP,
        server_default=db.text("CURRENT_TIMESTAMP")
    )  # 記錄時間（DB 端時間戳）

    # 實務建議：
    # - 若要統計各原因占比與時段趨勢，建議加索引：user_id、reason、created_at。
    # - 若要追蹤跨裝置登出行為，可額外記錄 session_id 或裝置標識。

class PlayerProfile(db.Model):
    __tablename__ = "player_profile"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
        name="user_id",
    )

    player_name = db.Column(db.String(32), nullable=False)
    team_name = db.Column(db.String(32), nullable=False, unique=True)  # DDL: UNIQUE KEY uq_team_name
    arena_name = db.Column(db.String(32), nullable=False)
    fanpage_name = db.Column(db.String(32), nullable=False)

    team_money = db.Column(INTEGER(unsigned=True), nullable=False, default=0)
    team_player_count = db.Column(INTEGER(unsigned=True), nullable=False, default=0)
    team_salary = db.Column(BIGINT(unsigned=True), nullable=False, default=0)
    arena_seat_count = db.Column(INTEGER(unsigned=True), nullable=False, default=0)
    fanclub_staff_count = db.Column(INTEGER(unsigned=True), nullable=False, default=0)
    fanclub_member_count = db.Column(INTEGER(unsigned=True), nullable=False, default=0)
    scouting_chances_left = db.Column(INTEGER(unsigned=True), nullable=False, default=0)
    league_results_win = db.Column(INTEGER(unsigned=True), nullable=False, default=0)
    league_results_lose = db.Column(INTEGER(unsigned=True), nullable=False, default=0)
    rookie_league_results_win = db.Column(INTEGER(unsigned=True), nullable=False, default=0)
    rookie_league_results_lose = db.Column(INTEGER(unsigned=True), nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, server_default=func.current_timestamp())
    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )
    created_ip = db.Column(db.String(64), nullable=True)
    user_agent = db.Column(db.String(256), nullable=True)

    # 關聯（可選）
    user = db.relationship(
        "User",
        backref=db.backref("player_profile", uselist=False, cascade="all, delete-orphan"),
        viewonly=False,
    )

    __table_args__ = (
        db.UniqueConstraint("user_id", name="uq_player_user"),
        db.UniqueConstraint("team_name", name="uq_team_name"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

class PlayersBasic(db.Model):
    __tablename__ = "players_basic"
    __table_args__ = (
        db.Index("idx_players_user", "user_id"),
        db.Index("idx_players_name", "player_name"),
        db.CheckConstraint("age between 15 and 60", name="players_basic_chk_1"),
        db.CheckConstraint("ath_stamina between 1 and 99", name="players_basic_chk_2"),
        db.CheckConstraint("ath_strength between 1 and 99", name="players_basic_chk_3"),
        db.CheckConstraint("ath_speed between 1 and 99", name="players_basic_chk_4"),
        db.CheckConstraint("ath_jump between 1 and 99", name="players_basic_chk_5"),
        db.CheckConstraint("shot_touch between 1 and 99", name="players_basic_chk_6"),
        db.CheckConstraint("shot_release between 1 and 99", name="players_basic_chk_7"),
        db.CheckConstraint("shot_accuracy between 1 and 99", name="players_basic_chk_8"),
        db.CheckConstraint("shot_range between 1 and 99", name="players_basic_chk_9"),
        db.CheckConstraint("def_rebound between 1 and 99", name="players_basic_chk_10"),
        db.CheckConstraint("def_boxout between 1 and 99", name="players_basic_chk_11"),
        db.CheckConstraint("def_contest between 1 and 99", name="players_basic_chk_12"),
        db.CheckConstraint("def_disrupt between 1 and 99", name="players_basic_chk_13"),
        db.CheckConstraint("off_move between 1 and 99", name="players_basic_chk_14"),
        db.CheckConstraint("off_dribble between 1 and 99", name="players_basic_chk_15"),
        db.CheckConstraint("off_pass between 1 and 99", name="players_basic_chk_16"),
        db.CheckConstraint("off_handle between 1 and 99", name="players_basic_chk_17"),
        db.CheckConstraint("talent_offiq between 1 and 99", name="players_basic_chk_18"),
        db.CheckConstraint("talent_defiq between 1 and 99", name="players_basic_chk_19"),
        db.CheckConstraint("talent_health between 1 and 99", name="players_basic_chk_20"),
        db.CheckConstraint("talent_luck between 1 and 99", name="players_basic_chk_21"),
        db.CheckConstraint("untrainable_sum between 10 and 990", name="players_basic_chk_22"),
        db.CheckConstraint("(height_cm IS NULL) OR (height_cm between 160 and 230)", name="players_basic_chk_height"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    player_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    player_name = db.Column(db.String(32), nullable=False)

    age = db.Column(TINYINT(unsigned=True), nullable=False, default=18)
    height_cm = db.Column(SMALLINT(unsigned=True), nullable=True)
    position = db.Column(ENUM("PG", "SG", "SF", "PF", "C"), nullable=False)

    ath_stamina = db.Column(TINYINT(unsigned=True), nullable=False)
    ath_strength = db.Column(TINYINT(unsigned=True), nullable=False)
    ath_speed = db.Column(TINYINT(unsigned=True), nullable=False)
    ath_jump = db.Column(TINYINT(unsigned=True), nullable=False)

    shot_touch = db.Column(TINYINT(unsigned=True), nullable=False)
    shot_release = db.Column(TINYINT(unsigned=True), nullable=False)
    shot_accuracy = db.Column(TINYINT(unsigned=True), nullable=False)
    shot_range = db.Column(TINYINT(unsigned=True), nullable=False)

    def_rebound = db.Column(TINYINT(unsigned=True), nullable=False)
    def_boxout = db.Column(TINYINT(unsigned=True), nullable=False)
    def_contest = db.Column(TINYINT(unsigned=True), nullable=False)
    def_disrupt = db.Column(TINYINT(unsigned=True), nullable=False)

    off_move = db.Column(TINYINT(unsigned=True), nullable=False)
    off_dribble = db.Column(TINYINT(unsigned=True), nullable=False)
    off_pass = db.Column(TINYINT(unsigned=True), nullable=False)
    off_handle = db.Column(TINYINT(unsigned=True), nullable=False)

    talent_offiq = db.Column(TINYINT(unsigned=True), nullable=False)
    talent_defiq = db.Column(TINYINT(unsigned=True), nullable=False)
    talent_health = db.Column(TINYINT(unsigned=True), nullable=False)
    talent_luck = db.Column(TINYINT(unsigned=True), nullable=False)

    untrainable_sum = db.Column(SMALLINT(unsigned=True), nullable=False)
    overall_grade = db.Column(ENUM("G", "C", "B", "A", "S", "SS", "SSR"), nullable=False)
    training_points = db.Column(SMALLINT(unsigned=True), nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, server_default=func.current_timestamp())
    start_salary = db.Column(INTEGER(unsigned=True), nullable=False, default=0, comment="球員起始薪資（生成時依20項能力總和 × 等級係數）")


class TeamRoster(db.Model):
    __tablename__ = "team_roster"
    __table_args__ = (
        db.UniqueConstraint("user_id", "player_id", name="uk_user_player"),
        db.Index("idx_user", "user_id"),
        db.Index("idx_player", "player_id"),
        db.CheckConstraint("contract_years between 1 and 5", name="chk_contract_years"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_0900_ai_ci"},
    )

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, nullable=False, comment="球隊擁有者/經理的使用者ID")
    player_id = db.Column(
        db.Integer,
        db.ForeignKey("players_basic.player_id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False,
        comment="球員ID（對應 players_basic.player_id）",
    )
    player_salary = db.Column(INTEGER(unsigned=True), nullable=False, default=0, comment="簽約薪資（年薪/平均年薪，今日快照）")
    sign_roles = db.Column(ENUM("ROLE","STARTER","GLUE","SPECIALIST","STAR"), nullable=False, default="ROLE", name="sign_roles")
    contract_years = db.Column(TINYINT(unsigned=True), nullable=False, default=1, comment="合約年限（年數）")
    created_at = db.Column(db.DateTime, nullable=False, server_default=func.current_timestamp(), comment="簽約建立時間")

    # 關聯（可選）
    player = db.relationship("PlayersBasic", backref=db.backref("roster_records", cascade="all, delete-orphan"))

"""
工具函式總覽說明
- 目的：
  - 提供 Web 應用（Flask）常用的安全與會話管理工具：IP 取得、驗證碼生成/驗證、密碼雜湊/驗證、登出日誌、安全的登入保護與閒置逾時控制、欄位驗證、滑動過期。
- 依賴：
  - Flask 物件：request、session、redirect、url_for、app.before_request
  - ORM/DB：db（SQLAlchemy session）、LogoutLog、LoginLog
  - 安全/加密：secrets、hmac、hashlib、base64、argon2
  - 時間：datetime、timezone、timedelta（皆使用 UTC 作為邏輯判定）
  - 設定：APP_SALT（HMAC 用 key）、IDLE_MINUTES（閒置分鐘）、NAME_MAX、SAFE_PATTERN
- 安全注意：
  - X-Forwarded-For 使用前請確認反向代理信任鏈，避免 header 欺騙
  - 密碼一律使用 argon2 等強雜湊，短碼以 HMAC-SHA256 雜湊並使用常數時間比對
  - session 滑動過期與閒置逾時需分工清楚：判定函式不續期、呼叫端確認未逾時後才續期
- 日誌與稽核：
  - 登出記錄 write_logout_log 失敗時不拋出例外，確保不影響主流程；如需稽核可搭配 logger
- 部署建議：
  - APP_SALT 放於安全的設定來源（環境變數或秘密管理），定期滾動；滾動策略需考慮舊資料驗證相容性
  - PERMANENT_SESSION_LIFETIME 與 IDLE_MINUTES 規則需協調，避免體驗與安全性衝突
"""

# 工具函式

def get_client_ip() -> str:
    """
    取得客戶端 IP（支援反向代理場景）。

    邏輯：
    - 優先取 HTTP Header 'X-Forwarded-For'（可能是多 IP，逗號分隔，取第一個最原始客戶端 IP）
    - 若無，回退到 Flask 的 request.remote_addr
    - 若兩者都缺（異常狀況），回傳 "0.0.0.0" 作為保底值

    注意事項：
    - 在生產環境若有反向代理（如 Nginx/Cloudflare），請確保正確設定只信任來源代理，
      並在受信任的代理添加/轉發 'X-Forwarded-For'，避免被來路不明的 header 欺騙。
    - IPv6 最長 45 字元，資料庫欄位長度需足夠；目前此函式只回傳字串，不做驗證。

    回傳：
    - 字串型態的 IP（可能是 IPv4 或 IPv6）
    """
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "0.0.0.0"  # 先取 XFF，沒有就用 remote_addr，最後保底
    if isinstance(ip, str) and "," in ip:  # XFF 多層代理時會有多個 IP，以逗號分隔
        # 多層代理時，XFF 形式通常為 "client, proxy1, proxy2"
        ip = ip.split(",")[0].strip()  # 取最左邊第一個即原始客戶端 IP，並去除空白
    return ip  # 回傳字串 IP


def generate_6digit_code() -> str:
    """
    產生 6 位數的隨機數字驗證碼。

    特色：
    - 使用 secrets.choice（密碼學安全隨機）從 0-9 選取，共 6 次，組合成字串。
    - 僅數字，便於手機端輸入。

    注意：
    - 此為「碼值」產生器，請勿直接儲存純文字碼；應搭配 hash_code() 雜湊後保存。
    - 若用於一次性驗證（OTP），請搭配 TTL 與使用次數限制。

    回傳：
    - 字串，例如 "604219"
    """
    return "".join(secrets.choice("0123456789") for _ in range(6))  # 從數字字元集中選 6 次並串接


def hash_password(plain: str) -> str:
    """
    使用 Argon2（建議為 argon2id）對密碼進行雜湊，回傳含參數的雜湊字串。

    輸入：
    - plain: 使用者的純文字密碼（請先做長度與字元集驗證）

    回傳：
    - 可直接存入資料庫的雜湊字串（包含 salt 與參數）

    資安建議：
    - Argon2 參數應依部署環境（CPU/記憶體/延遲要求）調整。
    - 禁止以任何形式保存純文字密碼。
    """
    return argon2.hash(plain)  # 呼叫 argon2 套件產生雜湊，內含 salt 與參數


def verify_password(plain: str, hashed: str) -> bool:
    """
    驗證密碼是否正確。

    邏輯：
    - 將純文字密碼與資料庫中的 Argon2 雜湊字串做驗證
    - 發生例外（雜湊格式錯誤、函式錯誤等）一律視為驗證失敗，避免資訊洩漏

    回傳：
    - True：密碼正確
    - False：密碼錯誤或驗證過程出現例外
    """
    try:
        return argon2.verify(plain, hashed)  # 驗證 plain 是否對應 hashed
    except Exception:
        # 不暴露具體錯誤原因，防止攻擊者蒐集雜湊格式或系統資訊
        return False  # 任一例外都回 False，避免 side-channel 與資訊外洩


def hash_code(plain: str) -> str:
    """
    對短碼（如 6 位數驗證碼）做 HMAC-SHA256 雜湊後以 Base64 編碼，回傳字串。

    用途：
    - 適用於一次性驗證碼、第二因子等短碼的安全保存（避免明文存放）
    - 使用應用層固定鹽值 APP_SALT 作為 HMAC key

    注意：
    - APP_SALT 應為高熵隨機字串，妥善保存（環境變數/秘密管理）
    - 若要支援「跨環境比對」，需保證 APP_SALT 一致；若只在同一環境可自行 rotate。

    回傳：
    - Base64 字串（HMAC digest）
    """
    digest = hmac.new(APP_SALT.encode("utf-8"), plain.encode("utf-8"), hashlib.sha256).digest()  # 使用 HMAC-SHA256 計算位元摘要
    return base64.b64encode(digest).decode("utf-8")  # 將位元摘要以 Base64 轉為可存/可傳的字串


def verify_code(plain: str, hashed: str) -> bool:
    """
    驗證短碼的正確性（與 hash_code() 相對應）。

    邏輯：
    - 對輸入的 plain 以相同 APP_SALT/HMAC-SHA256 計算摘要，Base64 編碼後
    - 使用 hmac.compare_digest 進行常數時間比對，防止時序攻擊

    回傳：
    - True：比對相符
    - False：不相符
    """
    digest = hmac.new(APP_SALT.encode("utf-8"), plain.encode("utf-8"), hashlib.sha256).digest()  # 重新計算輸入碼的 HMAC
    calc = base64.b64encode(digest).decode("utf-8")  # 轉 Base64 與資料庫存的格式一致
    return hmac.compare_digest(calc, hashed)  # 使用常數時間比對避免 timing attack


def write_logout_log(user_id: int, reason: str):
    """
    安全地寫入登出紀錄（logout_log），失敗不影響主流程。

    輸入：
    - user_id: 使用者 ID（必須為正整數；若缺值則直接返回）
    - reason: 登出原因（Enum 值）
      - "manual" | "idle_timeout" | "forced" | "session_invalid" | "other"

    邏輯：
    - 以 get_client_ip() 取得 IP
    - 取 User-Agent 並截斷至長度 512（避免超長 UA 造成 DB 例外）
    - 新增紀錄並提交；若有任何例外，回滾且吞掉錯誤，確保不影響主流程

    注意：
    - 此函式不拋出例外，適合在請求流程中安全呼叫。
    - 若有稽核需求，考慮將失敗事件另行記錄（例如用 logger）。

    無回傳值
    """
    if not user_id:  # 缺少使用者 ID 時不做任何事（避免寫入垃圾紀錄）
        return
    try:
        db.session.add(LogoutLog(  # 新增一筆登出紀錄
            user_id=user_id,  # 紀錄對應的使用者
            ip=get_client_ip(),  # 來源 IP（支援代理）
            reason=reason,  # 登出原因（Enum）
            user_agent=(request.headers.get("User-Agent") or "")[:512]  # 取 UA 並限制長度，避免 DB 欄位溢出
        ))
        db.session.commit()  # 提交交易
    except Exception:
        db.session.rollback()  # 任一例外則回滾，確保不影響其他操作


def login_required(view_func):
    """
    登入保護裝飾器：保護路由需登入才能存取，並處理閒置逾時與 session 失效紀錄。

    行為摘要：
    - 若 session 有 user_id：
      1) 先檢查是否「閒置逾時」：若逾時，清除 session、可選記錄 session_invalid、導向登入頁
      2) 未逾時則呼叫原始 view，並在外部續期（touch_last_seen）
    - 若 session 無 user_id：
      - 若偵測到曾有我們的 session cookie（可能被清），記錄為 session_invalid（可選），並導向登入頁

    技術細節：
    - has_app_session_cookie：透過 request.cookies.get(app.session_cookie_name) 偵測我方 cookie 是否存在
    - 查找近 24 小時內相同 IP/UA 的最後一次成功登入紀錄，若找到對應 user_id，紀錄該 user 的 session_invalid 登出

    安全與體驗：
    - 閒置逾時判定與續期分離：check_idle_timeout_and_record() 不做續期，僅判定/清理；成功進入 view 後再 touch_last_seen()
    - 發生例外（例如 DB 暫時不可用）不阻斷主流程，僅略過紀錄行為
    """
    from functools import wraps  # 匯入 wraps 以保留原函式的名稱與 docstring
    @wraps(view_func)  # 使用 wraps 裝飾，利於除錯與路由註冊識別
    def wrapper(*args, **kwargs):
        uid = session.get("user_id")  # 取得目前 session 的使用者 ID
        # 先檢查是否存在我們的 session cookie（用於後續記錄 session_invalid）
        try:
            has_app_session_cookie = bool(request.cookies.get(app.session_cookie_name))  # 探測是否曾有我方的 session cookie
        except Exception:
            has_app_session_cookie = False  # 如遇到異常（例如 app 未就緒），保守處理為 False

        if uid:  # 已登入使用者
            # 先檢查是否閒置逾時（逾時：已記錄並清除 session）
            if check_idle_timeout_and_record():  # 若回 True 表示已逾時且 session 已清
                if has_app_session_cookie:  # 僅在偵測到我們的 cookie 時，嘗試記錄 session_invalid
                    try:
                        ip = get_client_ip()  # 取得 IP
                        ua = (request.headers.get("User-Agent") or "")[:512]  # 取得 UA（截斷）
                        since = datetime.now(timezone.utc)  - timedelta(hours=24)  # 設定查找時間窗為 24 小時內
                        last = (LoginLog.query  # 查詢最後一次成功登入
                                .filter(LoginLog.ip == ip)
                                .filter(LoginLog.user_agent == ua)
                                .filter(LoginLog.success.is_(True))
                                .filter(LoginLog.created_at >= since)
                                .order_by(LoginLog.created_at.desc())
                                .first())
                        if last and last.user_id:  # 若找到對應使用者
                            write_logout_log(last.user_id, "session_invalid")  # 記錄 session 失效登出
                    except Exception:
                        pass  # 忽略任何記錄過程中的錯誤，避免阻塞登入流程
                return redirect(url_for("login_page", msg="已因閒置逾時登出，請重新登入", type="error"))  # 導回登入頁並提示
            # 未逾時則滑動續期
            touch_last_seen()  # 由呼叫端在確認未逾時後進行續期（更新 last_seen）
            return view_func(*args, **kwargs)  # 執行原 view

        # 未登入：若有 cookie，記錄 session_invalid（可選）
        if has_app_session_cookie:  # 有我方 cookie 但無 session，可能是 cookie 被清或 session 遺失
            try:
                ip = get_client_ip()  # 取得 IP
                ua = (request.headers.get("User-Agent") or "")[:512]  # 取得 UA（截斷）
                since = datetime.now(timezone.utc)  - timedelta(hours=24)  # 查近 24 小時成功登入
                last = (
                    LoginLog.query
                    .filter(LoginLog.ip == ip)
                    .filter(LoginLog.user_agent == ua)
                    .filter(LoginLog.success.is_(True))
                    .filter(LoginLog.created_at >= since)
                    .order_by(LoginLog.created_at.desc())
                    .first()
                )
                if last and last.user_id:  # 若找到對應使用者
                    write_logout_log(last.user_id, "session_invalid")  # 記錄 session 失效登出
            except Exception:
                pass  # 忽略記錄錯誤

        return redirect(url_for("login_page"))  # 未登入一律導向登入頁
    return wrapper  # 回傳包裝後的裝飾器結果


def _now_ts() -> int:
    """
    取得目前 UTC 時區的 Unix Timestamp（秒）。

    用途：
    - 作為 session 的 last_seen 時戳，用於閒置逾時判定

    回傳：
    - 整數秒，例如 1730000000
    """
    return int(datetime.now(timezone.utc).timestamp())  # 使用 UTC，避免時區造成誤差


def touch_last_seen():
    """
    更新目前使用者的 last_seen（滑動續期）。

    行為：
    - 在已登入的請求流程中呼叫，將 session['last_seen'] 設為目前 UTC timestamp

    注意：
    - 不在 check_idle_timeout_and_record() 內處理續期，以避免邏輯耦合與邊界混淆。
    """
    session["last_seen"] = _now_ts()  # 寫入最新的時間戳，用於滑動過期


def check_idle_timeout_and_record() -> bool:
    """
    閒置逾時檢查，並在逾時時寫入登出紀錄與清除 session。

    判定來源：
    - session['user_id'] 與 session['last_seen']（Unix 秒）
    - 閒置秒數 >= IDLE_MINUTES * 60 視為逾時

    逾時處理：
    - 呼叫 write_logout_log(uid, "idle_timeout") 記錄
    - 清除 session（session.clear()）

    回傳：
    - True  => 已逾時，且已寫入登出紀錄並清除 session
    - False => 未逾時（或資訊不足），此時由呼叫端在確認未逾時後再 touch_last_seen() 續期

    邊界與安全：
    - 若 last_seen 缺值或不可解析，視為資訊不足，回傳 False，交由呼叫端續期。
    - 發生例外時不拋出錯誤，盡可能保持主流程可繼續。
    """
    uid = session.get("user_id")  # 從 session 取使用者 ID
    last_seen_ts = session.get("last_seen")  # 取上次活躍時間戳
    if not uid or not last_seen_ts:  # 若未登入或沒有 last_seen
        # 未登入或缺 last_seen，不在此流程判斷為逾時（交由呼叫端續期）
        return False

    try:
        last_seen = int(last_seen_ts)  # 嘗試轉為整數秒
    except Exception:
        # 解析失敗視為缺值，交由呼叫端續期
        return False

    now = int(datetime.now(timezone.utc).timestamp())  # 取得目前 UTC 秒
    idle_seconds = now - last_seen  # 計算閒置秒數
    if idle_seconds >= IDLE_MINUTES * 60:  # 超過設定的閒置門檻即逾時
        # 寫入 idle_timeout 登出紀錄
        try:
            write_logout_log(uid, "idle_timeout")  # 嘗試記錄登出原因
        except Exception:
            # 不影響主流程
            pass
        # 清除 session
        session.clear()  # 清空整個 session，避免殘留資訊
        return True  # 回報已逾時

    return False  # 未逾時


def validate_name(label, value):
    """
    針對名稱類欄位的通用驗證（例如玩家名稱、隊名等）。

    規則：
    - 必填（空字串或 None 視為錯）
    - 去除前後空白後驗證長度：最多 NAME_MAX 個字（以應用層定義）
    - 僅允許中英文、數字、空白與 . _ -（由 SAFE_PATTERN 驗證，請確認為 Unicode-aware）

    參數：
    - label: 欄位中文標籤，供錯誤訊息使用
    - value: 原始輸入值

    成功：
    - 回傳正規化後的字串（已 strip）

    失敗：
    - 丟出 BadRequest（400），訊息包含對應 label 與規則說明
    """
    v = (value or '').strip()  # 將 None 視為空字串，並去除前後空白
    if not v:  # 檢查必填
        raise BadRequest(f'{label}為必填')
    if len(v) > NAME_MAX:  # 檢查長度上限（以字元數而非位元組）
        raise BadRequest(f'{label}最多 {NAME_MAX} 個字')
    if not SAFE_PATTERN.match(v):  # 以預設安全正則檢查字元集
        raise BadRequest(f'{label}僅允許中英文、數字、空白與 . _ -')
    return v  # 回傳清理後的安全值

# 建議：抽成共用函式，GET/POST 共用
def get_player_profile_by_user_id(user_id):
    pp = db.session.execute(
        db.select(PlayerProfile).where(PlayerProfile.user_id == user_id)
    ).scalar_one_or_none()
    if pp is None:
        return {
            "user_id": user_id,
            "player_name": "未設定",
            "scouting_chances_left": 0,
        }
    return {
        "user_id": pp.user_id,
        "player_name": pp.player_name,
        "scouting_chances_left": pp.scouting_chances_left,
    }

@login_manager.user_loader
def load_user(user_id: str):
    try:
        return User.query.get(int(user_id))
    except Exception:
        return None

# 每次請求刷新永久會話（滑動過期）
@app.before_request
def refresh_session_timeout():
    """
    在每次請求前（Flask 鉤子）若檢測到已登入，將 session 設為永久，
    讓 Flask 採用滑動過期機制（Session 的 TTL 會在活躍期間被延長）。

    注意：
    - session.permanent=True 會套用 app.config['PERMANENT_SESSION_LIFETIME']
    - 建議僅在已登入的情況下設定，避免匿名流量持久化。
    - 靜態資源請求若也走同一應用，可能導致過度續期；可視情況在保護路由內續期即可。
    """
    if session.get("user_id"):  # 僅在已登入的情況下啟用永久會話
        session.permanent = True  # 交由 Flask 的永久會話機制處理 TTL 滑動

@app.before_request
def load_current_user():
    # 可選：略過靜態資源請求，以免做不必要的 DB 查詢
    
    if request.endpoint == "static":
        return

    g.current_user = None
    g.has_team = False

    user_id = session.get("user_id")
    profile = PlayerProfile.query.filter_by(user_id=user_id).first()
    if not user_id:
        return
    if not profile:
        return
    # 依你的 ORM 實作調整
    user = db.session.get(User, user_id)
    g.current_user = user
    team_name = getattr(profile, "team_name", "")
    # 判斷 team_name 是否存在且非空（去除空白）
    if user and team_name:
        g.has_team = True

@app.before_request
def load_player_profile():
    g.player_profile = None
    if current_user.is_authenticated:
        # 視你的模型命名調整
        g.player_profile = PlayerProfile.query.filter_by(user_id=current_user.id).first()

@app.context_processor
def inject_globals():
    return {
        "current_user": getattr(g, "current_user", None),
        "has_team": getattr(g, "has_team", False),
    }

@app.context_processor
def inject_player_profile():
    # 讓所有模板都能拿到 player_profile
    return dict(player_profile=getattr(g, "player_profile", None))

# ------------------------
# 路由（Flask Views）
# ------------------------
"""
路由總覽說明
- 目的：
  - 提供應用的頁面展示與表單提交的 HTTP 端點，包含首頁、登入/註冊/忘記密碼、玩家資料維護、功能分頁、登出等。
- 依賴：
  - Flask: app、request、session、redirect、url_for、render_template、flash
  - DB/ORM: db（SQLAlchemy）、User、LoginLog、LogoutLog、PlayerProfile、func
  - 工具：get_client_ip、hash_password、verify_password、hash_code、verify_code、touch_last_seen、_online_count、common_ctx
  - 設定/常數：ACCOUNT_REGEX、PASSWORD_REGEX、SECOND_CODE_REGEX
  - 時間：datetime.utcnow（更新欄位）、其餘 UTC 基礎在工具函式中完成
- 安全與資料一致性：
  - 所有需要登入的頁面都加上 @login_required 以保護
  - 表單提交皆進行基本格式驗證，敏感資訊如密碼使用安全雜湊（argon2）
  - 使用 get_client_ip 記錄來源 IP，並限制 user-agent 長度避免 DB 欄位溢出
  - DB 提交（commit）失敗時務必 rollback，並回饋使用者錯誤訊息
- 體驗：
  - 使用 flash 與 querystring msg/type 回饋使用者操作結果
  - players 路由在資料不存在時自動建立預設資料，並提示使用者編輯
- 錯誤處理：
  - 讀取 session 避免 KeyError（統一用 session.get）
  - 資料庫查詢與提交採 try/except 包裹，失敗時回滾並提示
"""

# ------------------------
# 共用上下文與工具(起點)
# ------------------------

def common_ctx():
  # 取得當前使用者帳號（若未登入則為 None），供模板顯示 header 等區域用
  account = session.get("account")
  try:
    # 成功登入總數：統計 LoginLog 成功紀錄的數量
    total_login = db.session.query(func.count(LoginLog.id)).filter(LoginLog.success.is_(True)).scalar() or 0
    # 登出總數：統計 LogoutLog 全部紀錄數量
    total_logout = db.session.query(func.count(LogoutLog.id)).scalar() or 0
    # 在線人數：以成功登入數減去登出數，最小值為 0 避免負數
    online_count = max(0, total_login - total_logout)
  except Exception:
    # 若 DB 短暫不可用或查詢失敗，保守回傳 0
    online_count = 0
  # 回傳模板共用 context：account 與 online_count
  return {"account": account, "online_count": online_count}


def _online_count() -> int:
    # 計算線上人數：與 common_ctx 的邏輯一致，抽出獨立函式供少量地方直接呼叫
    total_login = db.session.query(func.count(LoginLog.id))\
                            .filter(LoginLog.success.is_(True))\
                            .scalar() or 0
    total_logout = db.session.query(func.count(LogoutLog.id)).scalar() or 0
    return max(0, total_login - total_logout)

ALLOWED_ROLES = {"ROLE", "STARTER", "GLUE", "SPECIALIST", "STAR"}

def to_int(value, field_name, default=None, min_val=None, max_val=None, unsigned=False, required=False):
    if value is None:
        if required:
            raise ValueError(f"{field_name} is required")
        return default
    try:
        iv = int(value)
    except (TypeError, ValueError):
        if required:
            raise ValueError(f"{field_name} must be an integer")
        return default
    if unsigned and iv < 0:
        raise ValueError(f"{field_name} must be unsigned (>= 0)")
    if min_val is not None and iv < min_val:
        raise ValueError(f"{field_name} must be >= {min_val}")
    if max_val is not None and iv > max_val:
        raise ValueError(f"{field_name} must be <= {max_val}")
    return iv

def validate_role(value, default="ROLE"):
    if value in ALLOWED_ROLES:
        return value
    if value is None:
        return default
    raise ValueError(f"sign_roles invalid: {value}")

def player_profile_to_dict(pp) -> dict:
    if pp is None:
        return None
    return {
        "id": pp.id,
        "user_id": pp.user_id,
        "player_name": pp.player_name,
        "team_name": pp.team_name,
        "arena_name": pp.arena_name,
        "fanpage_name": pp.fanpage_name,
        "team_money": pp.team_money,
        "team_player_count": pp.team_player_count,
        "team_salary": pp.team_salary,
        "arena_seat_count": pp.arena_seat_count,
        "fanclub_staff_count": pp.fanclub_staff_count,
        "fanclub_member_count": pp.fanclub_member_count,
        "scouting_chances_left": pp.scouting_chances_left,
        "league_results_win": pp.league_results_win,
        "league_results_lose": pp.league_results_lose,
        "rookie_league_results_win": pp.rookie_league_results_win,
        "rookie_league_results_lose": pp.rookie_league_results_lose,
        "created_at": pp.created_at,
        "updated_at": pp.updated_at,
        "created_ip": pp.created_ip,
        "user_agent": pp.user_agent,
    }

def player_to_dict(m: PlayersBasic) -> dict:
    return {
        "player_id": getattr(m, "player_id", None),
        "user_id": getattr(m, "user_id", None),
        "player_name": getattr(m, "player_name", None),
        "age": getattr(m, "age", None),
        "height_cm": getattr(m, "height_cm", None),
        "position": getattr(m, "position", None),  # ENUM: 'PG','SG','SF','PF','C'
        # Athletic
        "ath_stamina": getattr(m, "ath_stamina", None),
        "ath_strength": getattr(m, "ath_strength", None),
        "ath_speed": getattr(m, "ath_speed", None),
        "ath_jump": getattr(m, "ath_jump", None),
        # Shooting
        "shot_touch": getattr(m, "shot_touch", None),
        "shot_release": getattr(m, "shot_release", None),
        "shot_accuracy": getattr(m, "shot_accuracy", None),
        "shot_range": getattr(m, "shot_range", None),
        # Defense
        "def_rebound": getattr(m, "def_rebound", None),
        "def_boxout": getattr(m, "def_boxout", None),
        "def_contest": getattr(m, "def_contest", None),
        "def_disrupt": getattr(m, "def_disrupt", None),
        # Offense skills
        "off_move": getattr(m, "off_move", None),
        "off_dribble": getattr(m, "off_dribble", None),
        "off_pass": getattr(m, "off_pass", None),
        "off_handle": getattr(m, "off_handle", None),
        # Talents
        "talent_offiq": getattr(m, "talent_offiq", None),
        "talent_defiq": getattr(m, "talent_defiq", None),
        "talent_health": getattr(m, "talent_health", None),
        "talent_luck": getattr(m, "talent_luck", None),
        # Aggregates/Meta
        "untrainable_sum": getattr(m, "untrainable_sum", None),
        "overall_grade": getattr(m, "overall_grade", None),  # ENUM: G,C,B,A,S,SS,SSR
        "training_points": getattr(m, "training_points", None),
        "created_at": getattr(m, "created_at", None),
        # Salary
        "start_salary": getattr(m, "start_salary", None),
    }

def roster_to_dict(m) -> dict:
    """將單一 TeamRoster ORM 物件轉為字典。"""
    return {
        "id": getattr(m, "id", None),
        "user_id": getattr(m, "user_id", None),
        "player_id": getattr(m, "player_id", None),
        "player_salary": getattr(m, "player_salary", 0),
        "sign_roles": getattr(m, "sign_roles", "ROLE"),
        "contract_years": getattr(m, "contract_years", 1),
        "created_at": getattr(m, "created_at", None),
    }

def merge_player_contract_fields(
    player_list: List[Dict[str, Any]],
    team_player_list: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    將 team_player_list 的 player_salary、sign_roles、contract_years 合併進 player_list。
    以 player_id 對齊。若 team_player_list 找不到對應，預設為 None。
    """
    # 先把 roster（team_player_list）建索引：player_id -> (player_salary, sign_roles, contract_years)
    roster_map: Dict[int, Dict[str, Any]] = {}
    for r in team_player_list:
        pid = r.get("player_id")
        if pid is None:
            continue
        roster_map[pid] = {
            "player_salary": r.get("player_salary"),      # 可能是 None，如果你的查詢目前沒選出
            "sign_roles": r.get("sign_roles"),
            "contract_years": r.get("contract_years"),
        }

    merged = []
    for p in player_list:
        pid = p.get("player_id")
        roster = roster_map.get(pid, {})
        # 建立新 dict，不污染原始資料
        item = {
            **p,
            # 將 roster 欄位合併（若 roster 無此人，預設為 None）
            "player_salary": roster.get("player_salary"),
            "sign_roles": roster.get("sign_roles"),
            "contract_years": roster.get("contract_years"),
        }

        # 若你想 fallback 到 PlayersBasic 的 start_salary 當 player_salary（當 roster 沒值時）
        if item["player_salary"] is None:
            item["player_salary"] = p.get("start_salary")

        merged.append(item)

    return merged

# ------------------------
# 共用上下文與工具(終點))
# ------------------------

# ------------------------
# 展示頁（無提交）
# ------------------------

# 首頁
@app.route("/", methods=['GET'])
@login_required  # 保護首頁需登入，未登入會被導向登入頁（由 login_required 處理）
def index():
    """
    主頁面：顯示基本資訊區塊（帳號、線上人數、玩家資料摘要）
    """
    account = session.get("account")
    user_id = session.get("user_id")

    online_count = _online_count()
    profile = PlayerProfile.query.filter_by(user_id=user_id).first() if user_id else None

    # 預設字串（未登入或無 profile 時）
    league_results = "尚未開始比賽"
    rookie_league_results = "尚未開始比賽"

    if profile:
        # 讀取戰績，若欄位可能為 None，先以 0 取代
        lw = getattr(profile, "league_results_win", 0) or 0
        ll = getattr(profile, "league_results_lose", 0) or 0
        rlw = getattr(profile, "rookie_league_results_win", 0) or 0
        rll = getattr(profile, "rookie_league_results_lose", 0) or 0

        # 聯盟戰績
        total = lw + ll
        if total == 0:
            league_results = "尚未開始比賽"
        else:
            win_rate = round(lw / total * 100, 1)
            league_results = f"{lw} 勝 {ll} 敗（勝率 {win_rate}%）"

        # 新人聯盟戰績
        rtotal = rlw + rll
        if rtotal == 0:
            rookie_league_results = "尚未開始比賽"
        else:
            r_win_rate = round(rlw / rtotal * 100, 1)
            rookie_league_results = f"{rlw} 勝 {rll} 敗（勝率 {r_win_rate}%）"

    return render_template(
        "index.html",
        account=(profile.player_name if profile and getattr(profile, "player_name", None) else None),
        online_count=online_count,
        profile=profile,
        league_results=league_results,
        rookie_league_results=rookie_league_results,
    )


# 登入頁（GET）
@app.route("/login", methods=["GET"])
def login_page():
    # 回傳登入頁模板，頁面內可讀取 querystring 的 msg/type 顯示提示
    return render_template("login.html")

# 註冊頁（GET）
@app.route("/register", methods=["GET"])
def register_page():
    # 回傳註冊頁模板
    return render_template("register.html")

# 忘記密碼頁（GET，占位）
@app.route("/forgot", methods=["GET"])
def forgot_page():
    # 回傳忘記密碼頁模板
    return render_template("forgot.html")

@app.route("/success", methods=["GET"])
def success_page():
    # 通用成功頁，重設密碼等流程完成後可導向此頁
    return render_template("success.html")

@app.route("/error", methods=["GET"])
def error_page():
    # 自訂錯誤頁，可經由 ?msg= 傳入錯誤訊息，預設為「發生錯誤」
    msg = request.args.get("msg", "發生錯誤")  # 取得 querystring 的 msg 參數
    return f"<h1>錯誤</h1><p>{msg}</p>", 400  # 回傳簡單 HTML 與 400 狀態碼

# 儀表板頁（範例）
@app.route("/dashboard")
@login_required
def dashboard():
    account = session.get("account")  # 取目前登入帳號
    # 使用最簡 HTML 直接返回，開發時可改用模板
    return f"<h1>會員主頁</h1><p>歡迎，{account or ''}</p>"

# --- 功能頁路由（各分頁） ---
# 備註：目前每頁皆使用對應模板（teams.html 等），共用 context 由 common_ctx 提供基本資訊

@app.get("/teams")
@login_required
def teams():
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))
    # 取 PlayerProfile（若無則給預設）
    pp = db.session.execute(
        db.select(PlayerProfile).where(PlayerProfile.user_id == user_id)
    ).scalar_one_or_none()

    if pp is None:
        player_profile = {
            "user_id": user_id,
            "player_name": "未設定",
            "scouting_chances_left": 0,
            "team_player_count": 0,
            "team_salary": 0,
        }
    else:
        player_profile = player_profile_to_dict(pp)
    stmt = (
        select(PlayersBasic)
        .join(TeamRoster, TeamRoster.player_id == PlayersBasic.player_id)
        .where(TeamRoster.user_id == user_id)  # 若要限定特定使用者
    )
    rows = db.session.execute(stmt).scalars().all()
    player_list = [player_to_dict(m) for m in rows]
    stmt = (
        select(TeamRoster)
        .where(TeamRoster.user_id == user_id)  # 若要限定特定使用者
    )
    rows = db.session.execute(stmt).scalars().all()
    team_player_list = [roster_to_dict(m) for m in rows]
    players_signed = merge_player_contract_fields(player_list, team_player_list)
    return render_template(
    "teams.html",
    player_profile=player_profile,
    players_signed=players_signed,
    **common_ctx()
    )

@app.get("/assets")
@login_required
def assets():
  return render_template("assets.html", **common_ctx())

@app.get("/leagues")
@login_required
def leagues():
  return render_template("leagues.html", **common_ctx())

@app.get("/schedules")
@login_required
def schedules():
  return render_template("schedules.html", **common_ctx())

@app.get("/scouts")
@login_required
def scouts():
    flashed = get_flashed_messages(with_categories=True)

    signed_from_flash = []
    for i, item in enumerate(flashed):
        # item 應該是 (category, message)
        try:
            cat, msg = item
        except Exception as e:
            print("[GET /scouts] unexpected flash item format:", item, "error:", e, flush=True)
            continue

        if cat == "success" and isinstance(msg, dict) and "signed_players" in msg:
            signed_from_flash = msg["signed_players"]
            # 不一定要 break，如果你確定只會有一筆可 break
            break

    if not signed_from_flash:
        print("[GET /scouts] no signed_from_flash found", flush=True)
    user_id = session.get("user_id")
    if not user_id:
        return redirect(url_for("login"))

    # 取 PlayerProfile（若無則給預設）
    pp = db.session.execute(
        db.select(PlayerProfile).where(PlayerProfile.user_id == user_id)
    ).scalar_one_or_none()

    if pp is None:
        player_profile = {
            "user_id": user_id,
            "player_name": "未設定",
            "scouting_chances_left": 0,
            "team_player_count": 0,
            "team_salary": 0,
        }
    else:
        player_profile = {
            "user_id": pp.user_id,
            "player_name": pp.player_name,
            "scouting_chances_left": pp.scouting_chances_left,
            "team_player_count": pp.team_player_count,
            "team_salary": pp.team_salary,
        }

    # 未簽約名單（擇一版本）
    exists_signed = (
        select(TeamRoster.player_id)
        .where(TeamRoster.user_id == user_id)
        .where(TeamRoster.player_id == PlayersBasic.player_id)
    )

    unsigned_stmt = (
        db.select(PlayersBasic)
        .where(PlayersBasic.user_id == user_id)
        .where(~exists(exists_signed))
        .order_by(PlayersBasic.player_id.asc())
    )
    unsigned_players = db.session.execute(unsigned_stmt).scalars().all()

    # 分組：剛探來 vs 之前探的（都只在未簽約清單內）
    prev_scouted = []
    for p in unsigned_players:
        prev_scouted.append(p)

    ctx = dict(
        **common_ctx(),
        player_profile=player_profile,
        unsigned_players=unsigned_players,
        prev_scouted=prev_scouted,
        signed_from_flash=signed_from_flash,
    )
    return render_template("scouts.html", **ctx)

@app.post("/scouts")
@login_required
def scouts_post():
    # 取得目前登入者
    user_id = session.get("user_id")
    pp = db.session.execute(
        db.select(PlayerProfile).where(PlayerProfile.user_id == user_id)
    ).scalar_one_or_none()
    if pp is None:
        player_profile = {
            "user_id": user_id,
            "player_name": "未設定",
            "scouting_chances_left": 0,
        }
    else:
        player_profile = {
            "user_id": pp.user_id,
            "player_name": pp.player_name,
            "scouting_chances_left": pp.scouting_chances_left,
            "team_player_count": pp.team_player_count,
            "team_salary": pp.team_salary,

        }
    # 優先支援「測試按鈕」情境：讀取 URL 的 action、times
    action = request.args.get("action")
    times_raw = request.form.get('times')

    if action == "probe":
        # 有玩家檔案，帶出剩餘次數
        scouting_left = int(pp.scouting_chances_left or 0)
        # 解析 times，可為 1、10、"all"
        if times_raw == "10":
            times = min(10, scouting_left)
        elif times_raw == "ALL":
            # 使用玩家剩餘的探查次數
            times = max(0, scouting_left)
        elif times_raw == "1":
            # 使用玩家剩餘的探查次數
            times = min(1, scouting_left)
        else:
            try:
                times = 0
            except ValueError:
                times = 0

        # 若剩餘次數不足或 times 為 0，直接回應
        if times <= 0:
            player_profile = {
                "user_id": pp.user_id,
                "player_name": pp.player_name,
                "scouting_chances_left": scouting_left,
                "team_player_count": pp.team_player_count,
                "team_salary": pp.team_salary,
            }
            ctx = dict(
                **common_ctx(),
                player_profile=player_profile,
                probe_feedback="剩餘探查次數為 0，無法探查。",
                new_players=[],
            )
            return render_template("scouts.html", **ctx)

        new_players = []
        success = 0

        for _ in range(times):
            try:
                out = generate_and_persist(count=1, user_id=user_id, insert=False)  # 單筆插入可直接拿到 id
                new_players.append(out)
                success += out.get("total_inserted", 0)
            except Exception as e:
                print(f"[scouts_post] generate_and_persist error: {e}", flush=True)
        # 成功幾位就扣幾次（若你希望不論成功與否都扣，也可改為扣 times）
        if success > 0:
            try:
                pp.scouting_chances_left = max(0, scouting_left - success)
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                print(f"[scouts_post] commit error: {e}", flush=True)

        def to_result_row(p):
            def get(obj, key, default=None):
                if isinstance(obj, dict):
                    return obj.get(key, default)
                return getattr(obj, key, default)
            return {
                "player_id": get(p, "player_id", None),  # 不再 fallback 到 "id"
                "player_name": get(p, "player_name", "未知"),
                "height_cm": get(p, "height_cm", None),
                "position": get(p, "position", None),
                "overall_grade": get(p, "overall_grade", None),
                "shot_accuracy": get(p, "shot_accuracy", None),
                "shot_range": get(p, "shot_range", None),
                "def_rebound": get(p, "def_rebound", None),
                "def_boxout": get(p, "def_boxout", None),
                "def_contest": get(p, "def_contest", None),
                "def_disrupt": get(p, "def_disrupt", None),
                "off_move": get(p, "off_move", None),
                "off_dribble": get(p, "off_dribble", None),
                "off_pass": get(p, "off_pass", None),
                "off_handle": get(p, "off_handle", None),
                "start_salary": get(p, "start_salary", None),
            }

        instances = []
        for batch in new_players:
            # 直接用 preview，每個元素都應帶 player_id
            for p in batch.get("preview", []):
                instances.append(p)

        results = [to_result_row(p) for p in instances]

        # 修正這裡，不再用 results['player_id']
        last_scout_ids = [r["player_id"] for r in results if r.get("player_id") is not None]
        last_scout_ids_set = set(last_scout_ids)

        # player_profile 區塊維持
        player_profile = {
            "user_id": pp.user_id,
            "player_name": pp.player_name,
            "scouting_chances_left": pp.scouting_chances_left,
        }
        # 傳給模板：把 results 傳入，show_more 視需求加入
        # 未簽約名單（擇一版本）
        exists_signed = (
            select(TeamRoster.player_id)
            .where(TeamRoster.user_id == user_id)
            .where(TeamRoster.player_id == PlayersBasic.player_id)
        )

        unsigned_stmt = (
            db.select(PlayersBasic)
            .where(PlayersBasic.user_id == user_id)
            .where(~exists(exists_signed))
            .order_by(PlayersBasic.player_id.asc())
        )
        unsigned_players = db.session.execute(unsigned_stmt).scalars().all()

        # 分組：剛探來 vs 之前探的（都只在未簽約清單內）
        just_scouted = []
        prev_scouted = []
        for p in unsigned_players:
            if p.player_id in last_scout_ids_set:
                just_scouted.append(p)
            else:
                prev_scouted.append(p)

        ctx = dict(
            **common_ctx(),
            player_profile=player_profile,
            results=results,                # 如果你也要顯示本次探查的結果表
            just_scouted=just_scouted,
            prev_scouted=prev_scouted,
            unsigned_players=unsigned_players,  # 關鍵：加入這個
            show_more=False,
        )
        return render_template("scouts.html", **ctx)
    elif action == "sign":
        # 取被選取的 IDs（字串列表）
        sign_ids = request.form.getlist("sign_ids")
        sign_player_list = []
        print("[sign] selected only:")
        print(f"  sign_ids = {sign_ids}", flush=True)

        # 逐一印出每個被選 ID 的 role 與 year
        for sid in sign_ids:
            user_id = to_int(user_id, "user_id", required=True)
            player_id = to_int(sid, "player_id", required=True)
            role = request.form.get(f"sign_roles[{sid}]")
            sign_role = validate_role(role, default="ROLE")
            years_raw = request.form.get(f"sign_year[{sid}]")
            contract_years = to_int(years_raw, "contract_years", default=1, unsigned=True, min_val=1, max_val=5)
            print(f"  sign_roles[{sid}] = {sign_role}", flush=True)
            print(f"  sign_year[{sid}] = {contract_years}", flush=True)
            print(f"  user_id = {user_id}", flush=True)
            print(f"  player_id = {player_id}", flush=True)
            exists_signed = (
            select(PlayersBasic.start_salary)
            .where(PlayersBasic.user_id == user_id)
            .where(PlayersBasic.player_id == player_id)
            )
            start_salary = db.session.execute(exists_signed).scalars().first()
            player_salary = to_int(start_salary, "player_salary", default=0, unsigned=True)
            print(f"  player_salary = {player_salary}", flush=True)
            # ...你前面的 print 與 player_salary 取得都完成之後，接著 upsert：
            stmt = mysql_insert(TeamRoster.__table__).values(
                user_id=user_id,
                player_id=player_id,
                player_salary=player_salary,
                sign_roles=sign_role,
                contract_years=contract_years,
                # 若你想要由應用端指定時間，改用 datetime.utcnow()；否則可完全不帶，讓 DB 用 DEFAULT
                # created_at=datetime.utcnow()
            )

            # on duplicate key 只更新可變欄位，不動 created_at
            stmt = stmt.on_duplicate_key_update(
                player_salary=stmt.inserted.player_salary,
                sign_roles=stmt.inserted.sign_roles,
                contract_years=stmt.inserted.contract_years
                # created_at 不更新，保留第一次寫入的時間
            )
            db.session.execute(stmt)
            
            exists_signed = (
            select(PlayersBasic.player_name)
            .where(PlayersBasic.user_id == user_id)
            .where(PlayersBasic.player_id == player_id)
            )
            player_name = db.session.execute(exists_signed).scalars().first()
            sign_player_list.append(player_name)
        # 批次一次提交
        db.session.commit()
        print(f"簽約成功提交，sign_player_list={sign_player_list}", flush=True)
        if sign_player_list:
            flash({"signed_players": sign_player_list}, "success")
            print("flash:", {"signed_players": sign_player_list}, "success", flush=True)
        else:
            flash("已提交簽約，但沒有有效的球員名單。", "warning")

    return redirect(url_for("scouts"))

@app.get("/market")
@login_required
def market():
  return render_template("market.html", **common_ctx())

@app.get("/tactics")
@login_required
def tactics():
  return render_template("tactics.html", **common_ctx())

@app.get("/trades")
@login_required
def trades():
  return render_template("trades.html", **common_ctx())

@app.get("/community")
@login_required
def community():
  return render_template("community.html", **common_ctx())

@app.get("/guide")
@login_required
def guide():
  return render_template("guide.html", **common_ctx())

@app.route("/profile/edit")
@login_required
def profile_edit():
    # 直接導到現有的設定頁
    return render_template("players_setup.html", **common_ctx())

# ------------------------
# 玩家資料頁與設定
# ------------------------

@app.route("/players", methods=["GET"])
@login_required
def players():
    # 讀取登入狀態（理論上經過 @login_required 已保證存在，但此檢查更健壯）
    user_id = session.get('user_id')  # 從 session 取 user_id
    if not user_id:  # 若沒有 user_id，視為未登入
        print("if not user_id:", flush=True)  # 於伺服器即時輸出診斷訊息
        return redirect(url_for('login'))  # 導向登入（若無此端點可改 login_page）

    # 查詢既有玩家資料（以 user_id 唯一）
    profile = PlayerProfile.query.filter_by(user_id=user_id).first()

    # 若無資料，建立預設資料並儲存
    if not profile:
        try:
            # 以 session 中的帳號前 8 字作為預設玩家名，若無帳號則使用「玩家」
            default_player_name = (session.get("account") or "玩家")[:8]
            # 建立 PlayerProfile 物件，填入合理的預設值
            profile = PlayerProfile(
                user_id=user_id,  # 關聯目前使用者
                player_name=default_player_name,  # 預設玩家名稱
                team_name="我的球隊",            # 預設球隊名稱
                arena_name="主場館",             # 預設場館名稱
                fanpage_name="粉絲團",           # 預設粉絲團名稱
                created_ip=get_client_ip(),      # 建立時來源 IP
                user_agent=(request.headers.get("User-Agent") or "")[:256],  # 限長 UA
            )
            db.session.add(profile)  # 加入待提交
            db.session.commit()      # 提交至資料庫
            flash("已為您建立預設的玩家資訊，請點選編輯完善資料。", "success")  # 成功提示
        except Exception as e:
            db.session.rollback()  # 任一錯誤回滾交易
            flash(f"建立玩家資訊失敗：{e}", "danger")  # 顯示錯誤訊息
            # 即使失敗，仍回到資料頁，模板會顯示「尚未建立」的狀態
            return render_template(
                "players.html",
                profile=None,
                account=session.get("account"),
                online_count=_online_count(),
            )

    # 有資料（或剛建立成功）→ 顯示玩家資訊頁
    return render_template(
        "players.html",
        profile=profile,  # 傳入玩家資料
        account=(profile.player_name if profile and getattr(profile, "player_name", None) else None),  # 傳入帳號
        online_count=_online_count(),    # 傳入線上人數
    )


@app.route('/players/setup', methods=['GET', 'POST'])
@login_required
def players_setup():
    user_id = session.get('user_id')  # 取得使用者 ID
    if not user_id:  # 再次防呆，未登入則導向登入頁
        return redirect(url_for('login'))

    # 先查既有的 profile（unique: user_id）
    profile = PlayerProfile.query.filter_by(user_id=user_id).first()

    if request.method == "POST":  # 表單送出邏輯
        # 取得表單欄位，並去除前後空白
        player_name = (request.form.get("player_name") or "").strip()
        team_name   = (request.form.get("team_name") or "").strip()
        arena_name  = (request.form.get("arena_name") or "").strip()
        fanpage_name= (request.form.get("fanpage_name") or "").strip()

        # 簡單驗證：每欄必填、長度 <= 8（你可依需求調整）
        def too_long(s: str) -> bool:
            # 使用 len 作為簡化的字數衡量；若需更精準可用 grapheme 切分
            return len(s) > 8

        errors = []  # 收集錯誤訊息
        for label, value in [
            ("玩家名稱", player_name),
            ("球隊名稱", team_name),
            ("場館名稱", arena_name),
            ("粉絲團名稱", fanpage_name),
        ]:
            if not value:  # 必填檢查
                errors.append(f"{label}為必填")
            elif too_long(value):  # 長度檢查
                errors.append(f"{label}最多 8 字")

        if errors:
            # 若有錯誤，逐條提示
            for e in errors:
                flash(e, "danger")
            # 帶原值回去方便使用者修正
            return render_template("players_setup.html",
                                   account=session.get("account"),
                                   online_count=_online_count(),
                                   form={
                                       "player_name": player_name,
                                       "team_name": team_name,
                                       "arena_name": arena_name,
                                       "fanpage_name": fanpage_name,
                                   },
                                   profile=profile)

        try:
            if profile:
                # 更新既有資料
                profile.player_name = player_name
                profile.team_name = team_name
                profile.arena_name = arena_name
                profile.fanpage_name = fanpage_name
                profile.updated_at = now_taipei()  # 更新時間使用 UTC
                profile.user_agent = (request.headers.get("User-Agent") or "")[:256]  # 記錄最新 UA
            else:
                # 新增資料（第一次設定）
                profile = PlayerProfile(
                    user_id=user_id,
                    player_name=player_name,
                    team_name=team_name,
                    arena_name=arena_name,
                    fanpage_name=fanpage_name,
                    # 來自 .env 的初始值（新增）
                    team_money=PLAYER_DEFAULTS["team_money"],
                    team_player_count=PLAYER_DEFAULTS["team_player_count"],
                    team_salary=PLAYER_DEFAULTS["team_salary"],
                    arena_seat_count=PLAYER_DEFAULTS["arena_seat_count"],
                    fanclub_staff_count=PLAYER_DEFAULTS["fanclub_staff_count"],
                    fanclub_member_count=PLAYER_DEFAULTS["fanclub_member_count"],
                    scouting_chances_left=PLAYER_DEFAULTS["scouting_chances_left"],
                    league_results_win=PLAYER_DEFAULTS["league_results_win"],
                    league_results_lose=PLAYER_DEFAULTS["league_results_lose"],
                    rookie_league_results_win=PLAYER_DEFAULTS["rookie_league_results_win"],
                    rookie_league_results_lose=PLAYER_DEFAULTS["rookie_league_results_lose"],
                    created_ip=get_client_ip(),  # 首次建立來源 IP
                    user_agent=(request.headers.get("User-Agent") or "")[:256],
                )
                db.session.add(profile)  # 新增到 session

            db.session.commit()  # 提交更新/新增
            flash("玩家資訊已儲存", "success")  # 成功提示
            return redirect(url_for("index"))  # 返回首頁或改回 players 頁都可以
        except Exception as e:
            db.session.rollback()  # 失敗回滾
            flash(f"儲存失敗：{e}", "danger")  # 錯誤提示
            # 保留使用者輸入回填表單，避免重填
            return render_template("players_setup.html",
                                   account=session.get("account"),
                                   online_count=_online_count(),
                                   form={
                                       "player_name": player_name,
                                       "team_name": team_name,
                                       "arena_name": arena_name,
                                       "fanpage_name": fanpage_name,
                                   },
                                   profile=profile)

    # GET：顯示設定表單（若已有資料，預填現有值）
    return render_template("players_setup.html",
                           account=session.get("account"),
                           online_count=_online_count(),
                           form={
                               "player_name": getattr(profile, "player_name", ""),  # 安全取得屬性
                               "team_name": getattr(profile, "team_name", ""),
                               "arena_name": getattr(profile, "arena_name", ""),
                               "fanpage_name": getattr(profile, "fanpage_name", ""),
                           },
                           profile=profile)


# ------------------------
# 提交邏輯（POST）
# ------------------------

# 註冊提交
@app.route("/register", methods=["POST"])
def register_submit():
    # 支援 JSON 或 Form，但主要預期為傳統表單提交
    if request.is_json:
        data = request.get_json(silent=True) or {}  # 安全取得 JSON，失敗則給空 dict
        account = (data.get("account") or "").strip()        # 取得帳號並去除空白
        password = data.get("password") or ""                # 密碼保留原字元，避免 strip 破壞空白檢查
        second_code_plain = (data.get("second_code") or "").strip()  # 二次驗證碼
    else:
        account = (request.form.get("account") or "").strip()        # 表單帳號
        password = request.form.get("password") or ""                # 表單密碼
        second_code_plain = (request.form.get("second_code") or "").strip()  # 表單二次驗證碼

    # 格式驗證：帳號、密碼、二次驗證碼
    if not account or not ACCOUNT_REGEX.match(account):  # 帳號需符合 3~100 英數與 ._-
        return redirect(url_for("register_page", msg="帳號格式不符（3~100 位英數與 ._-）", type="error"))
    if not password or not PASSWORD_REGEX.match(password):  # 密碼 8~12 位，需同時有字母與數字，且不可含空白
        return redirect(url_for("register_page", msg="密碼需 8~12 位且同時包含英文字母與數字，且不可含空白", type="error"))
    if not second_code_plain or not SECOND_CODE_REGEX.match(second_code_plain):  # 二次驗證碼 6 位數字
        return redirect(url_for("register_page", msg="二次驗證碼需為 6 位數字", type="error"))

    # 檢查帳號是否已存在
    exists = User.query.filter_by(account=account).first()
    if exists:
        return redirect(url_for("register_page", msg="帳號已存在", type="error"))

    # 建立使用者：雜湊密碼、雜湊二次驗證碼、紀錄註冊 IP
    user = User(
        account=account,
        password_hash=hash_password(password),          # 安全雜湊密碼（argon2）
        second_code=hash_code(second_code_plain),       # HMAC 雜湊短碼（Base64）
        register_ip=get_client_ip(),                    # 來源 IP
    )
    db.session.add(user)    # 加入待提交
    db.session.commit()     # 提交

    # 導回登入頁並提示註冊成功
    return redirect(url_for("login_page", msg="註冊成功，請登入", type="success"))


# 登入提交
@app.route("/login", methods=["POST"])
def login_submit():
    account = (request.form.get("account") or "").strip()  # 取得帳號（去除空白）
    password = request.form.get("password") or ""          # 取得密碼（不 strip）

    user = User.query.filter_by(account=account).first()   # 依帳號查詢使用者
    ok = False                                             # 預設登入結果為失敗
    if user and verify_password(password, user.password_hash):  # 若找到使用者且密碼驗證通過
        ok = True

    # 記錄登入紀錄（無論成功與否都記錄）
    log = LoginLog(
        user_id=user.id if user else 0,                        # 找不到 user 時記 0 以利追蹤
        ip=get_client_ip(),                                    # 來源 IP
        success=ok,                                            # 登入是否成功
        user_agent=(request.headers.get("User-Agent") or "")[:512]  # 限長 UA
    )
    db.session.add(log)  # 新增登入紀錄
    db.session.commit()  # 提交紀錄

    if not ok:
        # 登入失敗：導回登入頁並提示錯誤
        return redirect(url_for("login_page", msg="登入失敗，帳號或密碼錯誤", type="error"))

    # 登入成功：寫入 session 並續期 last_seen
    session["account"] = user.account  # 記錄帳號供頁面顯示
    session["user_id"] = user.id       # 記錄 user_id 供權限與資料查詢
    touch_last_seen()                  # 更新 last_seen 以支援滑動過期
    return redirect(url_for("index"))  # 導向首頁


@app.route("/forgot", methods=["POST"])
def forgot_submit():
    """
    重設密碼（簡化流程）：
    - 接收 account、second_code、新密碼
    - 驗證格式與 second_code 正確後，直接更新密碼
    - 完成後導向 success_page
    """
    account = (request.form.get("account") or "").strip()             # 取得帳號
    second_code_plain = (request.form.get("second_code") or "").strip()# 取得二次驗證碼
    new_password = request.form.get("new_password") or ""             # 取得新密碼（不 strip）

    # 基本格式驗證
    if not account or not ACCOUNT_REGEX.match(account):
        return redirect(url_for("forgot_page", msg="帳號格式不符", type="error"))
    if not second_code_plain or not SECOND_CODE_REGEX.match(second_code_plain):
        return redirect(url_for("forgot_page", msg="二次驗證碼需為 6 位數字", type="error"))
    if not new_password or not PASSWORD_REGEX.match(new_password):
        return redirect(url_for("forgot_page", msg="新密碼需 8~12 位且同時包含英文字母與數字，且不可含空白", type="error"))

    user = User.query.filter_by(account=account).first()  # 查找使用者
    if not user:
        return redirect(url_for("forgot_page", msg="帳號不存在", type="error"))

    # 驗證二次驗證碼（以 HMAC 雜湊比對，常數時間比較）
    if not verify_code(second_code_plain, user.second_code):
        return redirect(url_for("forgot_page", msg="二次驗證碼不正確", type="error"))

    # 更新密碼雜湊並提交
    user.password_hash = hash_password(new_password)  # 以 argon2 重新雜湊新密碼
    db.session.commit()  # 提交更新

    # 導向成功頁
    return redirect(url_for("success_page"))


# ------------------------
# 登出
# ------------------------

@app.route("/logout", methods=["POST", "GET"])
def logout():
    """
    手動登出：
    - 若已登入，先寫入登出紀錄（manual）
    - 清空 session
    - 導回首頁（首頁受 @login_required 保護，未登入會要求重新登入）
    """
    uid = session.get("user_id")  # 取得目前登入使用者 ID
    if uid:  # 若存在，先寫入登出紀錄
        write_logout_log(uid, "manual")
    session.clear()  # 清除所有 session 資訊
    return redirect(url_for("index"))  # 導回首頁（未登入會被導向登入頁）

# ------------------------
# 啟動輔助（本機開發啟動入口）
# ------------------------
"""
區塊總覽
- 目的：
  - 當此檔案被「直接執行」時，清空終端並啟動 Flask 內建開發伺服器。
- 使用情境：
  - 本機開發階段，直接 `python app.py`（或你的主檔名）執行，啟用 debug 模式與熱重載。
- 依賴：
  - os：讀取環境變數與執行終端指令（清空畫面）
  - app：Flask 應用實例（需事先建立並配置）
- 安全與部署注意：
  - Flask 內建伺服器僅用於開發；正式環境請改用 WSGI/ASGI 伺服器（如 gunicorn/uWSGI/uvicorn）。
  - debug=True 會啟用互動偵錯器與自動重載，切勿在外網生產環境啟用。
  - 清空終端指令可能因權限或環境差異失敗，已用 try/except 防呆。
"""

if __name__ == "__main__":                                 # 只有當此檔案被「直接執行」時才會進入此區塊；若被其他模組 import，這段不會執行
    # 啟動前清空終端：Windows 用 cls，其它用 clear
    try:                                                   # 使用例外處理，避免在不支援或權限不足的環境中因清空失敗而中斷啟動流程
        os.system("cls" if os.name == "nt" else "clear")   # 根據作業系統選擇清空終端的指令：
                                                           # - Windows：cls
                                                           # - Unix/Linux/macOS：clear
    except Exception:                                      # 捕捉所有可能例外（例如找不到指令、沒有執行權限）
        pass                                               # 靜默忽略清空失敗，不影響後續伺服器啟動

    # 只在本機開發使用，正式環境請用 WSGI/ASGI 伺服器
    app.run(                                               # 啟動 Flask 內建開發伺服器（單進程、單線程，不適合生產）
        host="0.0.0.0",                                    # 監聽所有網路介面（在同網段或容器環境中，其他裝置也可連線測試）
        port=int(os.getenv("PORT", "5000")),               # 以環境變數 PORT 決定埠號；若未設定則使用 5000。用 int 轉型避免字串造成錯誤
        debug=True                                         # 啟用除錯模式：自動熱重載、詳細錯誤頁與互動偵錯器（只限本機開發）
    )
