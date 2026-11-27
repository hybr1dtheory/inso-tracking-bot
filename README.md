# INSO UKR Travel Monitoring Bot

[![Python Version](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/aiogram-3.x-blue.svg)](https://github.com/aiogram/aiogram)
[![Database](https://img.shields.io/badge/sqlite-3-brightgreen.svg)](https://www.sqlite.org/index.html)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

An asynchronous Telegram bot system designed for planning, tracking, and monitoring trips in high-risk and standard zones (specifically tailored for INSO Ukraine operations). It includes a dedicated traveler (crew) interface, an interactive operator monitoring dashboard, and a background Watchdog service that ensures safety through automated check-in reminders, active SOS tracking, and real-time alerts.

---

## 🎯 Key Features

### 🚗 For Travelers (Crew)
- **Multi-step Trip Planning:** A structured 6-step creation Wizard with strict date (`DD.MM.YYYY`) and 24-hour time (`HH:MM`) validation.
- **Crew Assignment:** Easy companion selection with built-in pagination.
- **One-Tap Check-In:** Periodic check-ins (`📍 Check-in`) with location/status input.
- **Active SOS Alerting:** Immediate `🆘 Need help` triggers with optional incident details sent to the monitoring group and crew.
- **Direct Resolve:** Interactive `✅ Issue resolved` button to reset alerts and return tracking to standard status.
- **My Trip Card:** Dedicated visual status cards for active and planned trips.

### 🎧 For Operators & Admins
- **Unified Live Dashboard:** A persistent overview of all `ACTIVE` and `PLANNED` trips accessible via the `/trips` command, automatically prioritized.
- **Trip Status At-A-Glance:** Color-coded status icons (`🟢` Normal, `🟡` Pending response, `🔴` Overdue check-in, `🆘` Active SOS, `📅` Planned).
- **Intervention Tools:** Force start, cancel, or close any trip directly from the bot.
- **Check-In On Demand:** Direct "ping" (`📡 Send check-in request`) dispatched to all crew members.
- **Ad-hoc Intervals:** Adjust check-in rules (`⏱ Change check-in interval`) dynamically (30m, 60m, 120m, or "Start/Finish only").

### 🛡 Admin Panel & Access Control
- **Rigid Whitelist (Gatekeeper):** Unregistered users are blocked at the outer-middleware level, showing their Telegram ID to request access.
- **Resource Management:** Command and buttons via `/admin` to list, page, add, rename, adjust teams, change roles (`TRAVELER`, `OPERATOR`, `ADMIN`), and permanently delete employees.
- **Identity Safety:** Protections against self-deletion or demoting the main administrator.

### ⏱ Automated Security (Watchdog Task)
- Background task running every 30 seconds (`asyncio.create_task`).
- Detects departure times and reminds the crew to confirm they are "On the road".
- Detects missing check-ins (exceeding check-in intervals) and pushes critical red alerts to the operators.

---

## 🛠 Tech Stack & Architecture

- **Runtime:** Python 3.10+
- **Framework:** `aiogram 3.x` (Asynchronous Telegram Bot API)
- **Database:** SQLite 3 (Utilizing `aiosqlite` for non-blocking I/O)
- **DB Modes:** `PRAGMA journal_mode = WAL;` (Write-Ahead Logging to prevent lockouts during high concurrent user activity & Watchdog loops) and `PRAGMA foreign_keys = ON;`.
- **Date & Time:** Timezone-aware handling centered around `Europe/Kyiv` (`zoneinfo` / DST safe) with DB persistence in standard UTC.
- **FSM:** State-based Finite State Machine for data gathering (aiogram FSM Context).
- **Interceptors:** Telegram `outer_middleware` for global private chat validation & role-access security.

---

## 📁 Repository Structure

```text
inso-tracking-bot/
├── bot/
│   ├── config.py              # Environment loading & core settings
│   ├── main.py                # Bot startup, middleware registration & main loop
│   ├── database/
│   │   ├── init_db.py         # Table creations, indexing, WAL initialization
│   │   └── queries.py         # DB transaction scripts (Users, Trips, Check-ins, SOS)
│   ├── handlers/
│   │   ├── admin.py           # Admin panel & User FSM Handlers
│   │   ├── operator.py        # Dashboard monitoring & Interactive state Handlers
│   │   └── traveler.py        # Planning Wizard & Trip action Handlers
│   ├── keyboards/
│   │   ├── inline.py          # Paginated lists, confirmation inline grids
│   │   └── reply.py           # Main reply menus
│   ├── middlewares/
│   │   └── auth.py            # Global Auth/Access outer-middleware
│   └── services/
│       └── watchdog.py        # Background alert & reminder checker task
├── data/
│   └── journey_db.sqlite3     # SQLite DB File (creates automatically)
├── .env.example               # Config template file
├── requirements.txt           # PIP dependencies
└── README.md                  # Project documentation```


##🚀 Installation & Setup
Follow these steps to run the bot on a local machine or server.

1. Clone the repository
```Bash
git clone https://github.com/hybr1dtheory/inso-tracking-bot.git
cd inso-tracking-bot```

2. Create and activate a Virtual Environment
```Bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate```

3. Install dependencies
```Bash
pip install --upgrade pip
pip install -r requirements.txt```

4. Configure Environment Variables
Create a .env file in the root directory based on .env.example:
```Python
# Telegram Bot API Token
BOT_TOKEN=1234567890:ABCdefGhIJKlmNoPQRsTUVwxyZ

# DB Path (defaults to data/journey_db.sqlite3)
DB_PATH=data/journey_db.sqlite3

# ID of the Monitoring Team Telegram Group (supergroups start with -100)
MONITORING_GROUP_ID=-1001234567890

# Pre-registered admin's Telegram ID
ADMIN_TG_ID=987654321
ADMIN_FULL_NAME=John Doe```

5. Running the Bot
Initiate the main script. On first startup, the required directory structure, indices, and database will initialize automatically:
```Bash
python bot/main.py```
To run as a system service in Linux (production), use systemd. You can find example service templates in the deployment guides.

##📝 Database Schema Overview
The database consists of 4 interrelated tables:
users: Standardizes demographics, role assignments (TRAVELER, OPERATOR, ADMIN), and organizational teams.
trips: Stores locations, standard departure parameters, check-in timeouts, pending operator pings, and SOS states.
trip_participants: Many-to-Many mapping for group assignments, designating a trip leader (is_leader = 1).
checkin_events: Full-audit historical log storing timeline data (TRIP_PLANNED, TRIP_STARTED, SELF_CHECKIN, OPERATOR_PING_SENT, REQUESTED_CHECKIN, HELP_REQUESTED, HELP_RESOLVED, TRIP_CLOSED, TRIP_CANCELLED).

##🔒 Security Notes
Any user who is not pre-registered in the database will be blocked, and they will only be able to view their Telegram ID.
Access to the /admin and /trips panels is rigorously scrutinized and cannot be bypassed.
Strict input validation prevents HTML Injection (such as < or &), which would otherwise crash Telegram rendering loops.

##🤝 Contributing
Contributions, issues, and feature requests are welcome! Feel free to check the issues page.