from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import yaml
from sklearn.ensemble import RandomForestClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.base import clone


@dataclass
class TrainArtifacts:
    model: Any
    model_name: str
    feature_columns: list[str]
    threshold: float
    metrics: dict[str, Any]
    feature_means: dict[str, float]
    feature_medians: dict[str, float]


def load_yaml(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_prices(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(
        columns={
            "1. open": "open",
            "2. high": "high",
            "3. low": "low",
            "4. close": "close",
            "5. volume": "volume",
        }
    )
    df["date"] = pd.to_datetime(df["date"], utc=False)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").dropna(subset=["close"])
    df["ret_1d"] = df["close"].pct_change()
    df["ret_5d"] = df["close"].pct_change(5)
    df["vol_5d"] = df["ret_1d"].rolling(5).std()
    df["vol_20d"] = df["ret_1d"].rolling(20).std()
    df["range_pct"] = (df["high"] - df["low"]) / df["close"]
    vol_mean_20 = df["volume"].rolling(20).mean()
    vol_std_20 = df["volume"].rolling(20).std().replace(0, np.nan)
    df["volume_z_20d"] = (df["volume"] - vol_mean_20) / vol_std_20
    df["target_ret_next_1d"] = df["ret_1d"].shift(-1)
    df["target_ret_next_5d"] = df["close"].pct_change(5).shift(-5)
    return df


def load_news(path: str, risk_keywords: dict[str, Any]) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["datetime"], unit="s", utc=True).dt.tz_convert(None).dt.floor("D")
    df["text"] = (df["headline"].fillna("") + " " + df["summary"].fillna("")).str.lower()

    per_day = pd.DataFrame(index=sorted(df["date"].dropna().unique()))
    per_day.index.name = "date"
    per_day["news_count"] = df.groupby("date").size()

    for key, cfg in risk_keywords.items():
        primaries = [str(x).lower() for x in cfg.get("primary", [])]
        high_alerts = [str(x).lower() for x in cfg.get("high_alert", [])]
        p_col = f"news_{key}_primary"
        h_col = f"news_{key}_high"
        df[p_col] = 0
        df[h_col] = 0
        for term in primaries:
            if not term:
                continue
            df[p_col] = df[p_col] + df["text"].str.contains(re.escape(term), regex=True, na=False).astype(int)
        for term in high_alerts:
            if not term:
                continue
            df[h_col] = df[h_col] + df["text"].str.contains(re.escape(term), regex=True, na=False).astype(int)
        per_day[p_col] = df.groupby("date")[p_col].sum()
        per_day[h_col] = df.groupby("date")[h_col].sum()

    per_day = per_day.fillna(0).reset_index()
    return per_day


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    if col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def load_financials(balance_path: str, cashflow_path: str, income_path: str) -> pd.DataFrame:
    b = pd.read_csv(balance_path)
    c = pd.read_csv(cashflow_path)
    i = pd.read_csv(income_path)
    for df in (b, c, i):
        df["fiscalDateEnding"] = pd.to_datetime(df["fiscalDateEnding"], utc=False)

    b = b.sort_values("fiscalDateEnding")
    c = c.sort_values("fiscalDateEnding")
    i = i.sort_values("fiscalDateEnding")

    f = pd.DataFrame({"date": sorted(set(b["fiscalDateEnding"]).intersection(c["fiscalDateEnding"]).intersection(i["fiscalDateEnding"]))})
    f = f.rename(columns={"date": "fiscalDateEnding"})
    f = f.merge(b, on="fiscalDateEnding", how="left").merge(c, on="fiscalDateEnding", how="left", suffixes=("", "_cf"))
    f = f.merge(i, on="fiscalDateEnding", how="left", suffixes=("", "_is"))

    cash = _num(f, "cashAndCashEquivalentsAtCarryingValue")
    cur_assets = _num(f, "totalCurrentAssets")
    cur_liab = _num(f, "totalCurrentLiabilities")
    total_liab = _num(f, "totalLiabilities")
    equity = _num(f, "totalShareholderEquity")
    revenue = _num(f, "totalRevenue")
    op_income = _num(f, "operatingIncome")
    op_cf = _num(f, "operatingCashflow")
    capex = _num(f, "capitalExpenditures")
    net_income = _num(f, "netIncome")

    out = pd.DataFrame({"date": f["fiscalDateEnding"]})
    out["fin_current_ratio"] = cur_assets / cur_liab.replace(0, np.nan)
    out["fin_cash_to_current_liab"] = cash / cur_liab.replace(0, np.nan)
    out["fin_leverage"] = total_liab / equity.replace(0, np.nan)
    out["fin_operating_margin"] = op_income / revenue.replace(0, np.nan)
    out["fin_ocf_margin"] = op_cf / revenue.replace(0, np.nan)
    out["fin_fcf_proxy"] = (op_cf - capex) / revenue.replace(0, np.nan)
    out["fin_net_margin"] = net_income / revenue.replace(0, np.nan)
    return out.sort_values("date")


def build_training_frame(
    prices_path: str,
    news_path: str,
    balance_path: str,
    cashflow_path: str,
    income_path: str,
    keywords_cfg_path: str,
    horizon_days: int = 5,
    target_quantile: float = 0.20,
) -> pd.DataFrame:
    prices = load_prices(prices_path)
    keywords = load_yaml(keywords_cfg_path)["risk_taxonomy"]
    news = load_news(news_path, keywords)
    fin = load_financials(balance_path, cashflow_path, income_path)

    df = prices.merge(news, on="date", how="left")
    df = df.sort_values("date")
    df = pd.merge_asof(df, fin.sort_values("date"), on="date", direction="backward")
    df = df.fillna(0)

    horizon_col = f"target_ret_next_{horizon_days}d"
    if horizon_col not in df.columns:
        df[horizon_col] = df["close"].pct_change(horizon_days).shift(-horizon_days)
    q = df[horizon_col].quantile(target_quantile)
    df["target_downside_risk"] = (df[horizon_col] <= q).astype(int)
    df["target_horizon_days"] = horizon_days
    return df


def _ts_cv_auc(model: Any, X: pd.DataFrame, y: pd.Series, n_splits: int = 5) -> float:
    if len(X) < max(50, n_splits * 20):
        return float("nan")
    splitter = TimeSeriesSplit(n_splits=n_splits)
    scores: list[float] = []
    for tr_idx, va_idx in splitter.split(X):
        X_tr, X_va = X.iloc[tr_idx], X.iloc[va_idx]
        y_tr, y_va = y.iloc[tr_idx], y.iloc[va_idx]
        if y_va.nunique() < 2:
            continue
        m = clone(model)
        m.fit(X_tr, y_tr)
        if hasattr(m, "predict_proba"):
            prob = m.predict_proba(X_va)[:, 1]
        else:
            raw = m.predict(X_va)
            prob = np.asarray(raw, dtype=float)
        try:
            auc = float(roc_auc_score(y_va, prob))
        except Exception:
            continue
        if not np.isnan(auc):
            scores.append(auc)
    return float(np.mean(scores)) if scores else float("nan")


def train_model(df: pd.DataFrame, horizon_days: int = 5, cv_splits: int = 5) -> TrainArtifacts:
    drop_cols = {"date", "target_ret_next_1d", "target_ret_next_5d", f"target_ret_next_{horizon_days}d", "target_downside_risk", "target_horizon_days"}
    feature_columns = [c for c in df.columns if c not in drop_cols]
    X = df[feature_columns]
    y = df["target_downside_risk"]

    n = len(df)
    split_idx = int(n * 0.8)
    X_train, X_test = X.iloc[:split_idx], X.iloc[split_idx:]
    y_train, y_test = y.iloc[:split_idx], y.iloc[split_idx:]

    logistic = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("clf", LogisticRegression(max_iter=2000, class_weight="balanced", solver="lbfgs")),
        ]
    )
    random_forest = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                RandomForestClassifier(
                    n_estimators=400,
                    max_depth=10,
                    min_samples_leaf=8,
                    class_weight="balanced_subsample",
                    random_state=42,
                    n_jobs=-1,
                ),
            ),
        ]
    )
    hist_gbm = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="median")),
            (
                "clf",
                HistGradientBoostingClassifier(
                    learning_rate=0.05,
                    max_depth=6,
                    max_iter=300,
                    min_samples_leaf=30,
                    random_state=42,
                ),
            ),
        ]
    )
    candidates: list[tuple[str, Any]] = [
        ("logistic_regression", logistic),
        ("random_forest", random_forest),
        ("hist_gradient_boosting", hist_gbm),
    ]
    best_name = ""
    best_model: Any | None = None
    best_auc = -1.0
    best_cv_auc = -1.0
    best_prob: np.ndarray | None = None
    model_scores: list[dict[str, Any]] = []

    for name, model in candidates:
        cv_auc = _ts_cv_auc(model, X_train, y_train, n_splits=cv_splits)
        model.fit(X_train, y_train)
        prob = model.predict_proba(X_test)[:, 1]
        auc = float(roc_auc_score(y_test, prob)) if y_test.nunique() > 1 else float("nan")
        model_scores.append({"model": name, "cv_auc": cv_auc, "test_auc": auc})
        if np.isnan(auc):
            continue
        score_cv = -1 if np.isnan(cv_auc) else cv_auc
        if (score_cv > best_cv_auc) or (score_cv == best_cv_auc and auc > best_auc):
            best_cv_auc = score_cv
            best_auc = auc
            best_name = name
            best_model = model
            best_prob = prob

    if best_model is None or best_prob is None:
        best_name = "logistic_regression"
        best_model = logistic.fit(X_train, y_train)
        best_prob = best_model.predict_proba(X_test)[:, 1]
        best_auc = float(roc_auc_score(y_test, best_prob)) if y_test.nunique() > 1 else float("nan")
        best_cv_auc = _ts_cv_auc(logistic, X_train, y_train, n_splits=cv_splits)

    pred = (best_prob >= 0.5).astype(int)
    rep = classification_report(y_test, pred, output_dict=True, zero_division=0)

    horizon_col = f"target_ret_next_{horizon_days}d"
    metrics = {
        "rows_total": int(n),
        "rows_train": int(len(X_train)),
        "rows_test": int(len(X_test)),
        "target_horizon_days": int(horizon_days),
        "target_column": horizon_col,
        "target_quantile_20_threshold": float(df[horizon_col].quantile(0.20)),
        "selected_model": best_name,
        "selected_model_cv_auc": best_cv_auc,
        "test_auc": best_auc,
        "model_comparison": model_scores,
        "cv_scheme": {"method": "TimeSeriesSplit", "n_splits": int(cv_splits)},
        "classification_report": rep,
        "test_period_start": str(df.iloc[split_idx]["date"].date()) if n > split_idx else None,
        "test_period_end": str(df.iloc[-1]["date"].date()) if n > 0 else None,
    }
    return TrainArtifacts(
        model=best_model,
        model_name=best_name,
        feature_columns=feature_columns,
        threshold=0.5,
        metrics=metrics,
        feature_means=X_train.apply(pd.to_numeric, errors="coerce").mean().fillna(0.0).to_dict(),
        feature_medians=X_train.apply(pd.to_numeric, errors="coerce").median().fillna(0.0).to_dict(),
    )


