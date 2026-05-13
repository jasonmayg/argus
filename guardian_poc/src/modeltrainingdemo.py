from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path
import re
from typing import Any

import joblib
import pandas as pd
import requests
from sklearn.feature_selection import SelectKBest, f_classif
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, precision_recall_curve, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


REPO_ROOT = Path(__file__).resolve().parents[2]
TRAINER_PATH = Path(__file__).resolve().parent / "train_google_risk_model.py"
DEFAULT_MODEL_PATH = REPO_ROOT / "guardian_poc" / "models" / "google_downside_risk_model.joblib"
DEFAULT_METRICS_PATH = REPO_ROOT / "guardian_poc" / "models" / "google_downside_risk_metrics.json"
DEMO_MODEL_PATH = REPO_ROOT / "guardian_poc" / "models" / "google_downside_risk_model_demo.joblib"
DEMO_METRICS_PATH = REPO_ROOT / "guardian_poc" / "models" / "google_downside_risk_metrics_demo.json"
DEMO_GCS_FEATURES_PATH = REPO_ROOT / "guardian_poc" / "models" / "google_downside_gcs_features_demo.csv"
ALPHA_VANTAGE_BASE_URL = "https://www.alphavantage.co/query"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_trainer_module():
    spec = importlib.util.spec_from_file_location("train_google_risk_model", str(TRAINER_PATH))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load trainer module from {TRAINER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _resolve_dataset_file(path_str: str) -> str:
    candidate = Path(path_str).expanduser()
    if candidate.exists():
        return str(candidate)

    dataset_env = os.getenv("ARGUS_DATASET_DIR", "").strip()
    search_dirs: list[Path] = []
    if dataset_env:
        search_dirs.append(Path(dataset_env).expanduser())

    search_dirs.extend(
        [
            REPO_ROOT / "Dataset",
            REPO_ROOT.parent / "Dataset",
            Path.home() / "Desktop" / "argus-main" / "Dataset",
            Path.home() / "Desktop" / "Alphabet " / "Dataset",
            Path.home() / "Desktop" / "Alphabet" / "Dataset",
        ]
    )

    for base in search_dirs:
        resolved = base / candidate.name
        if resolved.exists():
            return str(resolved)
    return str(candidate)


def _resolve_gcs_key_file() -> str | None:
    env_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if env_path and Path(env_path).expanduser().exists():
        return str(Path(env_path).expanduser())

    home = Path.home()
    candidates = [
        REPO_ROOT / "keys" / "treasury-reader.json",
        REPO_ROOT / "keys" / "service-account.json",
        home / "Desktop" / "argus-main" / "keys" / "treasury-reader.json",
        home / "Desktop" / "argus-main" / "keys" / "service-account.json",
        home / "Desktop" / "Alphabet " / "keys" / "treasury-reader.json",
        home / "Desktop" / "Alphabet " / "keys" / "service-account.json",
        home / "Desktop" / "Alphabet" / "keys" / "treasury-reader.json",
        home / "Desktop" / "Alphabet" / "keys" / "service-account.json",
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def _resolve_alpha_vantage_key(cli_value: str | None = None) -> str | None:
    if cli_value:
        return cli_value
    value = os.getenv("ALPHA_VANTAGE_API_KEY", "").strip()
    return value or None


def _resolve_sec_user_agent(cli_value: str | None = None) -> str:
    if cli_value:
        return cli_value
    return os.getenv("SEC_USER_AGENT", "ARGUS-modeltrainingdemo/1.0 contact@example.com").strip()


def summarize_existing_model(model_path: Path, metrics_path: Path) -> dict[str, Any]:
    artifact = joblib.load(model_path)
    metrics = json.loads(metrics_path.read_text(encoding="utf-8")) if metrics_path.exists() else {}
    feature_columns = artifact.get("feature_columns", []) if isinstance(artifact, dict) else []
    model = artifact.get("model") if isinstance(artifact, dict) else artifact
    return {
        "artifact_type": type(artifact).__name__,
        "model_name": artifact.get("model_name", "unknown") if isinstance(artifact, dict) else type(model).__name__,
        "threshold": float(artifact.get("threshold", 0.5)) if isinstance(artifact, dict) else 0.5,
        "feature_count": len(feature_columns),
        "first_features": feature_columns[:15],
        "pipeline_type": type(model).__name__,
        "test_auc": metrics.get("test_auc"),
        "cv_auc": metrics.get("selected_model_cv_auc"),
        "target_horizon_days": metrics.get("target_horizon_days"),
        "rows_total": metrics.get("rows_total"),
        "test_period_end": metrics.get("test_period_end"),
    }


def _build_demo_feature_columns(df: pd.DataFrame, include_gcs: bool = True) -> list[str]:
    drop_cols = {
        "date",
        "target_ret_next_1d",
        "target_ret_next_5d",
        "target_ret_next_3d",
        "target_downside_risk",
        "target_horizon_days",
    }
    cols = [c for c in df.columns if c not in drop_cols]
    if not include_gcs:
        cols = [c for c in cols if not c.startswith("gcs_")]
    return cols


def _engineer_price_features(raw_prices: pd.DataFrame) -> pd.DataFrame:
    prices = raw_prices.copy()
    prices["date"] = pd.to_datetime(prices["date"], utc=False, errors="coerce")
    for c in ["open", "high", "low", "close", "volume"]:
        prices[c] = pd.to_numeric(prices[c], errors="coerce")
    prices = prices.sort_values("date").dropna(subset=["date", "close"])
    prices["ret_1d"] = prices["close"].pct_change()
    prices["ret_5d"] = prices["close"].pct_change(5)
    prices["vol_5d"] = prices["ret_1d"].rolling(5).std()
    prices["vol_20d"] = prices["ret_1d"].rolling(20).std()
    prices["range_pct"] = (prices["high"] - prices["low"]) / prices["close"]
    vol_mean_20 = prices["volume"].rolling(20).mean()
    vol_std_20 = prices["volume"].rolling(20).std().replace(0, pd.NA)
    prices["volume_z_20d"] = (prices["volume"] - vol_mean_20) / vol_std_20
    prices["target_ret_next_1d"] = prices["ret_1d"].shift(-1)
    prices["target_ret_next_5d"] = prices["close"].pct_change(5).shift(-5)
    return prices


def _fetch_alpha_vantage_daily(symbol: str, api_key: str) -> dict[str, Any]:
    response = requests.get(
        ALPHA_VANTAGE_BASE_URL,
        params={
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": "compact",
            "apikey": api_key,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    series = payload.get("Time Series (Daily)") or {}
    if not series:
        raise RuntimeError(f"Alpha Vantage daily series unavailable: {payload}")
    latest_date = sorted(series.keys())[-1]
    row = series[latest_date]
    return {
        "date": latest_date,
        "open": float(row["1. open"]),
        "high": float(row["2. high"]),
        "low": float(row["3. low"]),
        "close": float(row["4. close"]),
        "volume": float(row["5. volume"]),
    }


def _aggregate_news_for_date(feed: list[dict[str, Any]], target_date: str, keyword_map: dict[str, Any]) -> dict[str, Any]:
    items = []
    for entry in feed:
        published = str(entry.get("time_published") or "").strip()
        if not published:
            continue
        published_date = published[:8]
        if published_date != target_date.replace("-", ""):
            continue
        title = str(entry.get("title") or "")
        summary = str(entry.get("summary") or "")
        items.append((title + " " + summary).lower())

    out = {"date": pd.to_datetime(target_date), "news_count": len(items)}
    for key, cfg in keyword_map.items():
        primaries = [str(x).lower() for x in cfg.get("primary", [])]
        highs = [str(x).lower() for x in cfg.get("high_alert", [])]
        out[f"news_{key}_primary"] = sum(sum(1 for kw in primaries if kw and kw in text) for text in items)
        out[f"news_{key}_high"] = sum(sum(1 for kw in highs if kw and kw in text) for text in items)
    return out


def _fetch_alpha_vantage_news(symbol: str, api_key: str, target_date: str, keyword_map: dict[str, Any]) -> pd.DataFrame:
    response = requests.get(
        ALPHA_VANTAGE_BASE_URL,
        params={
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "time_from": target_date.replace("-", "") + "T0000",
            "sort": "LATEST",
            "limit": 200,
            "apikey": api_key,
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    feed = payload.get("feed") or []
    aggregated = _aggregate_news_for_date(feed, target_date, keyword_map)
    return pd.DataFrame([aggregated])


def _latest_sec_fact(facts: dict[str, Any], concept_names: list[str]) -> float | None:
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    candidates = []
    for concept in concept_names:
        concept_payload = us_gaap.get(concept) or {}
        for unit_name, rows in (concept_payload.get("units") or {}).items():
            if unit_name.lower() != "usd":
                continue
            for row in rows:
                form = str(row.get("form") or "")
                val = row.get("val")
                end = str(row.get("end") or "")
                filed = str(row.get("filed") or "")
                if form not in {"10-K", "10-Q"} or val is None:
                    continue
                candidates.append((end, filed, float(val)))
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][2]


def _fetch_sec_financial_ratios(cik: str, user_agent: str) -> dict[str, float | str] | None:
    response = requests.get(
        SEC_COMPANYFACTS_URL.format(cik=str(cik).zfill(10)),
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate", "Host": "data.sec.gov"},
        timeout=30,
    )
    response.raise_for_status()
    facts = response.json()

    cash = _latest_sec_fact(facts, ["CashAndCashEquivalentsAtCarryingValue"])
    cur_assets = _latest_sec_fact(facts, ["AssetsCurrent"])
    cur_liab = _latest_sec_fact(facts, ["LiabilitiesCurrent"])
    total_liab = _latest_sec_fact(facts, ["Liabilities"])
    equity = _latest_sec_fact(facts, ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"])
    revenue = _latest_sec_fact(facts, ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"])
    op_income = _latest_sec_fact(facts, ["OperatingIncomeLoss"])
    op_cf = _latest_sec_fact(facts, ["NetCashProvidedByUsedInOperatingActivities"])
    capex = _latest_sec_fact(facts, ["PaymentsToAcquirePropertyPlantAndEquipment", "CapitalExpendituresIncurredButNotYetPaid"])
    net_income = _latest_sec_fact(facts, ["NetIncomeLoss"])

    if not any(v is not None for v in [cash, cur_assets, cur_liab, total_liab, equity, revenue]):
        return None

    def safe_div(num: float | None, den: float | None) -> float:
        if num is None or den in (None, 0):
            return 0.0
        return float(num) / float(den)

    return {
        "fin_current_ratio": safe_div(cur_assets, cur_liab),
        "fin_cash_to_current_liab": safe_div(cash, cur_liab),
        "fin_leverage": safe_div(total_liab, equity),
        "fin_operating_margin": safe_div(op_income, revenue),
        "fin_ocf_margin": safe_div(op_cf, revenue),
        "fin_fcf_proxy": safe_div((op_cf or 0.0) - (capex or 0.0), revenue),
        "fin_net_margin": safe_div(net_income, revenue),
    }


def _train_demo_model(
    df: pd.DataFrame,
    horizon_days: int,
    include_gcs: bool,
    recent_years: int | None,
    select_k: int | None,
    logistic_c: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    work = df.sort_values("date").copy()
    if recent_years:
        cutoff = work["date"].max() - pd.Timedelta(days=365 * recent_years)
        work = work[work["date"] >= cutoff].copy()

    feature_columns = _build_demo_feature_columns(work, include_gcs=include_gcs)
    non_constant = []
    for col in feature_columns:
        series = pd.to_numeric(work[col], errors="coerce")
        if series.nunique(dropna=True) > 1:
            non_constant.append(col)
    feature_columns = non_constant

    X = work[feature_columns]
    y = work["target_downside_risk"]
    split_idx = int(len(work) * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    steps = [
        ("imputer", SimpleImputer(strategy="median")),
        ("scaler", StandardScaler()),
    ]
    if select_k:
        steps.append(("selector", SelectKBest(score_func=f_classif, k=min(select_k, len(feature_columns)))))
    steps.append(
        (
            "clf",
            LogisticRegression(
                max_iter=3000,
                class_weight="balanced",
                solver="lbfgs",
                C=logistic_c,
            ),
        )
    )
    model = Pipeline(steps=steps)
    model.fit(X_train, y_train)
    train_prob = model.predict_proba(X_train)[:, 1]
    prob = model.predict_proba(X_test)[:, 1]

    precision, recall, thresholds = precision_recall_curve(y_train, train_prob)
    best_threshold = 0.5
    best_f1 = -1.0
    for idx, threshold in enumerate(thresholds):
        p = precision[idx + 1]
        r = recall[idx + 1]
        if p + r == 0:
            continue
        f1 = 2 * p * r / (p + r)
        if f1 > best_f1:
            best_f1 = f1
            best_threshold = float(threshold)

    pred = (prob >= best_threshold).astype(int)

    target_col = f"target_ret_next_{horizon_days}d"
    metrics = {
        "rows_total": int(len(work)),
        "rows_train": int(len(X_train)),
        "rows_test": int(len(X_test)),
        "target_horizon_days": int(horizon_days),
        "target_column": target_col,
        "target_quantile_20_threshold": float(work[target_col].quantile(0.20)) if target_col in work.columns else None,
        "selected_model": "demo_logistic_regression",
        "selected_model_cv_auc": None,
        "test_auc": float(roc_auc_score(y_test, prob)) if y_test.nunique() > 1 else float("nan"),
        "decision_threshold": best_threshold,
        "train_best_f1": best_f1,
        "classification_report": classification_report(y_test, pred, output_dict=True, zero_division=0),
        "test_period_start": str(work.iloc[split_idx]["date"].date()) if len(work) > split_idx else None,
        "test_period_end": str(work.iloc[-1]["date"].date()) if len(work) > 0 else None,
    }
    artifact = {
        "model": model,
        "model_name": "demo_logistic_regression",
        "feature_columns": feature_columns,
        "threshold": best_threshold,
        "feature_means": X_train.apply(pd.to_numeric, errors="coerce").mean().fillna(0.0).to_dict(),
        "feature_medians": X_train.apply(pd.to_numeric, errors="coerce").median().fillna(0.0).to_dict(),
    }
    return artifact, metrics


def _build_demo_frame(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    trainer = _load_trainer_module()
    prices_path = _resolve_dataset_file(args.prices)
    news_path = _resolve_dataset_file(args.news)
    balance_path = _resolve_dataset_file(args.balance)
    cashflow_path = _resolve_dataset_file(args.cashflow)
    income_path = _resolve_dataset_file(args.income)

    df = trainer.build_training_frame(
        prices_path=prices_path,
        news_path=news_path,
        balance_path=balance_path,
        cashflow_path=cashflow_path,
        income_path=income_path,
        keywords_cfg_path=args.keywords_config,
        horizon_days=args.horizon_days,
        target_quantile=args.target_quantile,
    )
    if not args.no_gcs:
        return _augment_training_frame_with_gcs(df)
    return df, {"gcs_enabled": False, "gcs_skipped": True}


def score_latest_demo_row(args: argparse.Namespace) -> dict[str, Any]:
    artifact = joblib.load(args.output_model)
    model = artifact["model"]
    feature_columns = artifact.get("feature_columns", [])
    threshold = float(artifact.get("threshold", 0.5))

    df, gcs_meta = _build_demo_frame(args)
    work = df.sort_values("date").copy()
    if args.recent_years:
        cutoff = work["date"].max() - pd.Timedelta(days=365 * args.recent_years)
        work = work[work["date"] >= cutoff].copy()

    latest = work.iloc[-1:].copy()
    for col in feature_columns:
        if col not in latest.columns:
            latest[col] = 0.0
    X = latest[feature_columns]
    prob = float(model.predict_proba(X)[0][1])
    level = "High" if prob >= 0.7 else ("Medium" if prob >= 0.4 else "Low")

    feature_values = []
    for col in feature_columns[:12]:
        value = pd.to_numeric(latest[col], errors="coerce").iloc[0]
        if pd.notna(value):
            feature_values.append({"name": col, "value": round(float(value), 6)})

    return {
        "as_of_date": str(latest["date"].iloc[0].date()),
        "probability": round(prob, 6),
        "percent": round(prob * 100, 2),
        "level": level,
        "decision_threshold": threshold,
        "predicted_positive": bool(prob >= threshold),
        "feature_sample": feature_values,
        "gcs_meta": gcs_meta,
    }


def score_latest_gcs_demo_row(args: argparse.Namespace) -> dict[str, Any]:
    """
    Score the latest cloud data without rebuilding the historical training frame.
    The trained model is loaded once, the latest GCS partition supplies daily
    macro features, and daily risk scores adjust the final index.
    """
    artifact = joblib.load(args.output_model)
    model = artifact["model"]
    feature_columns = artifact.get("feature_columns", [])
    threshold = float(artifact.get("threshold", 0.5))
    feature_means = artifact.get("feature_means", {}) if isinstance(artifact, dict) else {}

    gcs_features, gcs_meta = _load_latest_gcs_feature_row()
    if gcs_features.empty:
        raise RuntimeError("GCS feature frame is empty")

    latest = gcs_features.sort_values("date").iloc[-1:].copy()
    missing_feature_count = 0
    for col in feature_columns:
        if col not in latest.columns:
            latest[col] = feature_means.get(col, float("nan"))
            missing_feature_count += 1

    X = latest[feature_columns]
    base_prob = float(model.predict_proba(X)[0][1])
    daily_scores = _load_daily_risk_scores_from_outputs()
    risk_score_pressure = _daily_risk_score_pressure(latest.iloc[0], daily_scores)
    risk_weight = _bounded_float_env("DOWNSIDE_INDEX_RISK_SCORE_WEIGHT", 0.35, 0.0, 1.0)
    prob = (base_prob * (1.0 - risk_weight)) + (risk_score_pressure * risk_weight)
    level = "High" if prob >= 0.7 else ("Medium" if prob >= 0.4 else "Low")

    feature_values = []
    for col in feature_columns:
        if len(feature_values) >= 12:
            break
        value = pd.to_numeric(latest[col], errors="coerce").iloc[0]
        if pd.notna(value) and float(value) != 0.0:
            feature_values.append({"name": col, "value": round(float(value), 6)})

    return {
        "as_of_date": str(latest["date"].iloc[0].date()),
        "probability": round(prob, 6),
        "percent": round(prob * 100, 2),
        "level": level,
        "base_model_probability": round(base_prob, 6),
        "base_model_percent": round(base_prob * 100, 2),
        "daily_risk_score_pressure": round(risk_score_pressure, 6),
        "daily_risk_score_pressure_percent": round(risk_score_pressure * 100, 2),
        "daily_risk_score_count": len(daily_scores),
        "daily_risk_score_weight": risk_weight,
        "uses_daily_risk_score": True,
        "decision_threshold": threshold,
        "predicted_positive": bool(prob >= threshold),
        "feature_sample": feature_values,
        "gcs_meta": gcs_meta,
        "score_mode": "gcs_latest_plus_daily_risk_score",
        "missing_feature_count": missing_feature_count,
        "missing_feature_fill": "artifact_feature_mean_or_model_imputer",
    }


def _bounded_float_env(name: str, default: float, min_value: float, max_value: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except Exception:
        value = default
    return max(min_value, min(max_value, value))


def _extract_scores(obj: Any) -> list[float]:
    scores: list[float] = []
    if isinstance(obj, dict):
        for key in ("score", "risk_score", "effective_score", "max_score", "probability", "risk_probability"):
            if key in obj and obj.get(key) not in (None, ""):
                value = pd.to_numeric(pd.Series([obj.get(key)]), errors="coerce").iloc[0]
                if pd.notna(value):
                    score = float(value)
                    if 0 < score <= 1:
                        score *= 100.0
                    scores.append(max(0.0, min(100.0, score)))
        for value in obj.values():
            if isinstance(value, (dict, list)):
                scores.extend(_extract_scores(value))
    elif isinstance(obj, list):
        for item in obj:
            scores.extend(_extract_scores(item))
    return scores


def _load_daily_risk_scores_from_outputs() -> list[float]:
    scores: list[float] = []
    for path in [REPO_ROOT / "shared_storage" / "latest_results.json", REPO_ROOT / "shared_storage" / "latest_actions.json"]:
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        scores.extend(_extract_scores(payload))
    return scores


def _daily_risk_score_pressure(row: pd.Series, daily_scores: list[float]) -> float:
    """
    Convert the cloud-produced daily risk scores into a 0-1 pressure signal.
    This ties the index to the daily risk-score model while preserving the
    trained downside model as the base signal.
    """
    alert_count = max(float(row.get("gcs_raw_alert_count", 0.0) or 0.0), 0.0)
    mean_score = max(float(row.get("gcs_raw_alert_score_mean", 0.0) or 0.0), 0.0) / 100.0
    max_score = max(float(row.get("gcs_raw_alert_score_max", 0.0) or 0.0), 0.0) / 100.0
    triggered = max(float(row.get("gcs_raw_triggered_count", 0.0) or 0.0), 0.0)
    triggered_ratio = (triggered / alert_count) if alert_count > 0 else 0.0

    if daily_scores:
        series = pd.Series(daily_scores, dtype="float64").clip(lower=0.0, upper=100.0)
        mean_score = max(mean_score, float(series.mean()) / 100.0)
        max_score = max(max_score, float(series.max()) / 100.0)
        triggered_ratio = max(triggered_ratio, float((series >= 40.0).mean()))

    pressure = (0.45 * min(max_score, 1.0)) + (0.35 * min(mean_score, 1.0)) + (0.20 * min(triggered_ratio, 1.0))
    return max(0.0, min(1.0, pressure))


def _build_live_demo_frame(args: argparse.Namespace) -> tuple[pd.DataFrame, dict[str, Any]]:
    trainer = _load_trainer_module()
    keyword_map = trainer.load_yaml(args.keywords_config)["risk_taxonomy"]

    base_prices = trainer.load_prices(_resolve_dataset_file(args.prices))
    price_raw = base_prices[["date", "open", "high", "low", "close", "volume"]].copy()
    price_raw["date"] = pd.to_datetime(price_raw["date"], utc=False, errors="coerce")

    live_meta: dict[str, Any] = {"live_api_enabled": True}
    alpha_key = _resolve_alpha_vantage_key(args.alpha_vantage_key)
    if not alpha_key:
        raise RuntimeError("Missing Alpha Vantage API key. Set ALPHA_VANTAGE_API_KEY or pass --alpha-vantage-key.")

    latest_price = _fetch_alpha_vantage_daily(args.symbol, alpha_key)
    live_date = pd.to_datetime(latest_price["date"], utc=False)
    price_raw = price_raw[price_raw["date"] < live_date]
    price_raw = pd.concat([price_raw, pd.DataFrame([latest_price])], ignore_index=True)
    prices = _engineer_price_features(price_raw)
    live_meta["live_price_date"] = str(live_date.date())

    news = trainer.load_news(_resolve_dataset_file(args.news), keyword_map)
    news["date"] = pd.to_datetime(news["date"], utc=False, errors="coerce")
    live_news = _fetch_alpha_vantage_news(args.symbol, alpha_key, str(live_date.date()), keyword_map)
    news = news[news["date"] < live_date]
    news = pd.concat([news, live_news], ignore_index=True)
    live_meta["live_news_count"] = int(live_news["news_count"].iloc[0])

    fin = trainer.load_financials(
        _resolve_dataset_file(args.balance),
        _resolve_dataset_file(args.cashflow),
        _resolve_dataset_file(args.income),
    )
    fin["date"] = pd.to_datetime(fin["date"], utc=False, errors="coerce")
    sec_ratios = _fetch_sec_financial_ratios(args.sec_cik, _resolve_sec_user_agent(args.sec_user_agent))
    if sec_ratios:
        sec_frame = pd.DataFrame([{"date": live_date, **sec_ratios}])
        fin = fin[fin["date"] < live_date]
        fin = pd.concat([fin, sec_frame], ignore_index=True)
        live_meta["live_sec_ratios_loaded"] = True
    else:
        live_meta["live_sec_ratios_loaded"] = False

    df = prices.merge(news, on="date", how="left")
    df = pd.merge_asof(df.sort_values("date"), fin.sort_values("date"), on="date", direction="backward")
    df = df.fillna(0)

    if not args.no_gcs:
        gcs_features, gcs_meta = _load_or_build_gcs_feature_frame(
            DEMO_GCS_FEATURES_PATH,
            force_refresh=os.getenv("MODELTRAININGDEMO_REFRESH_GCS", "").strip().lower() in {"1", "true", "yes"},
        )
        df = pd.merge_asof(df.sort_values("date"), gcs_features.sort_values("date"), on="date", direction="backward")
        gcs_cols = [c for c in df.columns if c.startswith("gcs_")]
        if gcs_cols:
            df[gcs_cols] = df[gcs_cols].fillna(0)
    else:
        gcs_meta = {"gcs_enabled": False, "gcs_skipped": True}

    live_meta.update(gcs_meta)
    return df, live_meta


def score_live_demo_row(args: argparse.Namespace) -> dict[str, Any]:
    artifact = joblib.load(args.output_model)
    model = artifact["model"]
    feature_columns = artifact.get("feature_columns", [])
    threshold = float(artifact.get("threshold", 0.5))

    df, live_meta = _build_live_demo_frame(args)
    latest = df.sort_values("date").iloc[-1:].copy()
    for col in feature_columns:
        if col not in latest.columns:
            latest[col] = 0.0
    X = latest[feature_columns]
    prob = float(model.predict_proba(X)[0][1])
    level = "High" if prob >= 0.7 else ("Medium" if prob >= 0.4 else "Low")

    feature_values = []
    for col in feature_columns[:12]:
        value = pd.to_numeric(latest[col], errors="coerce").iloc[0]
        if pd.notna(value):
            feature_values.append({"name": col, "value": round(float(value), 6)})

    top_drivers: list[dict[str, Any]] = []
    try:
        imputer = model.named_steps.get("imputer")
        scaler = model.named_steps.get("scaler")
        selector = model.named_steps.get("selector")
        clf = model.named_steps.get("clf")

        X_imp = imputer.transform(X) if imputer is not None else X.to_numpy()
        selected_names = list(feature_columns)
        X_sel = X_imp
        if selector is not None:
            support = selector.get_support()
            selected_names = [name for name, keep in zip(feature_columns, support) if keep]
            X_sel = selector.transform(scaler.transform(X_imp) if scaler is not None else X_imp)
            X_scaled = X_sel
        else:
            X_scaled = scaler.transform(X_imp) if scaler is not None else X_imp

        coefs = getattr(clf, "coef_", None)
        if coefs is not None and len(selected_names) == X_scaled.shape[1]:
            contributions = []
            for idx, name in enumerate(selected_names):
                contrib = float(X_scaled[0, idx] * coefs[0][idx])
                raw_val = pd.to_numeric(latest[name], errors="coerce").iloc[0]
                contributions.append(
                    {
                        "name": name,
                        "value": None if pd.isna(raw_val) else round(float(raw_val), 6),
                        "contribution": round(contrib, 6),
                        "direction": "up" if contrib >= 0 else "down",
                    }
                )
            top_drivers = sorted(contributions, key=lambda item: abs(item["contribution"]), reverse=True)[:8]
    except Exception:
        top_drivers = []

    summary_lines = [
        f"Downside risk for {latest['date'].iloc[0].date()} is {prob * 100:.2f}% ({level}).",
        f"Decision threshold is {threshold:.4f}, so the current signal is {'triggered' if prob >= threshold else 'not triggered'}.",
    ]
    if top_drivers:
        driver_bits = []
        for item in top_drivers[:5]:
            sign = "+" if item["contribution"] >= 0 else ""
            driver_bits.append(f"{item['name']} ({sign}{item['contribution']})")
        summary_lines.append("Top model drivers: " + ", ".join(driver_bits) + ".")
    if live_meta.get("live_news_count") == 0:
        summary_lines.append("No same-day Alpha Vantage news items were matched for the ticker.")

    return {
        "as_of_date": str(latest["date"].iloc[0].date()),
        "probability": round(prob, 6),
        "percent": round(prob * 100, 2),
        "level": level,
        "decision_threshold": threshold,
        "predicted_positive": bool(prob >= threshold),
        "summary": " ".join(summary_lines),
        "feature_sample": feature_values,
        "top_drivers": top_drivers,
        "live_meta": live_meta,
    }


def _load_gcs_feature_frame() -> tuple[pd.DataFrame, dict[str, Any]]:
    key_path = _resolve_gcs_key_file()
    if key_path and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_path

    from data_dashboard import (
        _classify_dataset,
        _get_gcs_bucket,
        _infer_indicator_from_filename,
        _read_gcs_csv_text,
        _build_series_from_text,
    )
    from core.guardian_loader import _discover_raw_dt_segments, _read_alerts_from_gcs_raw_csvs

    bucket = _get_gcs_bucket()
    raw_base_prefix = os.getenv("GUARDIAN_GCS_RAW_PREFIX", "raw").strip().strip("/") or "raw"
    root_prefix = os.getenv("ARGUS_GCS_ROOT_PREFIX", "").strip().strip("/")
    history_root = f"{root_prefix}/{raw_base_prefix}" if root_prefix else raw_base_prefix
    dt_segments = _discover_raw_dt_segments(bucket, history_root)
    blobs = [b for b in bucket.list_blobs(prefix=history_root.rstrip("/") + "/") if str(b.name).lower().endswith(".csv")]

    merged: pd.DataFrame | None = None
    meta = {
        "gcs_key_path": key_path,
        "gcs_prefix": history_root,
        "gcs_latest_dt": dt_segments[-1] if dt_segments else None,
        "gcs_dt_count": len(dt_segments),
        "gcs_blob_count": len(blobs),
        "gcs_series_features": 0,
        "gcs_raw_dt_segments": len(dt_segments),
        "gcs_raw_alert_rows": 0,
    }

    series_frames: dict[str, list[pd.DataFrame]] = {}
    for blob in sorted(blobs, key=lambda x: x.name):
        stem = Path(blob.name).stem
        ds_type = _classify_dataset(stem)
        try:
            text = _read_gcs_csv_text(bucket, blob.name)
            series = _build_series_from_text(Path(blob.name).name, text)
        except Exception:
            continue
        dates = series.get("dates") or []
        values = series.get("values") or []
        if not dates or not values:
            continue

        indicator = _infer_indicator_from_filename(stem)
        feature_name = f"gcs_{ds_type}_{indicator}"
        frame = pd.DataFrame(
            {
                "date": pd.to_datetime(dates, utc=False, errors="coerce"),
                feature_name: pd.to_numeric(values, errors="coerce"),
            }
        ).dropna(subset=["date"])
        if frame.empty:
            continue
        series_frames.setdefault(feature_name, []).append(frame)

    for feature_name, frames in sorted(series_frames.items()):
        combined = pd.concat(frames, ignore_index=True)
        combined = combined.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        series = combined[feature_name].sort_index()
        combined[f"{feature_name}_chg_1"] = combined[feature_name].pct_change(fill_method=None).replace([float("inf"), float("-inf")], pd.NA)
        combined[f"{feature_name}_roll_5"] = combined[feature_name].rolling(5, min_periods=1).mean()
        combined[f"{feature_name}_roll_20"] = combined[feature_name].rolling(20, min_periods=1).mean()
        combined[f"{feature_name}_z_20"] = (
            (combined[feature_name] - combined[feature_name].rolling(20, min_periods=5).mean())
            / combined[feature_name].rolling(20, min_periods=5).std()
        )
        cols = ["date", feature_name, f"{feature_name}_chg_1", f"{feature_name}_roll_5"]
        feature_frame = combined[cols]
        if merged is None:
            merged = feature_frame
        else:
            merged = pd.merge(merged, feature_frame, on="date", how="outer")
        meta["gcs_series_features"] += len(cols) - 1

    raw_rows: list[dict[str, Any]] = []
    for dt_seg in dt_segments:
        raw_prefix = f"{history_root}/{dt_seg}".strip("/")
        try:
            raw_rows.extend(_read_alerts_from_gcs_raw_csvs(bucket, raw_prefix))
        except Exception:
            continue

    meta["gcs_raw_alert_rows"] = len(raw_rows)
    if raw_rows:
        raw_df = pd.DataFrame(raw_rows)
        raw_df["date"] = (
            raw_df["raw_dt"]
            .astype(str)
            .str.replace("dt=", "", regex=False)
        )
        raw_df["date"] = pd.to_datetime(raw_df["date"], utc=False, errors="coerce")
        raw_df["score"] = pd.to_numeric(raw_df.get("score"), errors="coerce").fillna(0.0)
        by_day = raw_df.dropna(subset=["date"]).groupby("date").agg(
            gcs_raw_alert_count=("score", "size"),
            gcs_raw_alert_score_mean=("score", "mean"),
            gcs_raw_alert_score_max=("score", "max"),
            gcs_raw_triggered_count=("score", lambda s: int((s >= 40).sum())),
            gcs_raw_source_count=("source", lambda s: int(pd.Series(s).fillna("").astype(str).nunique())),
        ).reset_index()

        top_cat_rows = []
        for date, grp in raw_df.dropna(subset=["date"]).groupby("date"):
            counts = Counter(str(x).strip() for x in grp.get("category_label", []) if str(x).strip())
            top_label, top_count = counts.most_common(1)[0] if counts else ("", 0)
            top_cat_rows.append(
                {
                    "date": date,
                    "gcs_raw_top_category_count": float(top_count),
                    "gcs_raw_has_top_category": float(1 if top_label else 0),
                }
            )
        by_day = by_day.merge(pd.DataFrame(top_cat_rows), on="date", how="left")

        if merged is None:
            merged = by_day
        else:
            merged = pd.merge(merged, by_day, on="date", how="outer")

    if merged is None:
        return pd.DataFrame(columns=["date"]), meta
    merged = merged.sort_values("date").reset_index(drop=True)
    return merged, meta


def _load_latest_gcs_feature_row() -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Build one feature row from the latest GCS dt partition only.
    This is the daily scoring path: no retraining and no historical scan.
    """
    key_path = _resolve_gcs_key_file()
    if key_path and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_path

    from data_dashboard import (
        _classify_dataset,
        _get_gcs_bucket,
        _infer_indicator_from_filename,
        _read_gcs_csv_text,
        _build_series_from_text,
    )
    from core.guardian_loader import _discover_raw_dt_segments, _read_alerts_from_gcs_raw_csvs

    bucket = _get_gcs_bucket()
    raw_base_prefix = os.getenv("GUARDIAN_GCS_RAW_PREFIX", "raw").strip().strip("/") or "raw"
    root_prefix = os.getenv("ARGUS_GCS_ROOT_PREFIX", "").strip().strip("/")
    history_root = f"{root_prefix}/{raw_base_prefix}" if root_prefix else raw_base_prefix
    dt_segments = _discover_raw_dt_segments(bucket, history_root)
    fallback_used = False
    fallback_candidates: list[str] = []

    if dt_segments:
        latest_dt = dt_segments[-1]
        latest_prefix = f"{history_root.rstrip('/')}/{latest_dt}/"
        blobs = [b for b in bucket.list_blobs(prefix=latest_prefix) if str(b.name).lower().endswith(".csv")]
    else:
        today = pd.Timestamp.utcnow().date()
        fallback_candidates = [
            f"dt={today.strftime('%Y-%m-%d')}",
            f"dt={(today - pd.Timedelta(days=1)).strftime('%Y-%m-%d')}",
        ]
        latest_dt = ""
        latest_prefix = ""
        blobs = []
        for candidate_dt in fallback_candidates:
            candidate_prefix = f"{history_root.rstrip('/')}/{candidate_dt}/"
            candidate_blobs = [b for b in bucket.list_blobs(prefix=candidate_prefix) if str(b.name).lower().endswith(".csv")]
            if candidate_blobs:
                latest_dt = candidate_dt
                latest_prefix = candidate_prefix
                blobs = candidate_blobs
                fallback_used = True
                break

    if not latest_dt:
        return pd.DataFrame(columns=["date"]), {
            "gcs_enabled": False,
            "gcs_key_path": key_path,
            "gcs_prefix": history_root,
            "gcs_error": "no GCS dt partitions found",
            "gcs_dt_fallback_candidates": fallback_candidates,
        }

    latest_date = pd.to_datetime(latest_dt.replace("dt=", ""), errors="coerce")
    row: dict[str, Any] = {"date": latest_date}
    meta = {
        "gcs_enabled": True,
        "gcs_key_path": key_path,
        "gcs_prefix": history_root,
        "gcs_latest_dt": latest_dt,
        "gcs_dt_count": len(dt_segments),
        "gcs_blob_count": len(blobs),
        "gcs_series_features": 0,
        "gcs_raw_alert_rows": 0,
        "gcs_latest_only": True,
        "gcs_dt_fallback_used": fallback_used,
        "gcs_dt_fallback_candidates": fallback_candidates,
    }

    for blob in sorted(blobs, key=lambda x: x.name):
        stem = Path(blob.name).stem
        ds_type = _classify_dataset(stem)
        try:
            text = _read_gcs_csv_text(bucket, blob.name)
            series = _build_series_from_text(Path(blob.name).name, text)
        except Exception:
            continue
        dates = pd.to_datetime(series.get("dates") or [], utc=False, errors="coerce")
        values = pd.to_numeric(series.get("values") or [], errors="coerce")
        frame = pd.DataFrame({"date": dates, "value": values}).dropna(subset=["date"])
        if frame.empty:
            continue
        frame = frame[frame["date"] <= latest_date].sort_values("date")
        if frame.empty:
            continue
        indicator = _infer_indicator_from_filename(stem)
        feature_name = f"gcs_{ds_type}_{indicator}"
        row[feature_name] = float(frame["value"].iloc[-1]) if pd.notna(frame["value"].iloc[-1]) else 0.0
        meta["gcs_series_features"] += 1

    try:
        raw_rows = _read_alerts_from_gcs_raw_csvs(bucket, latest_prefix.strip("/"))
    except Exception:
        raw_rows = []
    meta["gcs_raw_alert_rows"] = len(raw_rows)
    if raw_rows:
        scores = pd.to_numeric(pd.Series([r.get("score") for r in raw_rows]), errors="coerce").fillna(0.0)
        row["gcs_raw_alert_count"] = float(len(raw_rows))
        row["gcs_raw_alert_score_mean"] = float(scores.mean())
        row["gcs_raw_alert_score_max"] = float(scores.max())
        row["gcs_raw_triggered_count"] = float((scores >= 40).sum())
        row["gcs_raw_source_count"] = float(pd.Series([r.get("source", "") for r in raw_rows]).astype(str).nunique())
        labels = [str(r.get("category_label") or "").strip() for r in raw_rows if str(r.get("category_label") or "").strip()]
        row["gcs_raw_top_category_count"] = float(Counter(labels).most_common(1)[0][1]) if labels else 0.0
        row["gcs_raw_has_top_category"] = float(1 if labels else 0)

    return pd.DataFrame([row]), meta


def _load_or_build_gcs_feature_frame(cache_path: Path, force_refresh: bool = False) -> tuple[pd.DataFrame, dict[str, Any]]:
    if cache_path.exists() and not force_refresh:
        cached = pd.read_csv(cache_path)
        if "date" in cached.columns:
            cached["date"] = pd.to_datetime(cached["date"], utc=False, errors="coerce")
        return cached, {
            "gcs_enabled": True,
            "gcs_cache_path": str(cache_path),
            "gcs_cache_used": True,
            "gcs_feature_count": max(0, len(cached.columns) - 1),
        }

    features, meta = _load_gcs_feature_frame()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    features.to_csv(cache_path, index=False)
    meta["gcs_cache_path"] = str(cache_path)
    meta["gcs_cache_used"] = False
    return features, meta


def check_gcs_access() -> dict[str, Any]:
    key_path = _resolve_gcs_key_file()
    if key_path and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = key_path

    result: dict[str, Any] = {
        "gcs_key_path": key_path,
        "gcs_access_ok": False,
    }
    try:
        from data_dashboard import _get_gcs_bucket
        from core.guardian_loader import _discover_raw_dt_segments
    except Exception as exc:
        result["error"] = f"import failed: {exc}"
        return result

    try:
        bucket = _get_gcs_bucket()
        raw_base_prefix = os.getenv("GUARDIAN_GCS_RAW_PREFIX", "raw").strip().strip("/") or "raw"
        root_prefix = os.getenv("ARGUS_GCS_ROOT_PREFIX", "").strip().strip("/")
        history_root = f"{root_prefix}/{raw_base_prefix}" if root_prefix else raw_base_prefix
        dt_segments = _discover_raw_dt_segments(bucket, history_root)
        latest_prefix = f"{history_root}/{dt_segments[-1]}/" if dt_segments else f"{history_root}/"
        blobs = list(bucket.list_blobs(prefix=latest_prefix))
        csv_blobs = [b.name for b in blobs if str(b.name).lower().endswith(".csv")]
        result.update(
            {
                "gcs_access_ok": True,
                "bucket_name": getattr(bucket, "name", ""),
                "gcs_prefix": history_root,
                "gcs_dt_count": len(dt_segments),
                "gcs_latest_dt": dt_segments[-1] if dt_segments else None,
                "blob_count": len(blobs),
                "csv_blob_count": len(csv_blobs),
                "sample_csv_blobs": csv_blobs[:10],
            }
        )
        return result
    except Exception as exc:
        result["error"] = str(exc)
        return result


def _augment_training_frame_with_gcs(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    try:
        gcs_features, gcs_meta = _load_or_build_gcs_feature_frame(
            DEMO_GCS_FEATURES_PATH,
            force_refresh=os.getenv("MODELTRAININGDEMO_REFRESH_GCS", "").strip().lower() in {"1", "true", "yes"},
        )
    except Exception as exc:
        return df, {"gcs_enabled": False, "gcs_error": str(exc)}

    if gcs_features.empty:
        gcs_meta["gcs_enabled"] = False
        return df, gcs_meta

    merged = pd.merge_asof(
        df.sort_values("date"),
        gcs_features.sort_values("date"),
        on="date",
        direction="backward",
    )
    gcs_cols = [c for c in merged.columns if c.startswith("gcs_")]
    if gcs_cols:
        merged[gcs_cols] = merged[gcs_cols].fillna(0)
    gcs_meta["gcs_enabled"] = True
    gcs_meta["gcs_feature_count"] = len(gcs_cols)
    return merged, gcs_meta


def run_demo_training(args: argparse.Namespace) -> dict[str, Any]:
    df, gcs_meta = _build_demo_frame(args)

    artifact, metrics = _train_demo_model(
        df=df,
        horizon_days=args.horizon_days,
        include_gcs=not args.no_gcs,
        recent_years=args.recent_years,
        select_k=args.select_k,
        logistic_c=args.logistic_c,
    )
    demo_model_path = Path(args.output_model)
    demo_metrics_path = Path(args.output_metrics)
    demo_model_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(artifact, demo_model_path)
    with open(demo_metrics_path, "w", encoding="utf-8") as fh:
        json.dump(metrics, fh, ensure_ascii=False, indent=2)
    return {
        "output_model": str(demo_model_path),
        "output_metrics": str(demo_metrics_path),
        "selected_model": artifact.get("model_name"),
        "test_auc": metrics.get("test_auc"),
        "cv_auc": metrics.get("selected_model_cv_auc"),
        "rows_total": metrics.get("rows_total"),
        "target_horizon_days": metrics.get("target_horizon_days"),
        "demo_config": {
            "recent_years": args.recent_years,
            "select_k": args.select_k,
            "logistic_c": args.logistic_c,
            "include_gcs": not args.no_gcs,
        },
        "gcs_meta": gcs_meta,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect the current Downside Risk Index model and train a separate demo model without overwriting production artifacts."
    )
    parser.add_argument("--prices", default="Dataset/googl_daily_prices.csv")
    parser.add_argument("--news", default="Dataset/Google_Daily_News.csv")
    parser.add_argument("--balance", default="Dataset/googl_balance_sheet.csv")
    parser.add_argument("--cashflow", default="Dataset/googl_cash_flow_statement.csv")
    parser.add_argument("--income", default="Dataset/googl_income_statement.csv")
    parser.add_argument("--keywords-config", default="guardian_poc/config/risk_keywords.yaml")
    parser.add_argument("--horizon-days", type=int, default=3)
    parser.add_argument("--target-quantile", type=float, default=0.15)
    parser.add_argument("--cv-splits", type=int, default=5)
    parser.add_argument("--model-path", default=str(DEFAULT_MODEL_PATH))
    parser.add_argument("--metrics-path", default=str(DEFAULT_METRICS_PATH))
    parser.add_argument("--output-model", default=str(DEMO_MODEL_PATH))
    parser.add_argument("--output-metrics", default=str(DEMO_METRICS_PATH))
    parser.add_argument("--no-gcs", action="store_true", help="Skip loading Google Cloud data features for the demo run.")
    parser.add_argument("--check-gcs", action="store_true", help="Only verify GCS key discovery and blob access; do not train.")
    parser.add_argument("--refresh-gcs-cache", action="store_true", help="Rebuild the historical GCS feature cache before training.")
    parser.add_argument("--recent-years", type=int, default=2, help="Train the demo model on only the most recent N years of history.")
    parser.add_argument("--select-k", type=int, default=40, help="Keep only the top-K univariate features before fitting the demo model.")
    parser.add_argument("--logistic-c", type=float, default=0.1, help="Inverse regularization strength for the demo logistic regression.")
    parser.add_argument("--score-latest", action="store_true", help="Load the demo model and score the latest daily row using current GCS data.")
    parser.add_argument("--score-gcs", action="store_true", help="Score the latest GCS partition plus daily risk-score outputs without local Dataset files.")
    parser.add_argument("--score-live", action="store_true", help="Fetch live price/news/financial API data and score a synthetic latest row.")
    parser.add_argument("--symbol", default="GOOGL", help="Ticker symbol for live API fetches.")
    parser.add_argument("--sec-cik", default="0001652044", help="SEC CIK for the issuer used in live financial fetches.")
    parser.add_argument("--alpha-vantage-key", default=None, help="Alpha Vantage API key. Defaults to ALPHA_VANTAGE_API_KEY.")
    parser.add_argument("--sec-user-agent", default=None, help="SEC-required User-Agent header. Defaults to SEC_USER_AGENT env var.")
    parser.add_argument("--skip-train", action="store_true", help="Only inspect the current model; do not train a demo model.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.refresh_gcs_cache:
        os.environ["MODELTRAININGDEMO_REFRESH_GCS"] = "true"
    existing_summary = summarize_existing_model(Path(args.model_path), Path(args.metrics_path))
    print("Current model summary:")
    print(json.dumps(existing_summary, ensure_ascii=False, indent=2))

    if args.check_gcs:
        print("\nGCS access check:")
        print(json.dumps(check_gcs_access(), ensure_ascii=False, indent=2))
        return

    if args.score_latest:
        print("\nLatest daily score:")
        print(json.dumps(score_latest_demo_row(args), ensure_ascii=False, indent=2))
        return

    if args.score_gcs:
        print("\nLatest GCS daily score:")
        print(json.dumps(score_latest_gcs_demo_row(args), ensure_ascii=False, indent=2))
        return

    if args.score_live:
        print("\nLive API daily score:")
        print(json.dumps(score_live_demo_row(args), ensure_ascii=False, indent=2))
        return

    if args.skip_train:
        return

    print("\nTraining demo model...")
    demo_summary = run_demo_training(args)
    print(json.dumps(demo_summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
