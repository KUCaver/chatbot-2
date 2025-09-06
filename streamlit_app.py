# streamlit_app.py
# -------------------------------------------------------------
# 설치: pip install streamlit google-generativeai gTTS pillow pandas
# 실행: streamlit run streamlit_app.py
#  - 키 없거나 오류 시: 데모 규칙(요약/분류=규칙, 카드추천=로컬룰)으로 시연
#  - 키 정상일 때: Gemini로 요약/분류/자유대화/프롬프트 생성 활성화
#  - 주의: 공개 리포에 실제 키 하드코딩은 금물(여기선 PoC 편의상 기본값 제공)
# -------------------------------------------------------------

import os, io, json, time, base64, math, random
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from PIL import Image, ImageDraw
from gtts import gTTS

# 0) 페이지 설정 (항상 최상단)
st.set_page_config(page_title="아바타 금융 코치 PoC", page_icon="💬", layout="centered")

# === 안전 고지(간단) ===
st.caption("※ 데모 고지: 실제 결제/지오펜싱/CRM 연동은 PoC에서 모의로 시연합니다.")

# 1) API 키 (환경변수 → 기본값 → 사이드바 입력)
DEFAULT_API_KEY = "AIzaSyDvTKaKoZs9_UjG0aY8bd4pjmJaGKJKB6g"  # ⚠️ PoC 편의용. 공개 저장소엔 두지 마세요.
API_KEY = os.getenv("GOOGLE_API_KEY", "") or DEFAULT_API_KEY

