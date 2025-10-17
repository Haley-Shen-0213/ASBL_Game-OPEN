# 專案結構與技術說明

本專案為 Flask + SQLAlchemy 的 Web 應用，採用 Jinja2 模板與 session 認證機制，並內建登入/登出記錄與閒置登出控制。

## 目錄與檔案結構

- app.py
  - Flask 應用主程式
  - 環境變數載入、設定管理
  - SQLAlchemy ORM 模型（users、login_log、logout_log）
  - 密碼雜湊與二次驗證碼（second code）的雜湊邏輯
  - Session 與閒置過期控制（滑動續期）
  - 共用工具（IP 解析、代碼產生與驗證）
  - 視圖路由與模板渲染
- requirements.txt
  - 依賴套件清單（Flask、Flask‑SQLAlchemy、passlib[argon2] 等）
- templates/
  - Jinja2 前端模板（所有 HTML 皆於此）
  - base.html：全站基底模板（統一導覽、版面、靜態資源載入）
  - index.html、login.html、register.html、forgot.html、success.html、error.html：核心頁面
  - 其他模組頁面：guide.html、leagues.html、market.html、players.html、schedules.html、scouts.html、tactics.html、teams.html、trades.html、assets.html、community.html
- static/
  - images/logo.png（Logo，路徑示例：D:\ASBL_Game\static\images\logo.png；模板以 url_for('static', ...) 引用）

模板中引用靜態資源的方式：
```html
<img src="{{ url_for('static', filename='images/logo.png') }}" alt="ASBL Logo">
```

## 設定與環境變數

- 使用 python-dotenv（容錯載入）支援 .env
- 必填環境變數（缺少會直接拋出錯誤）
  - MYSQL_HOST、MYSQL_PORT（預設 3306）、MYSQL_USER、MYSQL_PASSWORD、MYSQL_DB
  - SECRET_KEY（Flask session 加密）
  - APP_SALT（應用層 HMAC 雜湊用 salt）
- SQLAlchemy 連線字串由上述參數組裝：
  - mysql+pymysql://{user}:{password}@{host}:{port}/{db}
- Flask 設定
  - JSON_AS_ASCII=False
  - SECRET_KEY 由環境變數設定
  - SQLALCHEMY_DATABASE_URI、SQLALCHEMY_TRACK_MODIFICATIONS=False
  - PERMANENT_SESSION_LIFETIME=10 分鐘（滑動過期）

## 資料模型與關聯

- User（users）
  - id, account(unique, index), password_hash, second_code_hash, register_ip, register_at
  - 關聯：User 1—N LoginLog（cascade="all, delete-orphan"）
- LoginLog（login_log）
  - id, user_id(FK→users.id, CASCADE), ip, success(Boolean), user_agent, created_at
- LogoutLog（logout_log）
  - id, user_id(FK→users.id, CASCADE), ip, reason(Enum: manual/idle_timeout/forced/session_invalid/other, 預設 manual), user_agent, created_at

注意：
- 外鍵皆設定 ON DELETE/UPDATE CASCADE，刪除使用者將一併清除其登入/登出記錄。
- Enum 欄位以命名 logout_reason 管理。

## 認證與安全機制

- 密碼雜湊：passlib 的 argon2（argon2.hash / argon2.verify）
- Second code 雜湊：HMAC-SHA256(APP_SALT) 後再做 base64 編碼
  - hash_code(plain) / verify_code(plain, hashed)
- 6 碼驗證碼：使用 secrets 產生 0–9 的亂數組成
- Session 安全：
  - SECRET_KEY 來自環境變數
  - PERMANENT_SESSION_LIFETIME=10 分鐘
  - 採滑動過期：每次有效操作會刷新 last_seen，以延續會話
  - 閒置逾時登出時記錄 logout_log，reason=idle_timeout
- IP 與 UA 記錄：
  - 以 X-Forwarded-For 解析客戶端 IP（有逗號取第一段），否則用 request.remote_addr
  - user_agent 存入 log

## 請求流程與中介邏輯（概要）

- 載入環境 → 初始化 Flask 與 SQLAlchemy → 定義模型與工具 → 設定 session 與逾時策略 → 定義路由
- 路由通常會：
  - 檢查 session 狀態與 last_seen 超時
  - 執行商務邏輯（登入、登出、註冊、頁面渲染）
  - 視需要寫入 LoginLog / LogoutLog
  - 回傳模板（render_template）或 JSON（API 類）

## 路由與流程圖

### 登入流程
<p align="left">
  <img src="docs/images/login.svg" alt="Login Flow" width="720">
</p>

### Players 頁流程
<p align="left">
  <img src="docs/images/players.svg" alt="Players Page Flow" width="720">
</p>

### 登出流程
<p align="left">
  <img src="docs/images/logout.svg" alt="Logout Flow" width="720">
</p>

### 閒置逾時檢查
<p align="left">
  <img src="docs/images/idle_timeout.svg" alt="Idle Timeout Check" width="720">
</p>

## 模板與頁面設計

- 基底模板 base.html 管理共用結構（導覽列、meta、favicon、CSS/JS）
- 個別功能頁透過 Jinja2 block 擴充
- 圖片、favicon 等一律使用 url_for('static', ...) 產生路徑，避免硬編路徑

## 日誌表運作規則

- login_log
  - 成功或失敗登入都記錄 success(Boolean)、ip、user_agent、created_at
- logout_log
  - 登出類型以 reason 區分：manual / idle_timeout / forced / session_invalid / other
  - 皆記錄 ip、user_agent、created_at

## 技術要點清單

- Flask（路由、session、模板渲染）
- Jinja2（模板與繼承）
- SQLAlchemy（ORM、關聯、外鍵、Enum）
- PyMySQL（MySQL 連線驅動）
- passlib[argon2]（安全密碼雜湊）
- HMAC-SHA256 + base64（second code 驗證碼雜湊）
- dotenv（可選；本地開發載入 .env）
- 安全實務：
  - 不在程式中硬編密鑰或資料庫帳密
  - 所有敏感值從環境變數載入與驗證
  - 會話滑動過期、閒置登出記錄
  - 以外鍵約束和 CASCADE 保持資料一致性

## 與資料庫操作相關的注意事項

- 若需清空 login_log / logout_log
  - TRUNCATE 快且會重置 AUTO_INCREMENT，但需先停用外鍵檢查或臨時移除外鍵
  - DELETE 可回滾，但大量資料較慢，需手動 ALTER TABLE … AUTO_INCREMENT=1
- 因模型已設 ON DELETE CASCADE，刪除 users 會自動清除相關 log

