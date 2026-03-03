import os
import math
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client

# ----------------------------
# 1) ÁLLÍTSD BE A PÁRTLISTÁT ITT
# ----------------------------
PARTIES = [
    "Párt A",
    "Párt B",
    "Párt C",
    "Párt D",
]

BASE_SCORE = 1000
PENALTY_PER_POINT = 10
WINNER_BONUS = 100


def sb():
    url = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL"))
    key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))
    if not url or not key:
        st.error("Hiányzik a SUPABASE_URL vagy SUPABASE_SERVICE_ROLE_KEY (Secrets-ben add meg).")
        st.stop()
    return create_client(url, key)


def is_close_100(x: float) -> bool:
    return math.isclose(x, 100.0, abs_tol=1e-9)


def now_utc():
    return datetime.now(timezone.utc)


def get_locked(client) -> bool:
    resp = client.table("settings").select("locked").eq("id", 1).execute()
    return bool(resp.data and resp.data[0]["locked"])


def set_locked(client, locked: bool):
    client.table("settings").upsert({"id": 1, "locked": locked}).execute()


def get_results(client):
    resp = client.table("results").select("data, updated_at").eq("id", 1).execute()
    if not resp.data:
        return None, None
    return resp.data[0]["data"], resp.data[0]["updated_at"]


def set_results(client, data: dict):
    client.table("results").upsert({"id": 1, "data": data, "updated_at": now_utc().isoformat()}).execute()


def upsert_tip(client, full_name: str, tip: dict):
    client.table("tips").upsert({"full_name": full_name, "tip": tip}).execute()


def load_all_tips(client):
    return client.table("tips").select("full_name, created_at, tip").execute().data or []


def compute_scores(tips_rows, results: dict):
    scores = []
    if not results:
        return scores

    max_res = max(results.values()) if results else None
    winners = {p for p, v in results.items() if v == max_res}

    for r in tips_rows:
        name = r["full_name"]
        tip = r["tip"] or {}

        total_diff = 0.0
        for p in PARTIES:
            tv = float(tip.get(p, 0.0))
            rv = float(results.get(p, 0.0))
            total_diff += abs(tv - rv)

        score = max(0, int(round(BASE_SCORE - PENALTY_PER_POINT * total_diff)))

        if tip:
            max_tip = max(float(tip.get(p, 0.0)) for p in PARTIES)
            tipped_winners = {p for p in PARTIES if float(tip.get(p, 0.0)) == max_tip}
            if tipped_winners & winners:
                score += WINNER_BONUS

        scores.append({"Név": name, "Összeltérés": round(total_diff, 2), "Pont": score})

    scores.sort(key=lambda x: (-x["Pont"], x["Összeltérés"], x["Név"]))
    return scores


st.set_page_config(page_title="Országgyűlési tippjáték", page_icon="🗳️", layout="centered")
client = sb()

st.title("🗳️ Tippjáték")

page = st.sidebar.radio("Menü", ["Tipp leadása", "Ranglista", "Admin"], index=0)

locked = get_locked(client)
results_data, results_updated_at = get_results(client)

if page == "Tipp leadása":
    st.subheader("Tipp leadása")

    if locked:
        st.warning("A tippelés le van zárva.")
        st.stop()

    full_name = st.text_input("Teljes név", placeholder="Pl. Kovács János")
    st.write("Add meg pártonként a százalékokat (0–100). Az összegnek **pont 100%**-nak kell lennie.")

    tip = {}
    total = 0.0

    for p in PARTIES:
        v = st.number_input(p, min_value=0.0, max_value=100.0, value=0.0, step=0.1, format="%.1f")
        tip[p] = float(v)
        total += float(v)

    remaining = 100.0 - total
    col1, col2 = st.columns(2)
    col1.metric("Kiosztott összesen", f"{total:.1f}%")
    col2.metric("Maradt", f"{remaining:.1f}%")

    can_submit = bool(full_name.strip()) and is_close_100(total)

    if not full_name.strip():
        st.info("Írd be a teljes neved.")
    elif not is_close_100(total):
        st.error("Az összeg nem 100%. Javítsd, és utána küldheted be.")

    if st.button("Beküldés", type="primary", disabled=not can_submit):
        upsert_tip(client, full_name.strip(), tip)
        st.success("Mentve! ✅")

elif page == "Ranglista":
    st.subheader("Ranglista")

    if not results_data:
        st.info("Még nincs rögzítve az éjféli eredmény.")
        st.stop()

    tips_rows = load_all_tips(client)
    scores = compute_scores(tips_rows, results_data)

    st.caption(f"Eredmény frissítve: {results_updated_at}")
    st.dataframe(scores, use_container_width=True)

    with st.expander("Éjféli tényadatok"):
        st.json(results_data)

else:
    st.subheader("Admin")

    admin_pass = st.secrets.get("ADMIN_PASSWORD", os.environ.get("ADMIN_PASSWORD"))
    if not admin_pass:
        st.error("Hiányzik az ADMIN_PASSWORD (Secrets-ben add meg).")
        st.stop()

    entered = st.text_input("Admin jelszó", type="password")
    if entered != admin_pass:
        st.info("Add meg az admin jelszót.")
        st.stop()

    st.success("Admin mód ✅")

    st.write("### Tippelés lezárása")
    new_locked = st.toggle("Tippelés zárva", value=locked)
    if new_locked != locked:
        set_locked(client, new_locked)
        st.toast("Beállítás mentve.")

    st.write("### Éjféli eredmény rögzítése")
    st.caption("Írd be ugyanazokra a pártokra a tény százalékokat. Itt is 100% legyen az összeg.")

    res = {}
    res_total = 0.0
    for p in PARTIES:
        default_val = float(results_data.get(p, 0.0)) if results_data else 0.0
        v = st.number_input(f"Tény: {p}", min_value=0.0, max_value=100.0, value=default_val, step=0.1, format="%.1f")
        res[p] = float(v)
        res_total += float(v)

    st.write(f"Összeg: **{res_total:.1f}%**")

    if st.button("Eredmény mentése", type="primary", disabled=not is_close_100(res_total)):
        set_results(client, res)
        st.success("Éjféli eredmény mentve.")
