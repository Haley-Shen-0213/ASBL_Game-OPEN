# 匯入標準函式庫模組
import os                                        # 讀取環境變數與路徑操作
import random                                    # 隨機數生成（用於能力值、位置、身高）
import argparse                                  # 命令列參數解析
from datetime import datetime                    # 產生 created_at 時間戳
from typing import Dict, List, Optional, Tuple   # 型別註解

# 嘗試讀取 .env 檔案中的環境變數（例如資料庫設定）
try:
    from dotenv import load_dotenv  # 從 python-dotenv 套件載入 .env
    load_dotenv()                   # 將 .env 內容注入到環境變數
except Exception:
    pass  # 若沒裝 dotenv 或發生例外，略過不影響主流程（也可以只靠系統環境變數）

# 從環境變數讀取 MySQL 連線設定（若未設定則使用預設值）
DB_HOST = os.getenv("MYSQL_HOST", "localhost")         # 資料庫主機
DB_PORT = int(os.getenv("MYSQL_PORT", "3306"))         # 資料庫連接埠
DB_USER = os.getenv("MYSQL_USER", "root")              # 使用者名稱
DB_PASSWORD = os.getenv("MYSQL_PASSWORD", "")          # 密碼
DB_NAME = os.getenv("MYSQL_DB", "")                    # 資料庫名稱

# 匯入 PyMySQL 以連線 MySQL，若未安裝則提示使用者安裝
try:
    import pymysql
except ImportError:
    raise SystemExit("請先安裝依賴：pip install pymysql python-dotenv")

# 嘗試匯入 tqdm 以提供進度條；若沒有也不影響主要功能
try:
    from tqdm import tqdm
except Exception:
    tqdm = None  # 未安裝 tqdm 時，將 tqdm 設為 None，後續以條件判斷是否顯示進度條

# ========== 常數設定 ==========
DEFAULT_AGE = 18                                      # 預設年齡
STAT_MIN, STAT_MAX = 1, 99                            # 能力值範圍
UNTRAINABLE_SUM_MIN, UNTRAINABLE_SUM_MAX = 10, 990    # 不可訓練能力加總上下限（保護邊界）
OVERALL_GRADES = ["G", "C", "B", "A", "S", "SS", "SSR"]  # 總評等級
OVERALL_GRADE_WEIGHTS = [0.28, 0.26, 0.22, 0.14, 0.07, 0.025, 0.005]  # 總評機率權重

# 能力欄位列表（用於總和與展示排序）
STAT_KEYS = [
    "ath_stamina", "ath_strength", "ath_speed", "ath_jump",
    "shot_touch", "shot_release", "shot_accuracy", "shot_range",
    "def_rebound", "def_boxout", "def_contest", "def_disrupt",
    "off_move", "off_dribble", "off_pass", "off_handle",
    "talent_offiq", "talent_defiq", "talent_health", "talent_luck",
]

# ========== DB 連線與資料讀取 ==========
def get_connection():
    """
    建立並回傳一個 MySQL 連線。
    需要 DB_NAME 已設定，否則拋出錯誤提醒設定 .env。
    """
    if not DB_NAME:
        # 若未提供資料庫名稱，明確提示使用者設定
        raise ValueError("MYSQL_DB 未設定，請在 .env 提供 MYSQL_DB=ASBL_DATA 等值")
    # 建立 pymysql 連線；使用 DictCursor 讓查詢結果以 dict 形式回傳
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )

def insert_player_to_db(conn, p: Dict) -> int:
    """
    將單一球員資料插入 players_basic 資料表。
    參數:
      conn: 既有的資料庫連線
      p: 球員資料 dict（欄位需對應 SQL）
    回傳:
      新增紀錄的自動編號（主鍵）
    """
    # 使用具名參數的 INSERT 語句，確保欄位順序與鍵相符
    sql = """
    INSERT INTO players_basic (
      user_id, player_name, age, height_cm, position,
      ath_stamina, ath_strength, ath_speed, ath_jump,
      shot_touch, shot_release, shot_accuracy, shot_range,
      def_rebound, def_boxout, def_contest, def_disrupt,
      off_move, off_dribble, off_pass, off_handle,
      talent_offiq, talent_defiq, talent_health, talent_luck,
      untrainable_sum, overall_grade, training_points, created_at,
      start_salary
    )
    VALUES (
      %(user_id)s, %(player_name)s, %(age)s, %(height_cm)s, %(position)s,
      %(ath_stamina)s, %(ath_strength)s, %(ath_speed)s, %(ath_jump)s,
      %(shot_touch)s, %(shot_release)s, %(shot_accuracy)s, %(shot_range)s,
      %(def_rebound)s, %(def_boxout)s, %(def_contest)s, %(def_disrupt)s,
      %(off_move)s, %(off_dribble)s, %(off_pass)s, %(off_handle)s,
      %(talent_offiq)s, %(talent_defiq)s, %(talent_health)s, %(talent_luck)s,
      %(untrainable_sum)s, %(overall_grade)s, %(training_points)s, %(created_at)s,
      %(start_salary)s
    )
    """
    with conn.cursor() as cur:
        # 執行單筆插入
        cur.execute(sql, p)
        # 立即提交，確保寫入成功（單筆插入成本較高）
        conn.commit()
        # 回傳最後插入的主鍵 ID
        return cur.lastrowid

