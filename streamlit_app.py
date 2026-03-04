import os
from datetime import datetime, timezone
import base64
from pathlib import Path
import streamlit as st
from supabase import create_client


@st.cache_data
def img_to_data_uri(path: str) -> str:
    p = Path(path)
    data = p.read_bytes()
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:image/png;base64,{b64}"

# ----------------------------
# PÁRTOK + SZÍNEK
# ----------------------------
PARTY_DEFS = [
    {"name": "Tisza Párt", "color": "#7EC8FF"},   # világos kék
    {"name": "Fidesz", "color": "#FF8A00"},       # narancssárga
    {"name": "Mi Hazánk", "color": "#2ECC71"},    # zöld
    {"name": "DK", "color": "#0B2D6B"},    
    {"name": "MKKP", "color": "#ed283c"}# sötétkék
]
LOGO_PATHS = {
    "Tisza Párt": "tisza.png",
    "Fidesz": "fidesz.png",
    "Mi Hazánk": "mihazank.png",
    "DK": "dk.png",
    "MKKP": "mkkp.png"
}
PARTIES = [p["name"] for p in PARTY_DEFS]
PARTY_COLOR = {p["name"]: p["color"] for p in PARTY_DEFS}

# ----------------------------
# IDŐPONTOK / SZÖVEGEK
# (csak kiírás a szabályokhoz – a tényleges lezárást az Admin "locked" kapcsoló kezeli)
# ----------------------------
CLOSES_AT_TEXT = "2026. április 10. 20:00"   # tippelés zárása (szabály szerint)
SNAPSHOT_AT_TEXT = "00:00 (éjféli pillanatkép)"  # ezt később átírhatod, ha nem pont 00:00 legyen

# pontozás
BASE_SCORE = 1000
PENALTY_PER_POINT = 10   # 1% összeltérés = -10 pont (összeltérés Σ |tipp - tény|)
WINNER_BONUS = 100

STEP = 0.01
FMT = "%.2f"

RULES_PART1 = """
## 📜 Tippjáték – szabályok

### 1) Mire tippelünk?

Az **országos pártlisták szavazatainak százalékos arányára** tippelünk.

Nem mandátumokra, nem az egyéni választókerületekre, hanem **kizárólag a listás százalékokra**.

Az országos pártlisták eredményei 2022-ben például így néztek ki:
"""

RULES_PART2 = """

### 2) 100,00% kötelező
A beküldéshez az összes mező összege **pontosan 100,00%** kell legyen.


### 3) Meddig lehet tippelni?
A szabály szerinti zárás:

**2026. április 5. 20:00**

Ezután már nem lehet új tippet leadni vagy meglévőt módosítani.


### 4) Lehet módosítani a tippet?
Igen.

Amíg a tippelés nyitva van, a **saját neved egyedi azonosítóként szolgál**.  
Ha ugyanazzal a névvel új tippet küldesz be, akkor **a korábbi tipped felülíródik**, és **mindig az utolsó beküldött verzió számít**.


### 5) Milyen eredménnyel döntjük el a játékot?
A választás estéjén **éjfélkor az NVI oldalán szereplő listás eredmények** alapján döntjük el a tippjátékot.

👉 Ha valamilyen technikai okból szükséges, ez az időpont **előre egyeztetve módosítható**.


### 6) Pontozás
Minden játékos tippjét összehasonlítjuk a valódi eredményekkel.

Minden pártnál megnézzük, hogy **mennyire tér el a tipped a tényleges százaléktól**.  
Minél kisebb az eltérés, annál jobb.

Az összes eltérésből kiszámolunk egy pontszámot.  
**Minél közelebb van a tipped a valós eredményhez, annál több pontot kapsz.**

Extra bónusz jár annak, aki **eltalálja, melyik pártlista kapja a legtöbb szavazatot**.


### 7) Ki nyer?
Amint rögzítjük az éjféli eredményt, az oldal **automatikusan kiszámolja a pontokat** (remélem), és rangsort készít.

A legtöbb pontot szerző játékos nyeri a tippjátékot.

A győztes jutalma:  
**egy 3D nyomtatott Lázár János WC-kefe tartó**, amelyet az eredményvárón ünnepélyesen vehet át.
"""

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
    # full_name primary key -> ugyanazzal a névvel újraküldve felülírja (lezárásig szándékosan)
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
.block-container {padding-top: 1.5rem; padding-bottom: 2rem; max-width: 900px;}
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
.small{font-size: 0.92rem;}
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

    # --- Szabályok doboz: első megnyitáskor automatikusan nyitva ---
    if "show_rules" not in st.session_state:
        st.session_state.show_rules = True

    if st.sidebar.button("📜 Szabályok"):
        st.session_state.show_rules = True

    with st.expander("📜 Szabályok", expanded=st.session_state.show_rules):
        st.markdown(RULES_PART1)
        st.image("lista_pelda.png", caption="Országos pártlistás eredmények – 2022")
        st.markdown(RULES_PART2)

    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

    if locked:
        st.warning("A tippelés le van zárva.")
        st.stop()

    full_name = st.text_input("Teljes név", placeholder="Pl. Kovács János")
    st.markdown(
        '<div class="small subtle">Add meg pártonként a százalékokat (0–100). Az összegnek <b>pont 100%</b>-nak kell lennie.</div>',
        unsafe_allow_html=True
    )
    st.markdown('<div class="hr"></div>', unsafe_allow_html=True)

    tip = {}
    total = 0.0

    for party in PARTIES:
        logo_html = ""
        if party in LOGO_PATHS and Path(LOGO_PATHS[party]).exists():
            logo_uri = img_to_data_uri(LOGO_PATHS[party])
            logo_html = f'<img class="party-logo" src="{logo_uri}" style="width:24px;height:24px;margin-right:8px;">'
        color = PARTY_COLOR[party]
        st.markdown(
            f"""
<div class="party-card">
  <div class="party-row">
    <div class="party-left">
      <div class="party-pill" style="background:{color};"></div>
      {logo_html}
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
