import os
import requests
import pandas as pd
import streamlit as st
from datetime import datetime
from zoneinfo import ZoneInfo
from streamlit_autorefresh import st_autorefresh

# ---------------- CONFIG ----------------
TFNSW_ENDPOINT = "https://api.transport.nsw.gov.au/v1/tp/departure_mon"

STOP_ID = "2122145"
REFRESH_SECONDS = 20
SHOW_ROWS = 10

SYDNEY_TZ = ZoneInfo("Australia/Sydney")

# ---------------- API ----------------
def fetch_departures(stop_id: str) -> dict:
    api_key = st.secrets.get("TFNSW_API_KEY") or os.environ.get("TFNSW_API_KEY")
    if not api_key:
        raise RuntimeError("Missing TFNSW_API_KEY")

    headers = {"Authorization": f"apikey {api_key}"}

    params = {
        "outputFormat": "rapidJSON",
        "coordOutputFormat": "EPSG:4326",
        "mode": "direct",
        "type_dm": "stop",
        "name_dm": stop_id,
        "depArrMacro": "dep",
        "TfNSWDM": "true",
        # bus-only
        "exclMOT_1": "1",   # train
        "exclMOT_4": "1",   # tram
        "exclMOT_7": "1",   # coach
        "exclMOT_9": "1",   # ferry
        "exclMOT_11": "1",  # school bus
    }

    r = requests.get(TFNSW_ENDPOINT, params=params, headers=headers, timeout=20)
    r.raise_for_status()
    return r.json()


# ---------------- TIME HELPERS ----------------
def parse_iso_to_sydney(iso_str: str):
    """Convert TfNSW ISO Z-time to Australia/Sydney datetime."""
    if not iso_str:
        return None
    try:
        utc_dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return utc_dt.astimezone(SYDNEY_TZ)
    except Exception:
        return None


# ---------------- DATA ----------------
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

    # Best datetime (estimated > planned)
    df["dt"] = df.apply(
        lambda r: parse_iso_to_sydney(r["estimated"])
        or parse_iso_to_sydney(r["planned"]),
        axis=1
    )

    df = df[df["dt"].notna()].copy()

    # Derived display fields
    df["date_label"] = df["dt"].dt.strftime("%d/%m/%Y")
    df["time_label"] = df["dt"].dt.strftime("%H:%M")

    now = datetime.now(SYDNEY_TZ)
    df["mins"] = ((df["dt"] - now).dt.total_seconds() // 60).astype("Int64")

    df = df[df["line"].astype(str).str.len() > 0]
    df = df.sort_values("dt").reset_index(drop=True)
    return df


# ---------------- UI ----------------
def render_grouped_by_date(df: pd.DataFrame, limit_rows: int):
    if df.empty:
        st.info("No upcoming bus departures right now.")
        return

    df = df.head(limit_rows)

    st.markdown("""
    <style>
      .datehdr { font-size: 18px; font-weight: 800; margin-top: 14px; }
      .row {
        display: grid;
        grid-template-columns: 70px 1fr 60px 60px;
        gap: 10px;
        padding: 12px;
        border-radius: 14px;
        margin: 8px 0;
        background: rgba(233,246,255,.7);
        border: 1px solid rgba(0,0,0,.06);
      }
      .badge {
        background: #1a9bd7;
        color: white;
        font-weight: 800;
        border-radius: 999px;
        padding: 6px 12px;
        text-align: center;
      }
      .dest { font-weight: 800; font-size: 16px; }
      .meta { font-size: 13px; opacity: .75; }
      .eta { text-align: right; font-weight: 800; }
      .time { text-align: right; font-weight: 700; opacity: .85; }
    </style>
    """, unsafe_allow_html=True)

    for date_label, group in df.groupby("date_label", sort=False):
        st.markdown(f'<div class="datehdr">{date_label}</div>', unsafe_allow_html=True)
        st.divider()

        for _, r in group.iterrows():
            mins = r["mins"]
            when = "Now" if mins is not None and mins <= 0 else (f"{int(mins)}m" if mins is not None else "—")

            pln = parse_iso_to_sydney(r["planned"])
            est = parse_iso_to_sydney(r["estimated"])

            st.markdown(
                f"""
                <div class="row">
                  <div class="badge">{r["line"]}</div>
                  <div>
                    <div class="dest">{r["destination"]}</div>
                    <div class="meta">
                      Planned: {pln.strftime("%H:%M") if pln else "—"}
                      • Est: {est.strftime("%H:%M") if est else "—"}
                    </div>
                  </div>
                  <div class="eta">{when}</div>
                  <div class="time">{r["time_label"]}</div>
                </div>
                """,
                unsafe_allow_html=True
            )


# ---------------- APP ----------------
st.set_page_config(page_title="Bus Departures", page_icon="🚌", layout="wide")
st_autorefresh(interval=REFRESH_SECONDS * 1000, key="refresh")

st.title("🚌 Live bus departures")
st.caption(f"Stop ID: {STOP_ID} • Timezone: Australia/Sydney")

try:
    payload = fetch_departures(STOP_ID)
    df = parse_stop_events(payload)

    left, right = st.columns([2, 1], gap="large")

    with left:
        render_grouped_by_date(df, SHOW_ROWS)

    with right:
        st.subheader("Stop info")
        name = df["stop_name"].dropna().iloc[0] if not df.empty else ""
        st.write(name or "—")
        st.write(f"Showing next {SHOW_ROWS} services")

    st.divider()
    with st.expander("Debug table"):
        st.dataframe(df, use_container_width=True)

except Exception as e:
    st.error(str(e))