def bulk_insert_players(conn, players: List[Dict]) -> int:
    """
    批量插入多筆球員資料至 players_basic。
    建議大量資料時使用，可降低 COMMIT 次數以提升效能。
    回傳: 實際插入的筆數
    """
    if not players:
        return 0  # 若列表為空，直接回傳 0
    # 與 insert_player_to_db 相同的欄位排列，搭配 executemany 做批量插入
    sql = """
    INSERT INTO players_basic (
      user_id, player_name, age, height_cm, position,
      ath_stamina, ath_strength, ath_speed, ath_jump,
      shot_touch, shot_release, shot_accuracy, shot_range,
      def_rebound, def_boxout, def_contest, def_disrupt,
      off_move, off_dribble, off_pass, off_handle,
      talent_offiq, talent_defiq, talent_health, talent_luck,
      untrainable_sum, overall_grade, training_points, created_at,
      start_salary
    )
    VALUES (
      %(user_id)s, %(player_name)s, %(age)s, %(height_cm)s, %(position)s,
      %(ath_stamina)s, %(ath_strength)s, %(ath_speed)s, %(ath_jump)s,
      %(shot_touch)s, %(shot_release)s, %(shot_accuracy)s, %(shot_range)s,
      %(def_rebound)s, %(def_boxout)s, %(def_contest)s, %(def_disrupt)s,
      %(off_move)s, %(off_dribble)s, %(off_pass)s, %(off_handle)s,
      %(talent_offiq)s, %(talent_defiq)s, %(talent_health)s, %(talent_luck)s,
      %(untrainable_sum)s, %(overall_grade)s, %(training_points)s, %(created_at)s,
      %(start_salary)s
    )
    """
    with conn.cursor() as cur:
        # executemany 會將 players 中的每個 dict 依序套入 SQL
        cur.executemany(sql, players)
    # 單次 commit，包含該批所有資料，效能優於每筆 commit
    conn.commit()
    return len(players)

def load_name_texts() -> Tuple[List[str], List[str]]:
    """
    自資料庫載入可用的 first_name 與 last_name 字串清單。
    期望資料表與欄位：
      - players_first_name(text)
      - players_last_name(text)
    回傳: (first_names, last_names)
    """
    # 取得 DB 連線
    conn = get_connection()
    first_names: List[str] = []
    last_names: List[str] = []
    try:
        with conn.cursor() as cur:
            # 讀取名
            cur.execute("SELECT text FROM players_first_name")
            rows = cur.fetchall()
            # 過濾空值並轉為字串清單
            first_names = [r["text"] for r in rows if r.get("text")]

            # 讀取姓
            cur.execute("SELECT text FROM players_last_name")
            rows = cur.fetchall()
            last_names = [r["text"] for r in rows if r.get("text")]
    finally:
        # 關閉連線，避免資源外洩
        conn.close()

    # 若列表為空，提示可能是資料或欄位名稱不符合預期
    if not first_names:
        raise RuntimeError("players_first_name 無資料或欄位名不符（預期 text）")
    if not last_names:
        raise RuntimeError("players_last_name 無資料或欄位名不符（預期 text）")

    return first_names, last_names

# ========== 名字生成邏輯 ==========
def generate_player_name(
    first_names: List[str],
    last_names: List[str],
    last_snippets_source: Optional[List[str]] = None
) -> str:
    """
    產生一個玩家姓名，格式為 'First Last'。
    若隨機出的姓氏過短（<=1），機率性地與另一段短字片段拼接，以增加自然度。
    """
    # 隨機取名與姓
    first = random.choice(first_names)
    last = random.choice(last_names)
    # 若姓長度大於 1，直接回傳
    if len(last) > 1:
        return f"{first} {last}"
    # 若姓太短，50% 機率嘗試用短片段補齊
    if random.random() > 0.5:
        # 片段來源：預設從 last_names 篩長度 <3 的短片段；也可由外部傳入自訂來源
        if last_snippets_source is None:
            candidates = [s for s in last_names if isinstance(s, str) and len(s) < 3]
        else:
            candidates = [s for s in last_snippets_source if isinstance(s, str) and len(s) < 3]
        # 若有可用片段，就把姓與片段拼接
        if candidates:
            last = last + random.choice(candidates)
    return f"{first} {last}"

# ========== 數值生成 ==========
def rand_stat() -> int:
    """
    產生單一能力值，範圍 [STAT_MIN, STAT_MAX]。
    """
    return random.randint(STAT_MIN, STAT_MAX)

def generate_untrainable_sum(stats: Dict[str, int]) -> int:
    """
    計算不可訓練能力加總（talent_* + 部分投籃與防守核心能力）。
    會做邊界保護在 [UNTRAINABLE_SUM_MIN, UNTRAINABLE_SUM_MAX]。
    """
    keys = [
        "talent_offiq", "talent_defiq", "talent_health", "talent_luck",
        "def_rebound", "def_boxout", "def_contest", "def_disrupt",
        "shot_touch", "shot_release", "shot_accuracy", "shot_range",
    ]
    # 累加指定鍵的能力值
    s = sum(stats[k] for k in keys)
    # 以 min/max 做上下限保護，避免異常值
    return max(UNTRAINABLE_SUM_MIN, min(UNTRAINABLE_SUM_MAX, s))