# 2) 유틸 & 공통 데이터 ---------------------------------------------------------
def draw_avatar(size: int = 320):
    img = Image.new("RGBA", (size, size), (245, 248, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((size*0.18, size*0.05, size*0.82, size*0.65),
              fill=(220,230,255), outline=(100,110,180), width=4)
    d.rectangle((size*0.31, size*0.55, size*0.69, size*0.95),
                fill=(210,220,255), outline=(100,110,180), width=4)
    return img

def tts_to_mp3_bytes(text: str):
    try:
        buf = io.BytesIO()
        gTTS(text=text, lang="ko").write_to_fp(buf)
        return buf.getvalue()
    except Exception:
        return None

def safe_json_loads(s: str, default):
    try: return json.loads(s)
    except Exception: return default

def money(x): 
    try: return f"{int(x):,}원"
    except: return str(x)

# 결제 룰 샘플 (간단화)
SAMPLE_RULES = [
    {"name":"Alpha Card","mcc":["FNB","CAFE"],"rate":0.05,"cap":20000},
    {"name":"Beta Card","mcc":["ALL"],"rate":0.02,"cap":50000},
    {"name":"Cinema Max","mcc":["CINE"],"rate":0.10,"cap":15000},
]
DEPT_MAP = {"민원":"고객보호센터","카드":"카드상담센터","대출":"여신상담센터",
            "연금":"연금·세제상담","세제":"연금·세제상담","상담요청":"종합상담","기타":"종합상담"}

SAMPLE_TX = pd.DataFrame([
    {"date":"2025-08-28","merchant":"스타커피 본점","mcc":"CAFE","amount":4800},
    {"date":"2025-08-29","merchant":"김밥왕","mcc":"FNB","amount":8200},
    {"date":"2025-08-30","merchant":"메가시네마","mcc":"CINE","amount":12000},
])

# 3) 아바타(폰 프레임) 렌더 ------------------------------------------------------
def render_phone_avatar(overlay_text: str = "무엇을 도와드릴까요?",
                        media_bytes: bytes | None = None,
                        is_video: bool = False):
    css = """
    <style>
      .phone { width: 360px; height: 720px; margin: 6px auto 18px;
        border: 12px solid #111; border-radius: 36px; position: relative;
        box-shadow: 0 12px 30px rgba(0,0,0,.25); overflow: hidden; background:#000; }
      .overlay { position:absolute; left:12px; right:12px; bottom:88px; display:flex; }
      .bubble { background: rgba(255,255,255,.88); padding:10px 14px; border-radius:14px;
        max-width:82%; font-size:14px; line-height:1.35; box-shadow:0 2px 8px rgba(0,0,0,.15); }
      .controls { position:absolute; left:0; right:0; bottom:18px; display:flex; justify-content:center; }
      .btn { width:56px; height:56px; border:none; border-radius:50%; background:#2b6cff; color:#fff;
        font-size:22px; box-shadow:0 8px 18px rgba(43,108,255,.35); }
      video, img { width:100%; height:100%; object-fit: cover; }
    </style>"""
    html_media = ""
    if media_bytes:
        b64 = base64.b64encode(media_bytes).decode()
        html_media = (f'<video autoplay muted loop playsinline src="data:video/mp4;base64,{b64}"></video>'
                      if is_video else f'<img src="data:image/png;base64,{b64}" />')
    else:
        try:
            with open("assets/avatar.mp4","rb") as f: 
                b64 = base64.b64encode(f.read()).decode()
            html_media = f'<video autoplay muted loop playsinline src="data:video/mp4;base64,{b64}"></video>'
        except:
            try:
                with open("assets/avatar.png","rb") as f: 
                    b64 = base64.b64encode(f.read()).decode()
                html_media = f'<img src="data:image/png;base64,{b64}" />'
            except:
                html_media = '<div style="width:100%;height:100%;background:#222"></div>'
    html = f"""{css}
    <div class="phone">
      {html_media}
      <div class="overlay"><div class="bubble">{overlay_text}</div></div>
      <div class="controls"><button class="btn" title="음성 입력(데모)">🎤</button></div>
    </div>"""
    components.html(html, height=760)

# 4) LLM 초기화 -------------------------------------------------------------------
USE_LLM, MODEL = False, None
if API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=API_KEY)
        MODEL = genai.GenerativeModel("gemini-1.5-flash-latest")
        USE_LLM = True
    except Exception:
        USE_LLM = False

def llm_summary(text: str) -> str:
    if USE_LLM and MODEL:
        try:
            res = MODEL.generate_content(
                f"다음 고객 민원/문의 내용을 상담사가 이해하기 쉽게 3문장 이내 한국어 요약:\n\n{text}"
            )
            return getattr(res, "text", str(res)).strip()
        except Exception as e:
            return f"[LLM 오류: {e}]"
    return "요약(데모): 핵심 쟁점과 요청사항을 간단히 정리해 상담사에게 전달합니다."

def llm_classify(text: str) -> dict:
    if USE_LLM and MODEL:
        schema = ("JSON으로만 답해. keys=[intent, sub_intent, urgency]. "
                  "intent in [민원, 카드, 대출, 연금, 세제, 상담요청, 기타]; urgency in [낮음, 보통, 높음]")
        try:
            res = MODEL.generate_content(f"{schema}\n\n사용자 발화:\n{text}")
            return safe_json_loads(getattr(res, "text", "{}"),
                                   {"intent":"기타","sub_intent":"분류오류","urgency":"보통"})
        except Exception as e:
            return {"intent":"기타","sub_intent":f"LLM 오류: {e}","urgency":"보통"}
    q = text
    if any(k in q for k in ["금리","민원","불만"]): return {"intent":"민원","sub_intent":"금리/표기","urgency":"보통"}
    if any(k in q for k in ["카드","혜택"]):        return {"intent":"카드","sub_intent":"혜택문의","urgency":"보통"}
    if "대출" in q or "갈아타" in q:               return {"intent":"대출","sub_intent":"대환","urgency":"보통"}
    if any(k in q for k in ["연금","세액","소득공제","세제"]):
        return {"intent":"세제","sub_intent":"연금/세제","urgency":"보통"}
    if any(k in q for k in ["전화","상담","콜백"]): return {"intent":"상담요청","sub_intent":"콜백","urgency":"보통"}
    return {"intent":"기타","sub_intent":"일반 문의","urgency":"보통"}

def build_handoff(summary: str, cls: dict) -> dict:
    dept = DEPT_MAP.get(cls.get("intent","기타"), "종합상담")
    return {
        "target_department": dept,
        "callback_enabled": True,
        "priority": 2 if cls.get("urgency")=="높음" else 1,
        "context_summary": summary,
        "recommendation_basis": f"{cls.get('intent')}/{cls.get('sub_intent')}",
        "version": "poc-0.2",
        "ts": int(time.time())
    }

# 5) 추천 로직(결제) --------------------------------------------------------------
def estimate_saving(amount: int, mcc: str, rules: list, month_usage: dict):
    best = ("현재카드 유지", 0, "추가 혜택 없음")
    for r in rules:
        if "ALL" not in r.get("mcc", []) and mcc not in r.get("mcc", []): 
            continue
        rate = float(r.get("rate", 0.0))
        cap  = int(r.get("cap", 99999999))
        used = int(month_usage.get(r["name"], 0))
        remain = max(0, cap - used)
        save = min(int(amount * rate), remain)
        if save > best[1]:
            best = (r["name"], save, f"{r['name']} {int(rate*100)}% / 잔여한도 {remain:,}원")
    return best

# 6) 목표 기반 포트폴리오(간이 알고리즘) ------------------------------------------
def plan_goal(goal_name:str, target_amt:int, months:int, risk:str, seed:int=0):
    """단순 예시: 위험성향에 따라 파킹/적금/ETF 비율 추천 및 월 납입 계산."""
    risk = risk.lower()
    if risk in ["낮음","low"]:     mix = {"파킹형":0.7,"적금":0.3,"ETF":0.0}
    elif risk in ["보통","mid"]:   mix = {"파킹형":0.4,"적금":0.4,"ETF":0.2}
    else:                          mix = {"파킹형":0.2,"적금":0.4,"ETF":0.4}
    monthly = math.ceil(target_amt / max(months,1) / 1000)*1000
    # 아주 간단한 기대수익(연) 가정 → 월 환산 (과장 금지)
    assumed = {"파킹형":0.022,"적금":0.035,"ETF":0.07}
    # 진행률/보상 포인트 샘플
    random.seed(seed or months)
    progress = random.randint(5,40)  # 시작 진행률
    return {
        "goal":goal_name,"target":target_amt,"months":months,"monthly":monthly,
        "mix":mix,"assumed_yields":assumed,"progress":progress
    }

# 7) 사이드바(키 입력, 아바타 썸네일)
with st.sidebar:
    st.image(draw_avatar(), caption="금융 코치")
    st.markdown(f"**LLM 모드:** {'✅ (키 사용)' if USE_LLM else '❌ (데모 규칙)'}")

# 8) 상단 아바타 + 사용자 미디어 업로드
st.title("아바타형 금융 코치 – PoC")
colA, colB = st.columns([1,1], vertical_alignment="top")
with colA:
    st.caption("아바타 미디어 업로드(선택) – 세션 동안 유지")
    media = st.file_uploader("이미지 PNG/JPG 또는 MP4 영상", type=["png","jpg","jpeg","mp4"])
with colB:
    if media:
        render_phone_avatar("어서 오세요. 어떤 금융 고민을 도와드릴까요?",
                            media_bytes=media.read(), is_video=media.type=="video/mp4")
    else:
        render_phone_avatar("어서 오세요. 어떤 금융 고민을 도와드릴까요?")

# 9) 탭들 -------------------------------------------------------------------------
tab1, tab2, tabX, tab3 = st.tabs([
    "① 요약·분류·핸드오프", 
    "② 결제 직전 실시간 최적화(모의 PAY)",
    "③ 목표 기반 포트폴리오",
    "④ 자유 대화(옵션)"
])

# --- 탭1: 요약·분류·핸드오프
with tab1:
    user_text = st.text_area(
        "고객의 고민/문의 입력",
        value="지난달 15일 100만원 정기예금 3.5%로 들었는데 앱에는 3.2%로 보입니다. 확인 부탁드려요.",
        height=140
    )
    if st.button("요약 & 분류 & 핸드오프 생성", type="primary"):
        summary = llm_summary(user_text)
        cls = llm_classify(user_text)
        handoff = build_handoff(summary, cls)

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("요약"); st.write(summary)
            st.subheader("의도 분류"); st.json(cls, expanded=False)
        with c2:
            st.subheader("상담사 핸드오프 페이로드"); st.json(handoff, expanded=False)
            coach = "말씀 감사합니다. 요약·분류 결과를 상담사에게 정확히 전달하겠습니다. 콜백도 예약 가능해요."
            if st.toggle("감정 코칭 멘트 음성 듣기", value=False):
                audio_bytes = tts_to_mp3_bytes(coach)
                if audio_bytes: st.audio(audio_bytes, format="audio/mp3")

        # 아바타 말풍선 갱신
        try:
            if media:
                render_phone_avatar(f"요약: {summary}", media_bytes=media.getvalue(),
                                    is_video=media.type=="video/mp4")
            else:
                render_phone_avatar(f"요약: {summary}")
        except Exception:
            pass

# --- 탭2: 결제 직전 실시간 최적화 (PAY 모의)
with tab2:
    st.caption("다른 금융사의 PAY 앱처럼 **결제 직전** 화면을 모의하여 즉시 추천·적용 흐름을 시연합니다.")
    cL, cR = st.columns([1,1])
    with cL:
        merchant = st.selectbox("가맹점", ["스타커피", "버거팰리스", "메가시네마", "김밥왕"])
        mcc = {"스타커피":"CAFE","버거팰리스":"FNB","김밥왕":"FNB","메가시네마":"CINE"}[merchant]
        amount = st.number_input("결제 금액(원)", min_value=1000, value=12800, step=500)
        rules_text = st.text_area("내 카드 혜택 룰(JSON)", 
            value=json.dumps(SAMPLE_RULES, ensure_ascii=False, indent=2), height=140)
        usage_text = st.text_input("이번달 카드별 누적 적립(JSON)", value='{"Alpha Card": 5000}')
        if "pay_state" not in st.session_state:
            st.session_state.pay_state = {"applied": False, "card": None, "save": 0}
        if st.button("실시간 추천 보기"):
            rules = safe_json_loads(rules_text, SAMPLE_RULES)
            usage = safe_json_loads(usage_text, {})
            name, save, reason = estimate_saving(int(amount), mcc, rules, usage)
            st.session_state.pay_state.update({"applied": False, "card": name, "save": save, "reason":reason})
            if media:
                render_phone_avatar(f"{merchant} {money(amount)} 결제 예정—추천: {name} (절약 {money(save)})",
                                    media_bytes=media.getvalue(), is_video=media.type=="video/mp4")
            else:
                render_phone_avatar(f"{merchant} {money(amount)}—추천 {name} (절약 {money(save)})")
        if st.button("추천 카드 적용(모의)"):
            st.session_state.pay_state["applied"] = True
            msg = f"✅ {st.session_state.pay_state['card']} 적용됨! 이번 결제 절약 {money(st.session_state.pay_state['save'])}"
            st.success(msg)
            # 버블 갱신
            if media:
                render_phone_avatar(msg, media_bytes=media.getvalue(), is_video=media.type=="video/mp4")
            else:
                render_phone_avatar(msg)
    with cR:
        st.subheader("PAY 미니 화면(모의)")
        ps = st.session_state.pay_state
        st.write("• 추천 카드:", ps.get("card") or "—")
        st.write("• 예상 절약:", money(ps.get("save",0)))
        st.write("• 사유:", ps.get("reason") or "—")
        st.info("※ 실서비스에선 카드 보유/혜택/잔여한도·가맹점 MCC·쿠폰/행사 등을 합산하여 라우팅합니다. (PoC는 로컬 룰)")

    st.divider()
    st.caption("거래 로그(샘플/업로드 가능)")
    up = st.file_uploader("CSV 업로드", type=["csv"], key="csv_pay")
    tx = pd.read_csv(up) if up else SAMPLE_TX.copy()
    st.dataframe(tx, use_container_width=True)

# --- 탭3: 목표 기반 포트폴리오
with tabX:
    st.caption("목표를 만들면 월 납입·추천 배분(파킹/적금/ETF 예시)·진행률을 보여줍니다.")
    gcol1, gcol2 = st.columns([1,1])
    with gcol1:
        goal = st.text_input("목표 이름", value="여행 자금")
        target = st.number_input("목표 금액(원)", min_value=100000, value=2000000, step=100000)
        months = st.number_input("기간(개월)", min_value=1, value=8, step=1)
        risk = st.selectbox("위험 성향", ["낮음","보통","높음"], index=1)
        if st.button("목표 플랜 생성"):
            plan = plan_goal(goal, int(target), int(months), risk)
            st.session_state.goal_plan = plan
            # 아바타 말풍선
            msg = f"'{goal}' 달성 플랜 생성! 월 {money(plan['monthly'])}로 {months}개월."
            render_phone_avatar(msg, media_bytes=(media.getvalue() if media else None),
                                is_video=(media and media.type=="video/mp4"))
    with gcol2:
        plan = st.session_state.get("goal_plan")
        if plan:
            st.subheader(f"목표: {plan['goal']}")
            st.write(f"목표 금액: {money(plan['target'])} / 기간: {plan['months']}개월")
            st.progress(min(plan["progress"],100)/100)
            st.write(f"권장 월 납입: **{money(plan['monthly'])}**")
            st.write("권장 배분:")
            st.json(plan["mix"])
            st.write("가정 수익(연):", plan["assumed_yields"])
            # 간단한 월별 스케줄 표
            rows = [{"월":i+1, "권장 납입": plan["monthly"], "누적": plan["monthly"]*(i+1)} for i in range(plan["months"])]
            st.dataframe(pd.DataFrame(rows))

# --- 탭4: 자유 대화
with tab3:
    st.caption("Gemini 키가 설정된 경우에만 활성화됩니다.")
    if "chat_hist" not in st.session_state:
        st.session_state.chat_hist = []
    for role, text in st.session_state.chat_hist:
        with st.chat_message("user" if role=="user" else "assistant"):
            st.markdown(text)
    if not USE_LLM:
        st.info("LLM 키가 없어 자유 대화는 비활성화")
    else:
        if msg := st.chat_input("메시지를 입력하세요"):
            # 간단한 히스토리 기반 대화
            history = "\n".join([("User: "+t if r=="user" else "Assistant: "+t) 
                                 for r,t in st.session_state.chat_hist]) + f"\nUser: {msg}\nAssistant:"
            try:
                res = MODEL.generate_content(history)
                reply = getattr(res, "text", str(res)).strip()
            except Exception as e:
                reply = f"[대화 오류: {e}]"
            st.session_state.chat_hist += [("user", msg), ("assistant", reply)]
            st.rerun()

st.markdown("---")
st.caption("본 PoC는 문서의 핵심 시나리오(아바타·상담사 핸드오프·결제 직전 최적화·목표 기반 관리)를 요약 구현합니다. :contentReference[oaicite:1]{index=1}")

