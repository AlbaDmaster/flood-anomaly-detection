# Flood Anomaly Detection System

An Isolation Forest based flood early warning and risk monitoring system for three flood-prone locations in Kenya: **Nairobi CBD**, **Kisumu** and **Tana River (Hola)**.

The system ingests daily environmental data from Open-Meteo, scores each day with a regional Isolation Forest model, and raises alerts:
- **Moderate** alerts appear on the web dashboard.
- **High** alerts are also emailed to responders, authorities and administrators.

A replay mode feeds past floods into the system one day at a time, for demonstrations.

Capstone project by Al-bashir Abdi (167024), BSc Computer Science (ICS 4E), Strathmore University. Supervisor: Ms. Juliet Kirui.

## Features

- **Data pipeline:** ten years (2015–2024) of daily rainfall, river discharge, soil moisture, humidity and temperature from the Open-Meteo Historical Weather API (ERA5-Land) and Flood API (GloFAS). Cleaning and 37 temporal features per day.
- **Models:** one Isolation Forest per region (200 trees, contamination 0.10, seed 42). Severity thresholds are set at the 90th percentile (moderate) and 99th percentile (high) of each region's scores.
- **Web dashboard:**
  - **Overview:** summary cards, an anomaly trend chart, a risk map and recent alerts.
  - **Regions:** charts of the anomaly score against its thresholds, and rainfall against river discharge.
  - **Alerts:** filters, acknowledgement and field feedback.
  - **Users:** administration of accounts and roles.
  - **Replay demo.**
- **Roles:** public, responder, authority and administrator. Passwords are stored as salted scrypt hashes, and access is checked on every page and API endpoint.
- **Email alerts:** each high alert is emailed once. Without mail settings, the emails are printed in the terminal instead.
- **Tests:** 112 automated tests (unit, integration, page and system), run against a separate test database.

## Project structure

```
app/               Flask application: API, pages, auth, ingestion, scoring, email, replay
  templates/       Dashboard pages
  static/          CSS, JavaScript, vendored Chart.js and Leaflet
src/data_pipeline/ Download, clean and explore the Open-Meteo data
src/features/      Temporal feature engineering
src/model/         Training, tuning, prediction, model inspection and the model report
data/              Cleaned dataset (master_dataset.csv) and features (features.csv)
models/            Trained models, one .joblib file per region
database/          MySQL schema, seed data and migrations
tests/             pytest test suite
run.py             Starts the web server
```

## Requirements

- Linux, or Windows with WSL (Ubuntu 24.04 recommended)
- Python 3.12 or later. Check with `python3 --version`: Ubuntu 22.04 ships 3.10, which is too old for the pinned pandas and NumPy.
- MySQL Server 8.0
- Git

On Ubuntu, install them with:

```bash
sudo apt update && sudo apt install -y git mysql-server python3-venv python3-pip
```

Several setup steps use `sudo` and will ask for your Ubuntu password.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/AlbaDmaster/flood-anomaly-detection.git
cd flood-anomaly-detection
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Create the database

Replace `choose-a-password` with a password of your own. You will put the same password in `.env` in step 3.

```bash
sudo service mysql start
sudo mysql -e "CREATE DATABASE IF NOT EXISTS flood_anomaly_detection CHARACTER SET utf8mb4;
  CREATE USER IF NOT EXISTS 'flood_app'@'localhost' IDENTIFIED BY 'choose-a-password';
  GRANT ALL PRIVILEGES ON flood_anomaly_detection.* TO 'flood_app'@'localhost';"
sudo mysql < database/schema.sql
sudo mysql < database/seed.sql
```