def pick_overall_grade() -> str:
    """
    依預設權重抽樣一個總評等級。
    """
    return random.choices(OVERALL_GRADES, weights=OVERALL_GRADE_WEIGHTS, k=1)[0]

# ========== 主生成功能 ==========
def generate_player(user_id: int, first_names: List[str], last_names: List[str]) -> Dict:
    """
    依據隨機規則產生一位球員的完整資料 dict，包含：
    - 基本資料：user_id、player_name、age、height_cm、position
    - 各能力值
    - untrainable_sum、overall_grade、training_points、created_at
    """
    # 先組姓名
    name = generate_player_name(first_names, last_names)
    # 依近似常態法則 + 骰子生成身高（更自然分布）
    height_cm = generate_random_height_with_dice(mean=195, std_dev=10, min_height=160, max_height=230)
    # 依身高估算位置（含機率分布）
    position = pick_position(height_cm)
    # 2) 依權重抽總評等級
    overall_grade = pick_overall_grade()
    # 3) 生成不可訓練能力，直到符合該等級區間
    stats_untrainable = {}
    untrainable_sum = 0
    stats_untrainable = build_untrainable_stats(overall_grade)

    untrainable_sum = compute_untrainable_sum(stats_untrainable)

    # 4) 生成可訓練能力
    stats_trainable = {
        "age": DEFAULT_AGE,
        "shot_accuracy": rand_stat(),
        "shot_range": rand_stat(),
        "def_rebound": rand_stat(),
        "def_boxout": rand_stat(),
        "def_contest": rand_stat(),
        "def_disrupt": rand_stat(),
        "off_move": rand_stat(),
        "off_dribble": rand_stat(),
        "off_pass": rand_stat(),
        "off_handle": rand_stat(),
    }

    # 5) 合併 20 項能力
    stats = {**stats_untrainable, **stats_trainable}

    # 組成完整 player dict（鍵需對應 DB 欄位）
    player = {
        "user_id": user_id,
        "player_name": name,
        **stats,
        "height_cm": height_cm,      # 身高（公分）
        "position": position,        # 位置（PG/SG/SF/PF/C）
        "untrainable_sum": untrainable_sum,
        "overall_grade": overall_grade,
        "training_points": 0,        # 初始訓練點數 0
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),  # 當前時間字串
    }
    # 7) 起始薪資
    player["start_salary"] = compute_start_salary(player)
    return player

def sum_all_stats(p: Dict) -> int:
    """
    將 STAT_KEYS 指定的能力值做總和（僅計算存在且為數字的欄位）。
    """
    return sum(int(p[k]) for k in STAT_KEYS if k in p and isinstance(p[k], (int, float)))

def print_player(p: Dict) -> None:
    """
    以人類可讀的方式列印單一球員資訊（用於除錯或單體測試）。
    """
    print("==== 生成的球員 ====")
    header_keys = ["user_id", "player_name", "age", "height_cm", "position"]
    # 先列印基本資訊
    for k in header_keys:
        print(f"{k}: {p[k]}")
    # 列印能力值
    for k in STAT_KEYS:
        print(f"{k}: {p[k]}")
    # 列印尾段欄位
    trailer_keys = ["untrainable_sum", "overall_grade", "training_points", "created_at"]
    for k in trailer_keys:
        print(f"{k}: {p[k]}")
    # 額外顯示能力總和
    total_stats = sum_all_stats(p)
    print(f"total_stats_sum: {total_stats}")

# === 新增：身高與位置生成 ===
def generate_random_height_with_dice(mean: int = 195, std_dev: int = 10, min_height: int = 160, max_height: int = 230) -> int:
    """
    使用 Box-Muller 產生近似常態分布的身高，並加上一顆 0~10 的骰子值，讓結果更離散自然。
    會限制最終身高在 [min_height, max_height] 範圍內。
    回傳：整數公分
    """
    import math
    while True:
        # 兩個 0~1 均勻分布亂數
        u1 = random.random()
        u2 = random.random()
        # Box-Muller 轉換取得標準常態亂數 z ~ N(0,1)
        z = math.sqrt(-2.0 * math.log(max(u1, 1e-12))) * math.cos(2.0 * math.pi * u2)
        # 將 z scale 成指定常態分布 N(mean, std_dev^2)
        height = mean + z * std_dev
        # 若在允許範圍則採用，否則重抽
        if min_height <= height <= max_height:
            # 額外擲骰（0~10），讓整數化後更接近日常分布
            dice = random.randint(0, 10)
            # 取四捨五入的整數公分 + 骰子，並避免超過上限
            final_height = min(int(round(height)) + dice, max_height)
            return final_height

# 定義位置列表（固定輸出順序用）
POSITIONS = ["PG", "SG", "SF", "PF", "C"]

