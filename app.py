from datetime import datetime, timedelta, timezone
import os
import re
import secrets
import hmac
import hashlib
import base64
from functools import wraps
from flask import (
    Flask, request, jsonify, render_template, redirect, url_for, session, flash
)
from flask_sqlalchemy import SQLAlchemy
from passlib.hash import argon2
from sqlalchemy.orm import relationship
from sqlalchemy import func
import threading

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

app = Flask(__name__)
app.config["JSON_AS_ASCII"] = False
app.secret_key = "change-this-to-a-secure-random-key"

MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = os.getenv("MYSQL_PORT", "3306")
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_DB = os.getenv("MYSQL_DB")
SECRET_KEY = os.getenv("SECRET_KEY")
APP_SALT = os.getenv("APP_SALT")

missing = [k for k, v in {
    "MYSQL_HOST": MYSQL_HOST,
    "MYSQL_USER": MYSQL_USER,
    "MYSQL_PASSWORD": MYSQL_PASSWORD,
    "MYSQL_DB": MYSQL_DB,
    "SECRET_KEY": SECRET_KEY,
    "APP_SALT": APP_SALT,
}.items() if not v]
if missing:
    raise RuntimeError(f"缺少環境變數：{', '.join(missing)}。請在環境或 .env 設定。")

# 基礎設定
app.config["SECRET_KEY"] = SECRET_KEY
DATABASE_URL = f"mysql+pymysql://{MYSQL_USER}:{MYSQL_PASSWORD}@{MYSQL_HOST}:{MYSQL_PORT}/{MYSQL_DB}"
app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# 伺服器端閒置登出：10 分鐘滑動過期
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=10)

db = SQLAlchemy(app)

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    account = db.Column(db.String(100), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    second_code = db.Column("second_code_hash", db.String(255), nullable=False)
    register_ip = db.Column(db.String(45), nullable=False)
    register_at = db.Column(db.TIMESTAMP, nullable=False, server_default=db.text("CURRENT_TIMESTAMP"))
    logs = relationship("LoginLog", back_populates="user", cascade="all, delete-orphan")

class LoginLog(db.Model):
    __tablename__ = "login_log"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"), nullable=False)
    ip = db.Column(db.String(45), nullable=False)
    success = db.Column(db.Boolean, nullable=False)
    user_agent = db.Column(db.String(512))
    created_at = db.Column(db.TIMESTAMP, server_default=db.text("CURRENT_TIMESTAMP"))
    user = relationship("User", back_populates="logs")

class LogoutLog(db.Model):
    __tablename__ = "logout_log"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("users.id", ondelete="CASCADE", onupdate="CASCADE"),
        nullable=False
    )
    ip = db.Column(db.String(45), nullable=False)
    reason = db.Column(
        db.Enum("manual", "idle_timeout", "forced", "session_invalid", "other", name="logout_reason"),
        nullable=False,
        server_default="manual"
    )
    user_agent = db.Column(db.String(512))
    created_at = db.Column(db.TIMESTAMP, server_default=db.text("CURRENT_TIMESTAMP"))

# 工具函式
def get_client_ip() -> str:
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "0.0.0.0"
    if isinstance(ip, str) and "," in ip:
        ip = ip.split(",")[0].strip()
    return ip

def generate_6digit_code() -> str:
    return "".join(secrets.choice("0123456789") for _ in range(6))

def hash_password(plain: str) -> str:
    return argon2.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    try:
        return argon2.verify(plain, hashed)
    except Exception:
        return False