### 3. Configure

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
nano .env
```

In `.env`:
- set `DB_PASSWORD` to the password from step 2;
- set `SECRET_KEY` to the random string that the second command printed.

The mail settings are optional (see [Email alerts](#email-alerts)). `.env` is git-ignored, so never commit it.

### 4. Create the first administrator

```bash
flask --app run users create --role administrator
```

You will be asked for a name, email and password.

### 5. Run

```bash
python run.py
```

Open http://127.0.0.1:5000 and sign in. Anyone can register a public account from the sign-in page. Administrators create the other roles on the **Users** page.

For a live demo, start the server without auto-restart, so that saving a file does not stop a running replay:

```bash
python -c "from run import app; app.run(port=5000, debug=True, use_reloader=False)"
```

## Getting data into the system

All commands are run from the project folder with the virtual environment active. In a new terminal, activate it first:

```bash
cd flood-anomaly-detection && source venv/bin/activate
```

| Command | What it does |
|---|---|
| `flask --app run ingest-live` | Pulls the last 30 days from Open-Meteo, then scores them and raises alerts. Add `--days 92` for more history, or `--region tana_river_hola` for one region. |
| `flask --app run replay --start 2024-04-01 --end 2024-05-31` | Replays the April–May 2024 long rains for all regions. Add `--delay 2` to wait 2 seconds between days. |
| `flask --app run replay --start 2018-04-20 --end 2018-05-15 --region tana_river_hola` | Replays the May 2018 Tana River flood, which reaches **high** severity. |
| `flask --app run score` | Scores any readings that have not been scored yet. |
| `flask --app run notify` | Emails any high alerts that are still pending or failed. |
| `flask --app run reset-readings` | Deletes all readings, anomalies, alerts and feedback, for example before a demo. It asks for confirmation; add `--yes` to skip the prompt. |

The Open-Meteo archive is published a few days behind real time, so the newest live reading is usually a few days old.

## Replay demo

1. Sign in as an administrator and open **Replay demo**.
2. Choose **May 2018 Tana River flood**, leave **Clear all current readings** ticked, and click **Start replay**.
3. Open the **Overview** in another tab and watch it update every few seconds. High alerts appear from 1 May 2018, and each one sends an email.
4. Use **Pause**, **Resume**, **Stop** and the speed selector while the replay runs.

## Email alerts

Without mail settings, alert emails are printed in the terminal that runs the server or command. To send real email through Gmail:

1. Turn on 2-Step Verification on a personal Gmail account.
2. Create an App Password at https://myaccount.google.com/apppasswords.
3. Fill in `.env`:
   ```
   MAIL_SERVER=smtp.gmail.com
   MAIL_PORT=587
   MAIL_USERNAME=you@gmail.com
   MAIL_PASSWORD=your16letterapppassword
   MAIL_FROM=Flood EWS <you@gmail.com>
   ```
4. Check the settings: `flask --app run send-test-email --to you@gmail.com`

## The model

| Command | What it shows |
|---|---|
| `python src/model/show_model.py` | Settings, thresholds, features and first tree splits of the Tana River model, and how it scores a normal, a moderate and a high day. Add `nairobi` or `kisumu` for the other regions, or `--plot` for a chart. |
| `python src/model/model_report.py --tests` | Builds `reports/model_report.html`, a one-page report of the data, model, evaluation metrics, ROC curves and test results. Open it in a browser. From WSL: `explorer.exe "$(wslpath -w reports/model_report.html)"`. |
| `python src/model/train_and_evaluate.py` | Retrains the three models from `data/processed/features.csv` and prints their evaluation. With the fixed seed it reproduces the committed models. |
| `python src/model/tune_contamination.py` | Compares contamination values from 0.02 to 0.10. |

Evaluation against the known flood windows (2018, 2019, 2020 and 2023–24), at contamination 0.10:

| Region | Precision | Recall | F1-score | ROC-AUC | False-positive rate |
|---|---|---|---|---|---|
| Nairobi CBD | 0.463 | 0.291 | 0.358 | 0.739 | 0.064 |
| Kisumu | 0.364 | 0.229 | 0.281 | 0.714 | 0.076 |
| Tana River (Hola) | 0.597 | 0.376 | 0.461 | 0.844 | 0.048 |

## Running the tests

The tests use a separate database, so your data is never touched. Create it once:

```bash
sudo mysql -e "CREATE DATABASE IF NOT EXISTS flood_anomaly_detection_test CHARACTER SET utf8mb4;
  GRANT ALL PRIVILEGES ON flood_anomaly_detection_test.* TO 'flood_app'@'localhost';"
sed '/^USE /d' database/schema.sql | sudo mysql flood_anomaly_detection_test
sed '/^USE /d' database/seed.sql | sudo mysql flood_anomaly_detection_test
```

Then run:

```bash
python -m pytest tests/
```

All 112 tests should pass in about a minute. If tests are skipped, MySQL is not running: start it with `sudo service mysql start`.

## Upgrading an older database

Databases created before the `USER.is_active` column was added need the migration once:

```bash
sudo mysql flood_anomaly_detection < database/migrations/001_add_user_is_active.sql
```

## Data sources

- Open-Meteo Historical Weather API (ERA5-Land reanalysis): https://open-meteo.com/en/docs/historical-weather-api
- Open-Meteo Flood API (GloFAS river discharge): https://open-meteo.com/en/docs/flood-api
