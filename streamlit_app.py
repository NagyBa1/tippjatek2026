import os
import math
from datetime import datetime, timezone

import streamlit as st
from supabase import create_client

# ----------------------------
# PÁRTOK + SZÍNEK
# ----------------------------
PARTY_DEFS = [
    {"name": "Tisza Párt", "color": "#7EC8FF"},   # világos kék
    {"name": "Fidesz", "color": "#FF8A00"},       # narancssárga
    {"name": "Mi Hazánk", "color": "#2ECC71"},    # zöld
    {"name": "DK", "color": "#0B2D6B"}, # sötétkék
    {"name": "Egyéb", "color": "#9AA0A6"}
]
PARTIES = [p["name"] for p in PARTY_DEFS]
PARTY_COLOR = {p["name"]: p["color"] for p in PARTY_DEFS}

# pontozás
BASE_SCORE = 1000
PENALTY_PER_POINT = 10   # 1% összeltérés = -10 pont (összeltérés Σ |tipp - tény|)
WINNER_BONUS = 100

STEP = 0.01
FMT = "%.2f"


def sb():
    url = st.secrets.get("SUPABASE_URL", os.environ.get("SUPABASE_URL"))
    key = st.secrets.get("SUPABASE_SERVICE_ROLE_KEY", os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))
    if not url or not key:
        st.error("Hiányzik a SUPABASE_URL vagy SUPABASE_SERVICE_ROLE_KEY (Secrets-ben add meg).")
        st.stop()
    return create_client(url, key)


def is_close_100(x: float) -> bool:
    # 2 tizedes mellett is legyen stabil: 99.99/100.01 se menjen át
    return abs(x - 100.0) < 0.005


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

    max_res = max(float(results.get(p, 0.0)) for p in PARTIES)
    winners = {p for p in PARTIES if float(results.get(p, 0.0)) == max_res}

    for r in tips_rows:
        name = r["full_name"]
        tip = r["tip"] or {}

        total_diff = 0.0
        for p in PARTIES:
            tv = float(tip.get(p, 0.0))
            rv = float(results.get(p, 0.0))
            total_diff += abs(tv - rv)

        # 2 tizedesre kerekítve számoljuk a diffet is
        total_diff = round(total_diff, 2)

        score = BASE_SCORE - PENALTY_PER_POINT * total_diff
        score = max(0, int(round(score)))

        max_tip = max(float(tip.get(p, 0.0)) for p in PARTIES) if tip else 0.0
        tipped_winners = {p for p in PARTIES if float(tip.get(p, 0.0)) == max_tip}
        if tipped_winners & winners:
            score += WINNER_BONUS

        scores.append({
            "Név": name,
            "Összeltérés": f"{total_diff:.2f}",
            "Pont": score,
        })

    scores.sort(key=lambda x: (-x["Pont"], float(x["Összeltérés"]), x["Név"]))
    return scores


# ----------------------------
# STYLE
# ----------------------------
st.set_page_config(page_title="Tippjáték", page_icon="🗳️", layout="centered")

st.markdown(
    """
<style>
/* kicsit “apposabb” spacing */
.block-container {padding-top: 1.5rem; padding-bottom: 2rem; max-width: 900px;}
/* party card */
.party-card{
  border-radius: 14px;
  padding: 14px 16px;
  border: 1px solid rgba(255,255,255,0.08);
  background: rgba(255,255,255,0.03);
  margin-bottom: 10px;
}
.party-row{
  display:flex; align-items:center; justify-content:space-between; gap: 12px;
}
.party-left{
  display:flex; align-items:center; gap: 10px;
}
.party-pill{
  width: 12px; height: 12px; border-radius: 999px;
  box-shadow: 0 0 0 3px rgba(255,255,255,0.06) inset;
}
.party-name{
  font-weight: 650;
  letter-spacing: 0.2px;
}
.subtle{opacity: 0.8;}
.hr{
  height: 1px; background: rgba(255,255,255,0.08);
  margin: 12px 0;
}
.small{
  font-size: 0.92rem;
}
</style>
""",
    unsafe_allow_html=True,
)