def pick_position(height_cm: int) -> str:
    """
    根據身高區間，以機率分配球員位置。
    規則（示例，可調整）：
    - <190：PG 60%、SG 40%
    - 190~199：PG 35%、SG 45%、SF 20%
    - 200~209：PG 5%、SG 10%、SF 20%、PF 50%、C 15%（以 r 門檻實作）
    - >=210：PG 5%、SG 10%、SF 10%、PF 30%、C 45%
    註：實作上用連續門檻比較以避免浮點誤差。
    """
    r = random.random()  # 0~1 均勻亂數
    if height_cm < 190:
        # 60% PG，其他 SG
        if r < 0.60:
            return "PG"
        else:
            return "SG"
    elif height_cm < 200:
        # 35% PG，接著 45% SG（累積 80%），剩餘 20% SF
        if r < 0.35:
            return "PG"
        elif r < 0.80:
            return "SG"
        else:
            return "SF"
    elif height_cm < 210:
        # 5% PG，10% SG（累積 15%），20% SF（累積 35%），50% PF（累積 85%），其餘 C
        if r < 0.05:
            return "PG"
        elif r < 0.15:
            return "SG"
        elif r < 0.35:
            return "SF"
        elif r < 0.85:
            return "PF"
        else:
            return "C"
    else:
        # >=210：5% PG，10% SG（累積 15%），10% SF（累積 25%），30% PF（累積 55%），其餘 C
        if r < 0.05:
            return "PG"
        elif r < 0.15:
            return "SG"
        elif r < 0.25:
            return "SF"
        elif r < 0.55:
            return "PF"
        else:
            return "C"

# ========== 機率格式化（新增） ==========
def format_percentage(prob: float) -> str:
    """
    將 0~1 的機率轉為百分比字串，顯示到「至少三個非 0 數字」為止，且不四捨五入，只截斷。
    範例：
      0.123456 -> '12.345%'（若前三個非 0 出現在 12.345 即停）
      0.00089  -> '0.089%'（補到 3 個非 0）
    """
    p = prob * 100.0  # 轉為百分比
    if p == 0:
        return "0%"
    # 以高精度字串避免科學記號，並去除尾端多餘 0 與小數點
    s = f"{p:.20f}".rstrip("0").rstrip(".")
    if "." not in s:
        # 純整數情況：若整數位已至少有 3 個非 0 字，直接加%；否則加上 .0%
        nonzeros = sum(1 for ch in s if ch != "0")
        if nonzeros >= 3:
            return s + "%"
        else:
            return s + ".0%"
    # 一般情況：拆分整數與小數部分
    int_part, frac_part = s.split(".") if "." in s else (s, "")
    # 統計整數部分的非 0 數字數量
    nonzeros = sum(1 for ch in int_part if ch != "0")
    out_frac = ""
    if nonzeros >= 3:
        # 整數位已滿足三個非 0，直接使用整數部分
        return int_part + "%"
    # 否則從小數部分依序補字，直到總非 0 達 3
    for ch in frac_part:
        out_frac += ch
        if ch != "0":
            nonzeros += 1
        if nonzeros >= 3:
            break
    # 去除 out_frac 尾端多餘的 0（純顯示目的）
    out_frac = out_frac.rstrip("0")
    if out_frac:
        return f"{int_part}.{out_frac}%"
    else:
        # 小數部分全為 0 或不足，但出於一致性至少保留 .0%
        return f"{int_part}.0%"