def save_artifacts(art: TrainArtifacts, model_path: str, metrics_path: str) -> None:
    payload = {
        "model": art.model,
        "model_name": art.model_name,
        "feature_columns": art.feature_columns,
        "threshold": art.threshold,
        "feature_means": art.feature_means,
        "feature_medians": art.feature_medians,
    }
    joblib.dump(payload, model_path)
    with open(metrics_path, "w", encoding="utf-8") as fh:
        json.dump(art.metrics, fh, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train Google historical downside-risk model for Alphabet Treasury PoC.")
    parser.add_argument("--prices", default="Dataset/googl_daily_prices.csv")
    parser.add_argument("--news", default="Dataset/Google_Daily_News.csv")
    parser.add_argument("--balance", default="Dataset/googl_balance_sheet.csv")
    parser.add_argument("--cashflow", default="Dataset/googl_cash_flow_statement.csv")
    parser.add_argument("--income", default="Dataset/googl_income_statement.csv")
    parser.add_argument("--keywords-config", default="guardian_poc/config/risk_keywords.yaml")
    parser.add_argument("--horizon-days", type=int, default=3, help="Prediction horizon for downside label, in trading days.")
    parser.add_argument("--target-quantile", type=float, default=0.15, help="Lower-tail quantile used to define downside label.")
    parser.add_argument("--cv-splits", type=int, default=5, help="TimeSeriesSplit folds on train period.")
    parser.add_argument("--output-model", default="guardian_poc/models/google_downside_risk_model.joblib")
    parser.add_argument("--output-metrics", default="guardian_poc/models/google_downside_risk_metrics.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    Path(args.output_model).parent.mkdir(parents=True, exist_ok=True)
    df = build_training_frame(
        prices_path=args.prices,
        news_path=args.news,
        balance_path=args.balance,
        cashflow_path=args.cashflow,
        income_path=args.income,
        keywords_cfg_path=args.keywords_config,
        horizon_days=args.horizon_days,
        target_quantile=args.target_quantile,
    )
    art = train_model(df, horizon_days=args.horizon_days, cv_splits=args.cv_splits)
    save_artifacts(art, args.output_model, args.output_metrics)
    print(f"Training rows: {art.metrics['rows_total']}")
    print(f"Selected model: {art.model_name}")
    print(f"Test AUC: {art.metrics['test_auc']:.4f}")
    print(f"Model saved to: {args.output_model}")
    print(f"Metrics saved to: {args.output_metrics}")


if __name__ == "__main__":
    main()
