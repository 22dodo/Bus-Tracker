import os
import requests
import pandas as pd
import streamlit as st
from datetime import datetime
from streamlit_autorefresh import st_autorefresh

TFNSW_ENDPOINT = "https://api.transport.nsw.gov.au/v1/tp/departure_mon"

# Fixed settings
STOP_ID = "2122145"
REFRESH_SECONDS = 20
SHOW_ROWS = 10  # total rows across all dates


def fetch_departures(stop_id: str) -> dict:
    api_key = os.environ.get("TFNSW_API_KEY")
    if not api_key:
        raise RuntimeError("Missing TFNSW_API_KEY environment variable")

    headers = {"Authorization": f"apikey {api_key}"}

    params = {
        "outputFormat": "rapidJSON",
        "coordOutputFormat": "EPSG:4326",
        "mode": "direct",
        "type_dm": "stop",
        "name_dm": stop_id,
        "depArrMacro": "dep",
        "TfNSWDM": "true",
        # bus-only by excluding other modes
        "exclMOT_1": "1",   # train
        "exclMOT_4": "1",   # tram/light rail
        "exclMOT_7": "1",   # coach
        "exclMOT_9": "1",   # ferry
        "exclMOT_11": "1",  # school bus (optional)
    }

    r = requests.get(TFNSW_ENDPOINT, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


def _parse_iso_to_local_dt(iso_str: str):
    """Parse ISO string (often ends with Z) into a local timezone-aware datetime, or None."""
    if not iso_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone()
    except Exception:
        return None


def _pick_best_time(row) -> datetime | None:
    """Prefer estimated; fallback to planned."""
    return _parse_iso_to_local_dt(row["estimated"]) or _parse_iso_to_local_dt(row["planned"])


def parse_stop_events(payload: dict) -> pd.DataFrame:
    events = payload.get("stopEvents", []) or []
    rows = []

    for ev in events:
        transport = ev.get("transportation") or {}
        location = ev.get("location") or {}

        planned = ev.get("departureTimePlanned") or ev.get("departureTime") or ""
        estimated = ev.get("departureTimeEstimated") or ""

        line = (
            transport.get("disassembledName")
            or transport.get("number")
            or transport.get("name")
            or ""
        )

        destination = (
            (ev.get("destination") or {}).get("name")
            or (transport.get("destination") or {}).get("name")
            or ""
        )

        rows.append({
            "line": str(line).strip(),
            "destination": destination,
            "planned": planned,
            "estimated": estimated,
            "stop_name": location.get("name", ""),
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    # Best departure datetime (local), then derived columns
    df["dt"] = df.apply(_pick_best_time, axis=1)
    df = df[df["dt"].notna()].copy()

    df["date_label"] = df["dt"].dt.strftime("%d/%m/%Y")
    df["time_label"] = df["dt"].dt.strftime("%H:%M")

    # Minutes until departure (for "Now / X min")
    now = datetime.now().astimezone()
    df["mins"] = ((df["dt"] - now).dt.total_seconds() // 60).astype("Int64")

    # Clean: drop blank routes
    df = df[df["line"].astype(str).str.len() > 0].copy()

    # Sort by datetime
    df = df.sort_values(by=["dt"]).reset_index(drop=True)
    return df


def render_grouped_by_date(df: pd.DataFrame, limit_rows: int = 30):
    """Render departures grouped under date headings."""
    if df.empty:
        st.info("No upcoming bus departures right now.")
        return

    # limit total rows across all dates
    df = df.head(limit_rows).copy()

    # Small CSS for date headers + rows
    st.markdown("""
    <style>
      .datehdr {
        font-size: 18px;
        font-weight: 800;
        margin-top: 14px;
        margin-bottom: 6px;
      }
      .row {
        display: grid;
        grid-template-columns: 74px 1fr 72px 64px;
        gap: 10px;
        align-items: center;
        padding: 10px 12px;
        border-radius: 14px;
        margin: 8px 0;
        background: rgba(233, 246, 255, 0.65);
        border: 1px solid rgba(0,0,0,0.06);
        font-family: system-ui, -apple-system, BlinkMacSystemFont;
      }
      .badge {
        background: #1a9bd7;
        color: #fff;
        font-weight: 800;
        border-radius: 999px;
        padding: 6px 12px;
        text-align: center;
        font-size: 15px;
      }
      .dest {
        font-weight: 800;
        font-size: 16px;
        line-height: 1.1;
      }
      .meta {
        font-size: 13px;
        opacity: 0.75;
        margin-top: 3px;
      }
      .eta {
        text-align: right;
        font-weight: 800;
      }
      .time {
        text-align: right;
        font-weight: 700;
        opacity: 0.85;
      }
    </style>
    """, unsafe_allow_html=True)

    for date_label, group in df.groupby("date_label", sort=False):
        st.markdown(f'<div class="datehdr">{date_label}</div>', unsafe_allow_html=True)
        st.divider()

        for _, row in group.iterrows():
            mins = row["mins"]
            if pd.isna(mins):
                when_txt = "—"
            elif mins <= 0:
                when_txt = "Now"
            else:
                when_txt = f"{int(mins)}m"

            st.markdown(
                f"""
                <div class="row">
                  <div class="badge">{row["line"]}</div>
                  <div>
                    <div class="dest">{row["destination"]}</div>
                    <div class="meta">Planned: {_parse_iso_to_local_dt(row["planned"]).strftime("%H:%M") if row["planned"] else "—"}
                     • Est: {_parse_iso_to_local_dt(row["estimated"]).strftime("%H:%M") if row["estimated"] else "—"}</div>
                  </div>
                  <div class="eta">{when_txt}</div>
                  <div class="time">{row["time_label"]}</div>
                </div>
                """,
                unsafe_allow_html=True
            )


# ---------------- UI ----------------
st.set_page_config(page_title="Bus Departures", page_icon="🚌", layout="wide")
st_autorefresh(interval=REFRESH_SECONDS * 1000, key="refresh")

st.title("🚌 Live bus departures")
st.caption(f"Stop ID: {STOP_ID} • Auto-refresh: every {REFRESH_SECONDS}s")

try:
    payload = fetch_departures(STOP_ID)
    df = parse_stop_events(payload)

    left, right = st.columns([2, 1], gap="large")

    with left:
        #st.subheader("Grouped by date")
        render_grouped_by_date(df, limit_rows=SHOW_ROWS)

    with right:
        st.subheader("Stop info")
        stop_name = df["stop_name"].dropna().iloc[0] if ("stop_name" in df.columns and len(df)) else ""
        st.write(stop_name or "—")
        st.write(f"Showing up to {SHOW_ROWS} upcoming departures.")

    st.divider()
    with st.expander("Debug table"):
        st.dataframe(df, use_container_width=True)

except Exception as e:
    st.error(f"Error: {e}")
