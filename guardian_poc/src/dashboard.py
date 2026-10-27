from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st
import yaml

APP_TZ = ZoneInfo("America/Los_Angeles")

st.set_page_config(page_title="Alphabet Treasury Guardian", page_icon="⚠️", layout="wide")


def load_latest(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def load_history(dir_path: str) -> pd.DataFrame:
    rows = []
    p = Path(dir_path)
    if not p.exists():
        return pd.DataFrame()
    for fp in sorted(p.glob("alerts_*.json"))[-180:]:
        try:
            obj = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows.append(
            {
                "generated_at_local": obj.get("generated_at_local"),
                "generated_at_utc": obj.get("generated_at_utc"),
                "event_count": obj.get("event_count", 0),
                "alert_count": obj.get("alert_count", 0),
                "triggered_count": obj.get("triggered_count", 0),
                "persona": obj.get("persona", ""),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "generated_at_local" in df.columns:
        df["generated_at"] = pd.to_datetime(df["generated_at_local"], errors="coerce")
    else:
        df["generated_at"] = pd.to_datetime(df["generated_at_utc"], errors="coerce", utc=True).dt.tz_convert(APP_TZ)
    return df.sort_values("generated_at")


def to_alert_df(snapshot: dict) -> pd.DataFrame:
    alerts = snapshot.get("alerts", [])
    if not alerts:
        return pd.DataFrame()
    df = pd.DataFrame(alerts)
    df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True).dt.tz_convert(APP_TZ)
    return df.sort_values("score", ascending=False)


def to_event_df(snapshot: dict) -> pd.DataFrame:
    events = snapshot.get("events", [])
    if not events:
        return pd.DataFrame()
    df = pd.DataFrame(events)
    df["published_at"] = pd.to_datetime(df["published_at"], errors="coerce", utc=True).dt.tz_convert(APP_TZ)
    if "countries" in df.columns:
        df["countries_text"] = df["countries"].apply(lambda x: ", ".join(x) if isinstance(x, list) else "")
    else:
        df["countries_text"] = ""
    if "categories" in df.columns:
        df["categories_text"] = df["categories"].apply(lambda x: ", ".join(x) if isinstance(x, list) else "")
    else:
        df["categories_text"] = ""
    return df.sort_values("published_at", ascending=False)


def load_countries(path: str) -> list[str]:
    p = Path(path)
    if not p.exists():
        return ["USA", "UK", "Germany", "Japan", "France", "Canada", "Brazil", "India", "Australia", "Mexico", "China"]
    try:
        payload = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        return ["USA", "UK", "Germany", "Japan", "France", "Canada", "Brazil", "India", "Australia", "Mexico", "China"]
    countries = list((payload.get("countries") or {}).keys())
    return countries if countries else ["USA", "UK", "Germany", "Japan", "France", "Canada", "Brazil", "India", "Australia", "Mexico", "China"]


def build_country_board(alerts_df: pd.DataFrame, countries: list[str]) -> pd.DataFrame:
    base = pd.DataFrame({"country": countries})
    if alerts_df.empty:
        base["avg_score"] = 0.0
        base["max_score"] = 0.0
        base["alert_count"] = 0
        base["top_category"] = "-"
        return base

    grouped = alerts_df.groupby("country", as_index=False).agg(
        avg_score=("score", "mean"),
        max_score=("score", "max"),
        alert_count=("score", "count"),
    )
    top_cat = (
        alerts_df.sort_values("score", ascending=False)
        .groupby("country", as_index=False)
        .first()[["country", "category_label"]]
        .rename(columns={"category_label": "top_category"})
    )
    out = base.merge(grouped, on="country", how="left").merge(top_cat, on="country", how="left")
    out["avg_score"] = out["avg_score"].fillna(0.0)
    out["max_score"] = out["max_score"].fillna(0.0)
    out["alert_count"] = out["alert_count"].fillna(0).astype(int)
    out["top_category"] = out["top_category"].fillna("-")
    return out


def level(score: float) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    if score > 0:
        return "LOW"
    return "NONE"


def level_color(score: float) -> str:
    if score >= 70:
        return "#b42318"
    if score >= 40:
        return "#b54708"
    if score > 0:
        return "#b08900"
    return "#344054"


def render_logo(path: str, width: int, fallback_label: str) -> None:
    p = Path(path)
    if p.exists():
        st.image(str(p), width=width)
    else:
        st.markdown(
            f"""
            <div style="border:1px dashed #98a2b3;border-radius:10px;padding:10px;width:{width}px;text-align:center;color:#667085;">
              {fallback_label}
            </div>
            """,
            unsafe_allow_html=True,
        )


latest_path = st.sidebar.text_input("Latest JSON", "guardian_poc/data/alerts_latest.json")
history_dir = st.sidebar.text_input("History Dir", "guardian_poc/data/snapshots")
countries_cfg = st.sidebar.text_input("Countries Config", "guardian_poc/config/countries.yaml")
alphabet_logo_path = st.sidebar.text_input("Alphabet Logo", "guardian_poc/assets/alphabet_logo.png")
min_score = st.sidebar.slider("Min score", 0, 100, 40, 1)

logo_col1, logo_col2, logo_col3 = st.columns([1, 2, 1])
with logo_col1:
    render_logo(alphabet_logo_path, width=180, fallback_label="Alphabet Logo")
with logo_col2:
    st.markdown(
        "<div style='text-align:center;padding-top:10px;'><h1 style='margin-bottom:0;'>Alphabet Treasury Guardian</h1></div>",
        unsafe_allow_html=True,
    )
st.markdown("---")

snapshot = load_latest(latest_path)
if not snapshot:
    st.warning("No snapshot found yet. Run daily job first.")
    st.stop()

alerts_all_df = to_alert_df(snapshot)
events_all_df = to_event_df(snapshot)
if alerts_all_df.empty:
    st.info("No alerts in latest snapshot.")
    alerts_df = alerts_all_df
else:
    alerts_df = alerts_all_df.copy()

countries = load_countries(countries_cfg)
board_df = build_country_board(alerts_all_df, countries)

col1, col2, col3, col4 = st.columns(4)
generated_local = snapshot.get("generated_at_local")
if not generated_local and snapshot.get("generated_at_utc"):
    try:
        generated_local = pd.to_datetime(snapshot.get("generated_at_utc"), utc=True).tz_convert(APP_TZ).isoformat()
    except Exception:
        generated_local = snapshot.get("generated_at_utc")
col1.metric("Generated (PT)", str(generated_local or "")[:19])
col2.metric("Events", int(snapshot.get("event_count", 0)))
col3.metric("Alerts", int(snapshot.get("alert_count", 0)))
col4.metric("Triggered", int(snapshot.get("triggered_count", 0)))
if snapshot.get("data_mode") == "demo_fallback":
    st.warning("Current snapshot is demo fallback because live APIs returned no events.")

st.subheader("Data Avaliability")
availability = snapshot.get("data_avaliability", [])
if not availability:
    st.caption("No source availability diagnostics found in snapshot yet.")
else:
    av_df = pd.DataFrame(availability)
    enabled_df = av_df[av_df["enabled"] == True].copy()  # noqa: E712
    total_enabled = int(len(enabled_df))
    healthy_enabled = int(enabled_df["healthy"].sum()) if not enabled_df.empty else 0
    health_ratio = (healthy_enabled / total_enabled) if total_enabled > 0 else 0.0
    h1, h2, h3 = st.columns([2, 1, 1])
    h1.progress(health_ratio, text=f"Enabled healthy sources: {healthy_enabled}/{total_enabled}")
    h2.metric("Enabled", total_enabled)
    h3.metric("Healthy", healthy_enabled)
    show_cols = ["source", "tier", "enabled", "implemented", "events", "healthy"]
    show_cols = [c for c in show_cols if c in av_df.columns]
    st.dataframe(av_df[show_cols].sort_values(["enabled", "events"], ascending=[False, False]), use_container_width=True, hide_index=True)

st.subheader("11-Country Board")
if "selected_country" not in st.session_state:
    st.session_state["selected_country"] = "All"
if "country_selector" not in st.session_state:
    st.session_state["country_selector"] = st.session_state["selected_country"]
if "pending_country" in st.session_state:
    st.session_state["selected_country"] = st.session_state["pending_country"]
    st.session_state["country_selector"] = st.session_state["pending_country"]
    del st.session_state["pending_country"]

selector_options = ["All"] + countries
if st.session_state["country_selector"] not in selector_options:
    st.session_state["country_selector"] = "All"
st.selectbox("Country filter", selector_options, key="country_selector")
st.session_state["selected_country"] = st.session_state["country_selector"]

card_cols = st.columns(4)
selected_country = st.session_state["selected_country"]
if selected_country == "All":
    for idx, row in board_df.iterrows():
        col = card_cols[idx % 4]
        score = float(row["max_score"])
        risk_level = level(score)
        color = level_color(score)
        border = f"2px solid {color}"
        col.markdown(
            f"""
            <div style="border:1px solid #E1E4E8;border-radius:4px;padding:12px;background:#FFFFFF;margin-bottom:8px;box-shadow: 0 1px 2px rgba(0,0,0,0.04);">
              <div style="font-size:11px;font-weight:700;color:#475569;text-transform:uppercase;border-bottom:1px solid #E1E4E8;padding-bottom:4px;margin-bottom:8px;">{row['country']}</div>
              <div style="font-size:20px;font-weight:700;color:{color};">{score:.1f} <span style="font-size:12px;font-weight:600;">({risk_level})</span></div>
              <div style="font-size:10px;color:#718096;margin-top:6px;font-family:monospace;">ALERTS: {int(row['alert_count'])} | TOP: {row['top_category']}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        if col.button(f"View {row['country']}", key=f"btn_country_{row['country']}"):
            st.session_state["pending_country"] = row["country"]
            st.rerun()
else:
    detail_row = board_df[board_df["country"] == selected_country]
    if detail_row.empty:
        st.info(f"{selected_country} not found in board.")
    else:
        row = detail_row.iloc[0]
        score = float(row["max_score"])
        risk_level = level(score)
        color = level_color(score)
        avg_score = float(row["avg_score"])
        alert_count = int(row["alert_count"])
        top_category = row["top_category"]
        st.markdown(
            f"""
            <div style="border:1px solid {color};border-left:4px solid {color};border-radius:4px;padding:20px;background:#FFFFFF;margin-bottom:16px;box-shadow: 0 1px 3px rgba(0,0,0,0.05);">
              <div style="font-size:10px;font-weight:700;color:#64748B;text-transform:uppercase;margin-bottom:4px;">Focused Country Profile</div>
              <div style="font-size:28px;font-weight:700;color:#1A202C;">{selected_country}</div>
              <div style="font-size:24px;font-weight:700;color:{color};margin-top:4px;">{score:.1f} <span style="font-size:14px;font-weight:600;">({risk_level})</span></div>
              <div style="font-size:12px;color:#475569;margin-top:12px;border-top:1px solid #E1E4E8;padding-top:8px;">Top Category: <strong>{top_category}</strong></div>
              <div style="font-size:12px;color:#475569;">Alerts: <strong>{alert_count}</strong> | Avg Score: <strong>{avg_score:.1f}</strong></div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Max Score", f"{score:.1f}")
        c2.metric("Avg Score", f"{avg_score:.1f}")
        c3.metric("Alert Count", alert_count)
        c4.metric("Top Category", str(top_category))

if st.button("Clear country filter"):
    st.session_state["pending_country"] = "All"
    st.rerun()
alerts_country_df = alerts_all_df.copy()
if selected_country != "All":
    alerts_country_df = alerts_country_df[alerts_country_df["country"] == selected_country]
events_country_df = events_all_df.copy()
if not events_country_df.empty and selected_country != "All":
    events_country_df = events_country_df[events_country_df["countries_text"].str.contains(selected_country, case=False, na=False)]

if not alerts_df.empty:
    alerts_df = alerts_df[alerts_df["score"] >= min_score]
    if selected_country != "All":
        alerts_df = alerts_df[alerts_df["country"] == selected_country]

st.subheader("Country Comparison (All 11)")
country_chart = board_df.copy()
country_chart = country_chart.set_index("country")[["max_score", "avg_score"]]
st.bar_chart(country_chart)

st.subheader("Top Alerts")
if alerts_df.empty:
    st.write("No alerts above current threshold.")
else:
    show_cols = [
        "country",
        "score",
        "category_label",
        "source",
        "published_at",
        "recommendation",
        "recommendation_source",
        "rationale",
        "url",
    ]
    existing = [c for c in show_cols if c in alerts_df.columns]
    st.dataframe(alerts_df[existing], use_container_width=True, hide_index=True)

    by_country = alerts_df.groupby("country", as_index=False)["score"].mean().sort_values("score", ascending=False)
    st.subheader("Country Risk Heat (Avg Score)")
    st.bar_chart(by_country.set_index("country"))

if selected_country != "All":
    st.subheader(f"{selected_country} Event & Warning Detail")
    if alerts_country_df.empty:
        st.info(f"No events/warnings found for {selected_country} in current snapshot.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Total Warnings", int(len(alerts_country_df)))
        c2.metric("High (>=70)", int((alerts_country_df["score"] >= 70).sum()))
        c3.metric("Medium (40-69.9)", int(((alerts_country_df["score"] >= 40) & (alerts_country_df["score"] < 70)).sum()))
        cat = (
            alerts_country_df.groupby("category_label", as_index=False)["score"]
            .count()
            .rename(columns={"score": "count"})
            .sort_values("count", ascending=False)
        )
        st.bar_chart(cat.set_index("category_label"))
        detail_cols = [
            "published_at",
            "source",
            "title",
            "category_label",
            "score",
            "recommendation",
            "rationale",
            "url",
        ]
        detail_existing = [c for c in detail_cols if c in alerts_country_df.columns]
        st.dataframe(
            alerts_country_df.sort_values("score", ascending=False)[detail_existing],
            use_container_width=True,
            hide_index=True,
        )

st.subheader("Event Explorer")
if events_all_df.empty:
    st.info("No raw events stored in current snapshot. Run daily job once to refresh.")
else:
    source_options = ["All"] + sorted(events_all_df["source"].dropna().unique().tolist())
    selected_source = st.selectbox("Event source", source_options, key="event_source_filter")
    event_view = events_country_df if selected_country != "All" else events_all_df
    if selected_source != "All":
        event_view = event_view[event_view["source"] == selected_source]
    search_q = st.text_input("Search events", value="", key="event_search")
    if search_q.strip():
        q = search_q.strip()
        event_view = event_view[
            event_view["title"].str.contains(q, case=False, na=False)
            | event_view["summary"].str.contains(q, case=False, na=False)
        ]
    if "selected_event_id" not in st.session_state:
        st.session_state["selected_event_id"] = ""
    event_cols = ["published_at", "source", "countries_text", "categories_text", "title", "summary", "url"]
    event_cols = [c for c in event_cols if c in event_view.columns]
    st.caption(f"Showing {len(event_view)} events")
    st.dataframe(event_view[event_cols], use_container_width=True, hide_index=True)

    st.markdown("**Click to View Event Detail**")
    preview = event_view.head(20).copy()
    for _, row in preview.iterrows():
        event_id = str(row.get("event_id", ""))
        title = str(row.get("title", ""))
        source = str(row.get("source", "-"))
        pub = row.get("published_at")
        pub_text = pd.to_datetime(pub, errors="coerce")
        pub_text = pub_text.strftime("%Y-%m-%d %H:%M") if not pd.isna(pub_text) else str(pub)
        c1, c2 = st.columns([1, 9])
        if c1.button("View", key=f"event_view_{event_id}"):
            st.session_state["selected_event_id"] = event_id
            st.rerun()
        c2.markdown(f"`{source}` {pub_text}  \n{title}")

    selected_id = st.session_state.get("selected_event_id", "")
    selected_df = event_view[event_view["event_id"] == selected_id] if selected_id else pd.DataFrame()
    if not selected_df.empty:
        row = selected_df.iloc[0]
        st.subheader("Event Detail")
        st.markdown(f"**Title:** {row.get('title', '-')}")
        st.markdown(f"**Source:** {row.get('source', '-')}")
        st.markdown(f"**Published (UTC):** {row.get('published_at', '-')}")
        st.markdown(f"**Countries:** {row.get('countries_text', '-')}")
        st.markdown(f"**Categories:** {row.get('categories_text', '-')}")
        st.markdown(f"**Summary:** {row.get('summary', '-')}")
        url = row.get("url", "")
        if isinstance(url, str) and url:
            st.markdown(f"**Link:** {url}")
            st.link_button("Open source event link", url)

        news_title = str(row.get("news_title", "") or "")
        news_summary = str(row.get("news_summary", "") or "")
        news_url = str(row.get("news_url", "") or "")
        if news_title or news_summary or news_url:
            st.markdown("**Generated News (Demo)**")
            if news_title:
                st.markdown(f"- {news_title}")
            if news_summary:
                st.markdown(f"- {news_summary}")
            if news_url:
                st.link_button("Open generated news link", news_url)

        suggestions = row.get("suggestion_options", [])
        if isinstance(suggestions, list) and suggestions:
            st.markdown("**Suggestion Options**")
            chosen = st.radio(
                "Choose an action",
                suggestions,
                key=f"suggestion_{selected_id}",
                horizontal=False,
            )
            st.info(f"Selected: {chosen}")

        related = alerts_all_df[alerts_all_df["title"] == row.get("title")] if not alerts_all_df.empty else pd.DataFrame()
        if not related.empty:
            st.markdown("**Related Warnings**")
            cols = ["country", "score", "category_label", "recommendation", "source"]
            cols = [c for c in cols if c in related.columns]
            st.dataframe(related[cols].sort_values("score", ascending=False), use_container_width=True, hide_index=True)

history_df = load_history(history_dir)
st.subheader("Daily Trend")
if history_df.empty:
    st.write("No history snapshots yet.")
else:
    if selected_country == "All":
        trend = history_df.set_index("generated_at")[["event_count", "triggered_count"]]
        st.line_chart(trend)
    else:
        st.caption(f"Trend currently shows global counts. Country selected: {selected_country}.")
        trend = history_df.set_index("generated_at")[["event_count", "triggered_count"]]
        st.line_chart(trend)