client = sb()
locked = get_locked(client)
results_data, results_updated_at = get_results(client)

st.title("🗳️ Tippjáték")
page = st.sidebar.radio("Menü", ["Tipp leadása", "Ranglista", "Admin"], index=0)


if page == "Tipp leadása":
    st.subheader("Tipp leadása")

    if locked:
        st.warning("A tippelés le van zárva.")
        st.stop()

    full_name = st.text_input("Teljes név", placeholder="Pl. Kovács János")

    st.markdown('<div class="small subtle">Add meg pártonként a százalékokat (0–100). Az összegnek <b>pont 100%</b>-nak kell lennie.</div>', unsafe_allow_html=True)
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

    tip = {}
    total = 0.0

    for party in PARTIES:
        color = PARTY_COLOR[party]
        st.markdown(
            f"""
<div class="party-card">
  <div class="party-row">
    <div class="party-left">
      <div class="party-pill" style="background:{color};"></div>
      <div class="party-name">{party}</div>
    </div>
    <div class="subtle small">%</div>
  </div>
</div>
""",
            unsafe_allow_html=True,
        )

        v = st.number_input(
            label=f"{party} (%)",
            min_value=0.0,
            max_value=100.0,
            value=0.0,
            step=STEP,
            format=FMT,
            key=f"inp_{party}",
            label_visibility="collapsed",
        )
        tip[party] = float(v)
        total += float(v)

    total = round(total, 2)
    remaining = round(100.0 - total, 2)
    
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

    # progress: 0..100
    st.progress(min(max(total / 100.0, 0.0), 1.0))
    
    c1, c2, c3 = st.columns([1, 1, 1])
    c1.metric("Kiosztott összesen", f"{total:.2f}%")
    c2.metric("Maradt", f"{remaining:.2f}%")
    c3.metric("Állapot", "OK ✅" if is_close_100(total) else ("TÚL SOK ❌" if total > 100 else "HIÁNYZIK ⚠️"))

    can_submit = bool(full_name.strip()) and is_close_100(total)
   
    if not full_name.strip():
        st.info("Írd be a teljes neved.")
    elif not is_close_100(total):
        if total > 100:
            st.error("Több mint 100%-ot osztottál ki. Vegyél vissza valamelyikből.")
        else:
            st.warning("Még nem 100%. Ossz ki még a maradékból.")

    if st.button("Beküldés", type="primary", disabled=not can_submit, use_container_width=True):
        upsert_tip(client, full_name.strip(), tip)
        st.success("Mentve! ✅")
st.info(
    "📌 Megjegyzés az „Egyéb” kategóriához\n\n"
    "Az „Egyéb” mező kizárólag a nemzetiségi és egyéb nem pártlistás országos listák eredményét jelenti. "
    "Minden pártlista külön szerepel a tippelésben.\n\n"
    "A 2022-es választáson például a német nemzetiségi lista kb. 0,5%-ot ért el. "
    "Az ilyen szavazatok az összesített 100%-ba beleszámítanak, ezért szükséges az „Egyéb” mező."
)

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


else:  # Admin
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
    st.caption("Írd be a tény százalékokat. Itt is 100% legyen az összeg.")

    res = {}
    res_total = 0.0
    for party in PARTIES:
        default_val = float(results_data.get(party, 0.0)) if results_data else 0.0
        v = st.number_input(
            f"Tény: {party}",
            min_value=0.0,
            max_value=100.0,
            value=default_val,
            step=STEP,
            format=FMT,
            key=f"res_{party}",
        )
        res[party] = float(v)
        res_total += float(v)

    res_total = round(res_total, 2)
    st.write(f"Összeg: **{res_total:.2f}%**")

    if st.button("Eredmény mentése", type="primary", disabled=not is_close_100(res_total)):
        set_results(client, res)
        st.success("Éjféli eredmény mentve.")