# ========== 模擬統計（擴充：身高與位置分布 + 選擇性輸出） ==========
def total_stats_bucket(total: int) -> str:
    """
    將能力總和 total 分箱，以利統計顯示。
    <1000 為一類；其餘以 100 區間分箱（如 1200-1299）。
    """
    if total < 1000:
        return "<1000"
    # 取下界（整百），上界為下界 + 99
    lower = (total // 100) * 100
    upper = lower + 99
    return f"{lower}-{upper}"

# 固定輸出身高分箱的順序，讓統計結果易讀、穩定
HEIGHT_BUCKET_ORDER = ["<180", "180-189", "190-199", "200-209", "210-219", ">=220"]

def height_bucket(height_cm: int) -> str:
    """
    以 10 公分為單位將身高分箱，提供模擬統計用。
    """
    if height_cm < 180:
        return "<180"
    elif height_cm < 190:
        return "180-189"
    elif height_cm < 200:
        return "190-199"
    elif height_cm < 210:
        return "200-209"
    elif height_cm < 220:
        return "210-219"
    else:
        return ">=220"

def simulate_many(
    n: int,                               # 要模擬生成的球員次數（迴圈次數）
    first_names: List[str],               # 名字清單（從 DB 載入或快取）
    last_names: List[str],                # 姓氏清單（從 DB 載入或快取）
    only_height_position: bool = False,   # 若為 True，僅輸出身高與位置分布統計
    only_overall: bool = False,           # 若為 True，僅輸出 Overall 等級機率統計
    only_total: bool = False,             # 若為 True，僅輸出能力總和分箱統計
) -> None:
    """
    進行 n 次球員生成的模擬，並統計：
    - Overall 等級出現機率（依 OVERALL_GRADES 列表的順序輸出）
    - 總能力值加總（sum_all_stats）的分箱機率（例如 <1000、1200-1299）
    - 身高與位置分布機率（依 height_bucket 與 POSITIONS 的固定順序輸出）
    可透過 only_* 三個旗標參數，選擇性地只輸出其中一類統計，以降低 I/O 噪音。
    注意：即使只輸出部分類別，內部仍會依條件決定是否計數，避免不必要計算。
    """

    # 初始化身高分箱計數表：預先以固定順序建立鍵，值為 0
    # 這裡先用推導式初始化，讓所有預期的分箱鍵都存在（提高輸出穩定性）
    height_counts: Dict[str, int] = {k: 0 for k in HEIGHT_BUCKET_ORDER}

    # 根據 only_* 旗標決定實際要統計哪些類別
    # 規則：若沒有指定任何 only_*，則全部統計；若指定其中之一，則只統計該類，或在該類別的旗標為 True 時，即便其他為 True 也啟用
    # 直觀理解：
    # - count_height_pos：當 only_overall 與 only_total 都不是 True（即未限制）時，預設統計；或只要 only_height_position=True，就無論如何也統計
    count_height_pos = (not (only_overall or only_total)) or only_height_position
    # - count_overall：當 only_height_position 與 only_total 都不是 True 時，預設統計；或只要 only_overall=True，就無論如何也統計
    count_overall = (not (only_height_position or only_total)) or only_overall
    # - count_total：當 only_height_position 與 only_overall 都不是 True 時，預設統計；或只要 only_total=True，就無論如何也統計
    count_total = (not (only_height_position or only_overall)) or only_total

    # Overall 等級的出現次數計數表，以 OVERALL_GRADES 為鍵，全部初始化為 0
    grade_counts: Dict[str, int] = {g: 0 for g in OVERALL_GRADES}
    # 能力總和的分箱計數表（動態字典：鍵在模擬過程中才會出現）
    bucket_counts: Dict[str, int] = {}
    # 再次顯式初始化身高分箱計數（確保所有分箱鍵存在；與上方推導式初始化一致）
    height_counts: Dict[str, int] = {"<180": 0,"180-189": 0, "190-199": 0, "200-209": 0, "210-219": 0, ">=220": 0}
    # 位置分布計數表，鍵為固定的五個位置，初始為 0
    position_counts: Dict[str, int] = {pos: 0 for pos in POSITIONS}

    # 準備迭代器：若已安裝 tqdm，則以 tqdm 包裝迭代器顯示進度條；否則就用純 range(n)
    iterator = range(n)
    if tqdm is not None:
        # desc 為進度條標題；unit 指定單位文字「次」
        iterator = tqdm(iterator, desc="模擬球員", unit="次")

    # 主模擬迴圈：執行 n 次
    for _ in iterator:
        # 生成一名球員（user_id=0 表示模擬用途，不影響真實資料）
        p = generate_player(user_id=0, first_names=first_names, last_names=last_names)

        # 若需要統計 Overall 等級，將該名球員的 overall_grade 對應的計數 +1
        if count_overall:
            grade_counts[p["overall_grade"]] += 1

        # 若需要統計能力總和分箱：
        if count_total:
            # 計算該名球員在 STAT_KEYS 所列能力的總和
            total = sum_all_stats(p)
            # 依總和落點取得分箱（字串），例如 "1200-1299"
            bucket = total_stats_bucket(total)
            # 以 dict 的 get 讀取當前計數（若不存在則為 0），再 +1 累計
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        # 若需要統計身高與位置分布：
        if count_height_pos:
            # 先以 height_bucket 將身高轉為分箱字串（例如 "190-199"）
            hb = height_bucket(p["height_cm"])
            # 身高分箱的對應計數 +1
            height_counts[hb] += 1
            # 位置分布：以位置字串作為鍵，讀取目前計數（若不存在則 0）後 +1
            position_counts[p["position"]] = position_counts.get(p["position"], 0) + 1

    # ——— 輸出統計結果區塊 ———

    # 顯示大標題與總模擬次數
    print("\n==== 模擬結果 ====")
    print(f"總模擬次數: {n}")

    # 若有進行 Overall 統計，則依 OVERALL_GRADES 的固定順序輸出次數與機率
    if count_overall:
        print("\nOverall 等級出現機率：")
        for g in OVERALL_GRADES:
            # 讀取該等級的出現次數，若沒出現則為 0
            count = grade_counts.get(g, 0)
            # 機率 = 次數 / 總模擬次數
            prob = count / n
            # 以 format_percentage 將 0~1 機率轉為漂亮的百分比字串（截斷到三個非 0 數字）
            print(f"{g}: 次數={count}, 機率={format_percentage(prob)}")

    # 若有進行能力總和分箱統計：
    if count_total:
        # 為了讓分箱輸出順序穩定、易讀，自訂排序：
        # - 先輸出特殊的 "<1000"
        # - 其餘分箱（如 "1200-1299"）依下界數字升冪排序
        def bucket_sort_key(b: str) -> Tuple[int, int]:
            if b == "<1000":
                # 第一群組（0），特判 "<1000" 排最前面
                return (0, -1)
            # 其他範圍以左界整數排序，例如 "1200-1299" 取 "1200"
            lower = int(b.split("-")[0])
            # 第二群組（1），按下界數字由小到大
            return (1, lower)

        print("\n總能力值加總（total_stats_sum）分箱機率：")
        # 先依自訂排序鍵排序後輸出
        for b in sorted(bucket_counts.keys(), key=bucket_sort_key):
            # 該分箱的累計次數
            count = bucket_counts[b]
            # 機率 = 次數 / 總模擬次數
            prob = count / n
            # 輸出格式：分箱標籤、次數、格式化百分比
            print(f"{b}: 次數={count}, 機率={format_percentage(prob)}")

    # 若有進行身高與位置分布統計：
    if count_height_pos:
        print("\n身高分箱機率：")
        # 身高分箱以 HEIGHT_BUCKET_ORDER 的固定順序輸出，避免 key 遍歷順序造成的跳動
        for hb in HEIGHT_BUCKET_ORDER:
            # 讀取每個分箱的次數（若不存在則 0），以確保穩定輸出
            count = height_counts.get(hb, 0)
            # 機率 = 次數 / 總模擬次數
            prob = count / n
            print(f"{hb}: 次數={count}, 機率={format_percentage(prob)}")

        # 位置分布：以固定的 POSITIONS 順序（["PG","SG","SF","PF","C"]）輸出
        print("\n位置分布機率：")
        for pos in POSITIONS:
            # 讀取對應位置的次數（不存在則 0）
            count = position_counts.get(pos, 0)
            # 機率 = 次數 / 總模擬次數
            prob = count / n
            print(f"{pos}: 次數={count}, 機率={format_percentage(prob)}")

# 等級區間（由不可訓練總和決定）
# 注意邊界：你定義的是 <400:G；400–599:C；600–699:B；700–799:A；800–899:S；900–950:SS；951+:SSR
GRADE_RULES = {
    "G":  {"sum_min": 10,  "sum_max": 400, "stat_min": 10, "stat_max": 60},
    "C":  {"sum_min": 399, "sum_max": 600, "stat_min": 20, "stat_max": 70},
    "B":  {"sum_min": 599, "sum_max": 700, "stat_min": 30, "stat_max": 70},
    "A":  {"sum_min": 699, "sum_max": 800, "stat_min": 40, "stat_max": 75},
    "S":  {"sum_min": 799, "sum_max": 900, "stat_min": 50, "stat_max": 80},
    "SS": {"sum_min": 900, "sum_max": 950, "stat_min": 60, "stat_max": 99},
    "SSR":{"sum_min": 951, "sum_max": 990, "stat_min": 91, "stat_max": 99},
}

# 不可訓練能力鍵（10 項）
UNTRAINABLE_KEYS = [
    "ath_stamina", "ath_strength", "ath_speed", "ath_jump",
    "shot_touch", "shot_release",
    "talent_offiq", "talent_defiq", "talent_health", "talent_luck",
]

# 可訓練能力鍵（20 項中扣除上述 10 項）
TRAINABLE_KEYS = [
    "shot_accuracy", "shot_range",
    "def_rebound", "def_boxout", "def_contest", "def_disrupt",
    "off_move", "off_dribble", "off_pass", "off_handle",
]

# 等級係數（用於起始薪資）
GRADE_FACTOR = {
    "G": 1.0,
    "C": 1.1,
    "B": 1.3,
    "A": 1.6,
    "S": 2.0,
    "SS": 2.5,
    "SSR": 3.0,
}

def compute_untrainable_sum(stats: Dict) -> int:
    """只加總 UNTRAINABLE_KEYS"""
    return sum(int(stats.get(k, 0) or 0) for k in UNTRAINABLE_KEYS)

def pick_target_sum(rule):
    # 在等級的總分範圍內挑一個目標總分（可改為偏好中位數）
    import random
    return random.randint(rule["sum_min"], rule["sum_max"])

def generate_untrainable_by_grade(grade: str):
    import random

    rule = GRADE_RULES[grade]
    stat_min = rule["stat_min"]
    stat_max = rule["stat_max"]

    # 先建立所有欄位的 min 值，年齡可特別處理
    stats = {}
    for k in UNTRAINABLE_KEYS:
        if k == "age":
            stats[k] = DEFAULT_AGE  # 年齡不算入能力總分的話，就在 compute_untrainable_sum 排除它
        else:
            stats[k] = stat_min

    # 計算目前總分（不含 age 的話，請在 compute_untrainable_sum 內排除 age）
    current_sum = compute_untrainable_sum(stats)
    target_sum = pick_target_sum(rule)

    # 若 min*欄位數已超過 target_sum，則直接回傳 min（或把 target_sum 調到 current_sum）
    if current_sum >= target_sum:
        return stats

    # 計算每欄可提升的餘裕
    keys_for_dist = [k for k in UNTRAINABLE_KEYS if k != "age"]
    capacity = {k: (stat_max - stats[k]) for k in keys_for_dist}
    remaining = target_sum - current_sum

    # 隨機分配剩餘分數到各欄位（受 capacity 限制）
    # 可採用多輪少量分配，避免一次集中導致超限
    # 為了效能，使用簡單迴圈就能達標，無需 while True
    while remaining > 0:
        # 篩選還有空間的欄位
        candidates = [k for k in keys_for_dist if capacity[k] > 0]
        if not candidates:
            # 所有欄位都滿了，無法達到 target_sum，直接停止（總分可能略低於 target）
            break
        # 隨機挑一個欄位與一次分配量（1~min(剩餘, 該欄位容量, 小步長))
        k = random.choice(candidates)
        step = min(remaining, capacity[k], random.randint(1, 3))  # 小步長 1~3，可調整平滑度
        stats[k] += step
        capacity[k] -= step
        remaining -= step

    # 最終校驗：若總分仍不在範圍，做一次邊界修正
    final_sum = compute_untrainable_sum(stats)
    if final_sum < rule["sum_min"]:
        # 若偏低，嘗試把剩餘空間用掉
        short = rule["sum_min"] - final_sum
        for k in keys_for_dist:
            if short <= 0:
                break
            room = stat_max - stats[k]
            add = min(room, short)
            stats[k] += add
            short -= add
    elif final_sum > rule["sum_max"]:
        # 若偏高，等比例壓回來（或逐格減）
        over = final_sum - rule["sum_max"]
        for k in keys_for_dist:
            if over <= 0:
                break
            reducible = stats[k] - stat_min
            sub = min(reducible, over)
            stats[k] -= sub
            over -= sub

    return stats

# 用法：以 overall_grade 決定等級，再生成
def build_untrainable_stats(overall_grade: str):
    return generate_untrainable_by_grade(overall_grade)

def compute_start_salary(player: Dict) -> int:
    """
    起始薪資 = 20項能力值總和 × 等級係數
    能力鍵來自 STAT_KEYS（你已定義的 20 項）
    """
    # 20 項能力值總和
    total_abilities = sum(int(player.get(k, 0) or 0) for k in STAT_KEYS)
    # 等級係數（預設 1.0）
    grade = str(player.get("overall_grade") or "").upper()
    factor = float(GRADE_FACTOR.get(grade, 1.0))
    return int(round(total_abilities * factor))

# ========== 封裝：提供可被其他模組呼叫的 API ==========
def generate_and_persist(
    count: int,                                  # 需要產生的球員筆數
    user_id: int,                                # 資料所屬的使用者（寫入時關聯用）
    *,                                           
    insert: bool = True,                         # True：批次插入（bulk）；False：逐筆插入（single）
    batch_size: int = 100,                       # 批次插入時的每批大小（調節效能與記憶體）
    echo: bool = False,                          # 是否印出每位球員（開發/除錯用，正式批量建議關閉）
    first_names: Optional[List[str]] = None,     # 可注入名的快取，避免在此函式內讀 DB
    last_names: Optional[List[str]] = None,      # 可注入姓的快取，避免在此函式內讀 DB
) -> Dict:
    """
    產生 count 位球員，並依 insert/batch_size 寫入資料庫。
    - 預設行為：insert=True（批次）、echo=False（靜默）、batch_size=100
    - 若有提供 first_names/last_names，則直接使用，避免重複讀取 DB（提升效能）。
    - 統一回傳摘要，方便上層（CLI/服務/API）使用與測試。

    回傳:
      {
        "total_generated": int,                    # 真正生成的筆數（理應等於 count）
        "total_inserted": int,                    # 成功寫入資料庫的筆數（由 DB 回傳值累計）
        "preview": [Dict... 最多 5 筆],           # 前 5 筆資料（用於顯示或驗證）
        "mode": "single_insert" | "bulk_insert"   # 實際採用的插入模式
      }

    設計優勢與理由：
    - 可注入姓名清單（快取）：避免每次呼叫都讀 DB，降低 I/O，讓多次批次呼叫更有效率。
    - 提供單筆與批次兩種模式：兼顧簡單性（逐筆）與高效能（批次）。
    - 批次插入配合 batch_size：能平衡吞吐量與記憶體佔用，避免巨大交易造成壓力或鎖表時間過長。
    - echo 開關：在壓測/正式環境避免大量 I/O（print 是昂貴操作），在開發/問題定位時能開啟觀察內容。
    - 統一回傳結構：上層可穩定取用統計值與預覽，利於自動化測試與監控。
    """

    # 一次性準備姓名來源：
    # - 若外部沒有提供快取，則在此讀一次 DB，避免在迴圈中重複開銷
    # 優勢：減少 DB 連線和查詢次數，降低延遲與負載
    if first_names is None or last_names is None:
        first_names, last_names = load_name_texts()

    if not insert:
        # 單筆插入模式：每筆都能立即取得 lastrowid
        conn = get_connection()
        try:
            total_inserted = 0
            preview: List[Dict] = []
            inserted_ids: List[int] = []

            for i in range(count):
                p = generate_player(user_id=user_id, first_names=first_names, last_names=last_names)

                if echo:
                    print_player(p)
                    print()

                new_id = insert_player_to_db(conn, p)  # 確保此函式回傳新列的自增主鍵
                total_inserted += 1
                inserted_ids.append(int(new_id))

                if i < 5:
                    p_with_id = dict(p)
                    p_with_id["player_id"] = int(new_id)
                    preview.append(p_with_id)

            return {
                "total_generated": count,
                "total_inserted": total_inserted,
                "preview": preview,
                "inserted_ids": inserted_ids,
                "mode": "single_insert",
            }
        finally:
            conn.close()

    else:
        # 批次插入模式：效能最佳。若要回傳每筆主鍵，需讓 bulk_insert_players 回傳 List[int]
        conn = get_connection()
        try:
            batch: List[Dict] = []
            total_inserted = 0
            preview: List[Dict] = []
            inserted_ids: List[int] = []  # 若底層支援，將填入

            for i in range(count):
                p = generate_player(user_id=user_id, first_names=first_names, last_names=last_names)

                if echo:
                    print_player(p)
                    print()

                # 收集前 5 筆預覽（此刻尚無 id）
                if i < 5:
                    preview.append(dict(p))  # 先放內容，待有 id 再補

                batch.append(p)

                if len(batch) >= batch_size:
                    # 若 bulk_insert_players 僅回傳筆數：
                    #   affected = bulk_insert_players(conn, batch)
                    #   total_inserted += affected
                    # 若你已改為回傳每筆主鍵清單 List[int]，請改用以下兩行：
                    new_ids = bulk_insert_players(conn, batch)  # 期望為 List[int]
                    if isinstance(new_ids, list):
                        total_inserted += len(new_ids)
                        inserted_ids.extend(int(x) for x in new_ids)
                        # 將 preview 尚未帶 id 的項目補上 player_id
                        pv_idx = 0
                        for pid in new_ids:
                            # 找到第一個尚未有 player_id 的 preview 項，補上
                            while pv_idx < len(preview) and "player_id" in preview[pv_idx]:
                                pv_idx += 1
                            if pv_idx < len(preview):
                                preview[pv_idx]["player_id"] = int(pid)
                                pv_idx += 1
                    else:
                        # 舊實作：只回傳受影響筆數
                        affected = int(new_ids) if new_ids is not None else 0
                        total_inserted += affected

                    batch.clear()

            if batch:
                # 同上批次處理
                new_ids = bulk_insert_players(conn, batch)
                if isinstance(new_ids, list):
                    total_inserted += len(new_ids)
                    inserted_ids.extend(int(x) for x in new_ids)
                    pv_idx = 0
                    for pid in new_ids:
                        while pv_idx < len(preview) and "player_id" in preview[pv_idx]:
                            pv_idx += 1
                        if pv_idx < len(preview):
                            preview[pv_idx]["player_id"] = int(pid)
                            pv_idx += 1
                else:
                    affected = int(new_ids) if new_ids is not None else 0
                    total_inserted += affected

            # 確保 preview 至少有一致的結構
            for pv in preview:
                if "player_id" not in pv:
                    pv["player_id"] = None  # 若無法取得每筆主鍵，先用 None 佔位

            out: Dict = {
                "total_generated": count,
                "total_inserted": total_inserted,
                "preview": preview,
                "mode": "bulk_insert",
            }
            if inserted_ids:
                out["inserted_ids"] = inserted_ids

            return out
        finally:
            conn.close()

# ========== CLI 主程式（保留原行為） ==========
def main():
    """
    命令列入口。支援：
    - 模擬模式：--simulate [--sim-count N] [--only-* 選擇性輸出]
    - 生成寫入：
        - 預設（未加 --insert）：逐筆印出並單筆插入
        - --insert：不印出，改為批次寫入，可用 --batch-size 調整批次
    """
    parser = argparse.ArgumentParser(description="Generate test players or run large simulation.")
    parser.add_argument("--user-id", type=int, default=1, help="User ID for FK.")  # 外鍵用的 user_id
    parser.add_argument("--count", type=int, default=1, help="How many players to generate.")  # 生成數量
    parser.add_argument("--simulate", action="store_true", help="Run large simulation to estimate probabilities.")  # 模擬模式開關
    parser.add_argument("--sim-count", type=int, default=100_000_000, help="Simulation iterations (default=10,000,000).")  # 模擬次數

    # 只輸出特定類別統計（可減少輸出）
    parser.add_argument("--only-height-position", action="store_true", help="只統計身高與位置的機率")
    parser.add_argument("--only-overall", action="store_true", help="只統計 Overall grade 的機率")
    parser.add_argument("--only-total", action="store_true", help="只統計 total stats sum bucket 的機率")

    # 寫入 DB 選項
    parser.add_argument("--insert", action="store_true", help="將產生的球員寫入資料庫 players_basic")  # 若指定，批次寫入，不印出
    parser.add_argument("--batch-size", type=int, default=100, help="批量寫入一次的數量（預設 100）")   # 批次大小

    args = parser.parse_args()

    # 先載入姓名清單，避免在每次生成時都去查 DB
    first_names, last_names = load_name_texts()

    if args.simulate:
        # 進入模擬模式：不寫入 DB，只印出統計
        simulate_many(
            args.sim_count,
            first_names,
            last_names,
            only_height_position=args.only_height_position,
            only_overall=args.only_overall,
            only_total=args.only_total,
        )
        return

    # 生成模式（沿用既有邏輯）
    if not args.insert:
        # 未指定 --insert：逐筆印出並單筆插入（便於觀察資料）
        conn = get_connection()
        try:
            total_inserted = 0
            for _ in range(args.count):
                p = generate_player(user_id=args.user_id, first_names=first_names, last_names=last_names)
                print_player(p)   # 印出詳細資料
                print()
                insert_player_to_db(conn, p)  # 單筆插入
                total_inserted += 1
            print(f"未指定 --insert，但仍已寫入 {total_inserted} 筆至 players_basic（單筆插入）")
        finally:
            conn.close()
        return

    # 指定 --insert：批量寫入（不印出），效能較佳
    conn = get_connection()
    try:
        batch: List[Dict] = []
        total_inserted = 0
        for _ in range(args.count):
            p = generate_player(user_id=args.user_id, first_names=first_names, last_names=last_names)
            batch.append(p)
            if len(batch) >= args.batch_size:
                inserted = bulk_insert_players(conn, batch)
                total_inserted += inserted
                batch.clear()
        # 將尾批寫入
        if batch:
            inserted = bulk_insert_players(conn, batch)
            total_inserted += inserted
        print(f"已寫入 {total_inserted} 筆至 players_basic")
    finally:
        conn.close()

# 允許以 python -m modules.player_generator 執行本檔
if __name__ == "__main__":
    main()