def hash_code(plain: str) -> str:
    digest = hmac.new(APP_SALT.encode("utf-8"), plain.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")

def verify_code(plain: str, hashed: str) -> bool:
    digest = hmac.new(APP_SALT.encode("utf-8"), plain.encode("utf-8"), hashlib.sha256).digest()
    calc = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(calc, hashed)

def write_logout_log(user_id: int, reason: str):
    """
    安全寫入 logout_log，失敗不影響主流程。
    reason: "manual" | "idle_timeout" | "forced" | "session_invalid" | "other"
    """
    if not user_id:
        return
    try:
        db.session.add(LogoutLog(
            user_id=user_id,
            ip=get_client_ip(),
            reason=reason,
            user_agent=(request.headers.get("User-Agent") or "")[:512]
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()

def login_required(view_func):
    """
    - 若 session 有 user_id：允許通行（先檢查 idle_timeout）
    - 若沒有，但帶有我們系統曾經設定過的 cookie/痕跡，可視為 session_invalid
    - 若有閒置時間超過上限（記錄 last_seen），視為 idle_timeout
    """
    print("login_required", flush=True)
    from functools import wraps
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        uid = session.get("user_id")
        # 先檢查是否存在我們的 session cookie（用於後續記錄 session_invalid）
        try:
            has_app_session_cookie = bool(request.cookies.get(app.session_cookie_name))
        except Exception:
            has_app_session_cookie = False
        if uid:
            print("if uid:", flush=True)
            # 先檢查是否閒置逾時（逾時：已記錄並清除 session）
            if check_idle_timeout_and_record():
                print("if check_idle_timeout_and_record():", flush=True)
                if has_app_session_cookie:
                    print("if has_app_session_cookie:", flush=True)
                    try:
                        ip = get_client_ip()
                        ua = (request.headers.get("User-Agent") or "")[:512]
                        since = datetime.now(timezone.utc)  - timedelta(hours=24)
                        last = (LoginLog.query
                                .filter(LoginLog.ip == ip)
                                .filter(LoginLog.user_agent == ua)
                                .filter(LoginLog.success.is_(True))
                                .filter(LoginLog.created_at >= since)
                                .order_by(LoginLog.created_at.desc())
                                .first())
                        if last and last.user_id:
                            write_logout_log(last.user_id, "session_invalid")
                    except Exception:
                        print("if has_app_session_cookie:except Exception", flush=True)
                        pass               
                return redirect(url_for("login_page", msg="已因閒置逾時登出，請重新登入", type="error"))
            # 未逾時則滑動續期
            touch_last_seen()
            return view_func(*args, **kwargs)    
        # 未登入：若有 cookie，記錄 session_invalid（可選）
        if has_app_session_cookie:
            print("if has_app_session_cookie:", flush=True)
            try:
                ip = get_client_ip()
                ua = (request.headers.get("User-Agent") or "")[:512]
                since = datetime.now(timezone.utc)  - timedelta(hours=24)
                last = (
                    LoginLog.query
                    .filter(LoginLog.ip == ip)
                    .filter(LoginLog.user_agent == ua)
                    .filter(LoginLog.success.is_(True))
                    .filter(LoginLog.created_at >= since)
                    .order_by(LoginLog.created_at.desc())
                    .first()
                )
                if last and last.user_id:
                    write_logout_log(last.user_id, "session_invalid")
            except Exception:
                pass

        return redirect(url_for("login_page"))    
    return wrapper

IDLE_MINUTES = 10

def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())

def touch_last_seen():
    session["last_seen"] = _now_ts()
    print(f"touch_last_seen: set last_seen={session['last_seen']}", flush=True)

def check_idle_timeout_and_record() -> bool:
    """
    回傳：
    - True  => 已逾時，且已寫入登出紀錄並清除 session
    - False => 未逾時（同時會幫你在外部呼叫 touch_last_seen 續期）
    注意：不要在這裡續期，讓呼叫端在確認未逾時後再 touch_last_seen()
    """
    print("check_idle_timeout_and_record", flush=True)
    uid = session.get("user_id")
    last_seen_ts = session.get("last_seen")
    if not uid or not last_seen_ts:
        # 未登入或缺 last_seen，不在此流程判斷為逾時
        return False

    try:
        last_seen = int(last_seen_ts)
    except Exception:
        # 解析失敗視為缺值，交由呼叫端續期
        return False

    now = int(datetime.now(timezone.utc).timestamp()) 
    idle_seconds = now - last_seen
    if idle_seconds >= IDLE_MINUTES * 60:
        # 寫入 idle_timeout 登出紀錄
        try:
            write_logout_log(uid, "idle_timeout")
        except Exception:
            pass
        # 清除 session
        session.clear()
        return True

    return False

PASSWORD_REGEX = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)[^\s]{8,12}$")
ACCOUNT_REGEX = re.compile(r"^[A-Za-z0-9_.-]{3,100}$")
SECOND_CODE_REGEX = re.compile(r"^\d{6}$")

# 每次請求刷新永久會話（滑動過期）
@app.before_request
def refresh_session_timeout():
    if session.get("user_id"):
        session.permanent = True

# ------------------------
# 路由
# ------------------------

# 首頁
@app.route("/")
@login_required
def index():
    """
    主頁面：依 session 判斷是否登入，顯示不同區塊。
    """
    account = session.get("account")
    # 成功登入總數
    total_login = db.session.query(func.count(LoginLog.id)).filter(LoginLog.success.is_(True)).scalar() or 0
    # 登出總數
    total_logout = db.session.query(func.count(LogoutLog.id)).scalar() or 0

    online_count = max(0, total_login - total_logout)

    return render_template("index.html", account=account, online_count=online_count)

def common_ctx():
  # 你現有的 online_count 取得方式
  account = session.get("account")
  # 假設你已有 get_online_count()，或改用自己的計算
  try:
    # 成功登入總數
    total_login = db.session.query(func.count(LoginLog.id)).filter(LoginLog.success.is_(True)).scalar() or 0
    # 登出總數
    total_logout = db.session.query(func.count(LogoutLog.id)).scalar() or 0

    online_count = max(0, total_login - total_logout)
  except Exception:
    online_count = 0
  return {"account": account, "online_count": online_count}

# 登入頁
@app.route("/login", methods=["GET"])
def login_page():
    return render_template("login.html")

# 註冊頁
@app.route("/register", methods=["GET"])
def register_page():
    return render_template("register.html")

# 忘記密碼頁（占位）
@app.route("/forgot", methods=["GET"])
def forgot_page():
    return render_template("forgot.html")

@app.route("/success", methods=["GET"])
def success_page():
    # 通用成功頁，可重設密碼成功後導向此頁
    return render_template("success.html")

@app.route("/error", methods=["GET"])
def error_page():
    # 如果有需要可自訂錯誤頁
    msg = request.args.get("msg", "發生錯誤")
    return f"<h1>錯誤</h1><p>{msg}</p>", 400

# 登出
@app.route("/logout", methods=["POST", "GET"])
def logout():
    """
    手動登出：紀錄登出、清空 session、導回主頁
    """
    uid = session.get("user_id")
    if uid:
        write_logout_log(uid, "manual")
    session.clear()
    return redirect(url_for("index"))

@app.route("/dashboard")
@login_required
def dashboard():
    account = session.get("account")
    return f"<h1>會員主頁</h1><p>歡迎，{account or ''}</p>"



# --- 功能頁路由（六個分頁） ---
# 注意：目前都回到同一個 index.html，由 active_tab 控制右側主內容佔位
# 若你未來要做成獨立模板，可將 return render_index(...) 改為 render_template("teams.html", ...)

@app.get("/teams")
@login_required
def teams():
  return render_template("teams.html", **common_ctx())

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
  return render_template("scouts.html", **common_ctx())

@app.get("/market")
@login_required
def market():
  return render_template("market.html", **common_ctx())

@app.get("/players")
@login_required
def players():
  return render_template("players.html", **common_ctx())

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

# ------------------------
# 提交邏輯
# ------------------------

# 註冊提交
@app.route("/register", methods=["POST"])
def register_submit():
    # 支援 JSON 或 Form，但預期為 Form 傳統提交
    if request.is_json:
        data = request.get_json(silent=True) or {}
        account = (data.get("account") or "").strip()
        password = data.get("password") or ""
        second_code_plain = (data.get("second_code") or "").strip()
    else:
        account = (request.form.get("account") or "").strip()
        password = request.form.get("password") or ""
        second_code_plain = (request.form.get("second_code") or "").strip()

    # 驗證
    if not account or not ACCOUNT_REGEX.match(account):
        return redirect(url_for("register_page", msg="帳號格式不符（3~100 位英數與 ._-）", type="error"))
    if not password or not PASSWORD_REGEX.match(password):
        return redirect(url_for("register_page", msg="密碼需 8~12 位且同時包含英文字母與數字，且不可含空白", type="error"))
    if not second_code_plain or not SECOND_CODE_REGEX.match(second_code_plain):
        return redirect(url_for("register_page", msg="二次驗證碼需為 6 位數字", type="error"))

    # 檢查是否已存在
    exists = User.query.filter_by(account=account).first()
    if exists:
        return redirect(url_for("register_page", msg="帳號已存在", type="error"))

    # 建立
    user = User(
        account=account,
        password_hash=hash_password(password),
        second_code=hash_code(second_code_plain),
        register_ip=get_client_ip(),
    )
    db.session.add(user)
    db.session.commit()

    return redirect(url_for("login_page", msg="註冊成功，請登入", type="success"))

# 登入提交
@app.route("/login", methods=["POST"])
def login_submit():
    account = (request.form.get("account") or "").strip()
    password = request.form.get("password") or ""

    user = User.query.filter_by(account=account).first()
    ok = False
    if user and verify_password(password, user.password_hash):
        ok = True

    # 記錄登入紀錄
    log = LoginLog(
        user_id=user.id if user else 0,
        ip=get_client_ip(),
        success=ok,
        user_agent=(request.headers.get("User-Agent") or "")[:512]
    )
    db.session.add(log)
    db.session.commit()

    if not ok:
        return redirect(url_for("login_page", msg="登入失敗，帳號或密碼錯誤", type="error"))

    # 登入成功，寫入 session 並導回主頁
    session["account"] = user.account
    session["user_id"] = user.id
    touch_last_seen()
    return redirect(url_for("index"))

@app.route("/forgot", methods=["POST"])
def forgot_submit():
    """
    進行重設密碼第一步：驗證帳號與 second_code 後，導向 reset 頁或直接允許在同頁輸入新密碼。
    為了簡化，這裡直接在同一路由完成重設（POST /reset），也提供二步驟版本。
    這裡採用直接重設：POST /forgot 接受 account、second_code、新密碼。
    """
    account = (request.form.get("account") or "").strip()
    second_code_plain = (request.form.get("second_code") or "").strip()
    new_password = request.form.get("new_password") or ""

    if not account or not ACCOUNT_REGEX.match(account):
        return redirect(url_for("forgot_page", msg="帳號格式不符", type="error"))
    if not second_code_plain or not SECOND_CODE_REGEX.match(second_code_plain):
        return redirect(url_for("forgot_page", msg="二次驗證碼需為 6 位數字", type="error"))
    if not new_password or not PASSWORD_REGEX.match(new_password):
        return redirect(url_for("forgot_page", msg="新密碼需 8~12 位且同時包含英文字母與數字，且不可含空白", type="error"))

    user = User.query.filter_by(account=account).first()
    if not user:
        return redirect(url_for("forgot_page", msg="帳號不存在", type="error"))

    if not verify_code(second_code_plain, user.second_code):
        return redirect(url_for("forgot_page", msg="二次驗證碼不正確", type="error"))

    # 更新密碼
    user.password_hash = hash_password(new_password)
    db.session.commit()

    return redirect(url_for("success_page"))

# ------------------------
# 啟動輔助
# ------------------------

@app.cli.command("init-db")
def init_db():
    """初始化資料表"""
    db.create_all()
    print("資料表建立完成")

if __name__ == "__main__":
    # 啟動前清空終端：Windows 用 cls，其它用 clear
    try:
        os.system("cls" if os.name == "nt" else "clear")
    except Exception:
        pass

    # 只在本機開發使用，正式環境請用 WSGI/ASGI 伺服器
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=True)