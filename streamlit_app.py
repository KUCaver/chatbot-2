# streamlit_app.py
# -------------------------------------------------------------
# 설치: pip install -U streamlit google-generativeai gTTS pillow pandas
# 실행: streamlit run streamlit_app.py
#  - LLM 키 없으면 규칙기반 폴백
#  - 키 넣으면 Gemini로 요약/분류/자유대화 강화
# -------------------------------------------------------------
import os, io, json, time, base64, math, random, re
import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
from PIL import Image, ImageDraw
from gtts import gTTS

# ============ 공통 설정 ============
st.set_page_config(page_title="아바타 금융 코치 PoC (단일 챗 UI)", page_icon="💬", layout="centered")
st.caption("※ PoC 고지: 결제/지오펜싱/CRM 연동은 모의 시연입니다.")

# 사이드바: 키 입력 (Secrets → Env → Sidebar)
with st.sidebar:
    st.header("설정")
    key_from_sidebar = st.text_input("Gemini API Key (GOOGLE_API_KEY)", type="password")
    API_KEY = (st.secrets.get("GOOGLE_API_KEY", "")
               or os.getenv("GOOGLE_API_KEY", "")
               or key_from_sidebar)
    st.markdown("---")
    st.caption("키가 없으면 규칙기반 데모 모드로 작동합니다.")

# LLM 초기화
USE_LLM, MODEL = False, None
if API_KEY:
    try:
        import google.generativeai as genai
        genai.configure(api_key=API_KEY)
        MODEL = genai.GenerativeModel("gemini-1.5-flash-latest")
        USE_LLM = True
    except Exception as e:
        st.error(f"Gemini 초기화 실패: {e}")
else:
    st.info("LLM 키 미설정: 규칙기반 모드로 동작합니다.")

# ============ 유틸 ============
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
    try:
        return json.loads(s)
    except Exception:
        return default

def money(x):
    try:
        return f"{int(x):,}원"
    except:
        return str(x)

# 샘플 룰/데이터
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

# ============ 아바타(폰 프레임) ============
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

# ============ 기능 로직 ============
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
        "version": "poc-0.3",
        "ts": int(time.time())
    }

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

def plan_goal(goal_name:str, target_amt:int, months:int, risk:str, seed:int=0):
    risk = (risk or "").lower()
    if risk in ["낮음","low"]:     mix = {"파킹형":0.7,"적금":0.3,"ETF":0.0}
    elif risk in ["보통","mid"]:   mix = {"파킹형":0.4,"적금":0.4,"ETF":0.2}
    else:                          mix = {"파킹형":0.2,"적금":0.4,"ETF":0.4}
    monthly = math.ceil(target_amt / max(months,1) / 1000)*1000
    assumed = {"파킹형":0.022,"적금":0.035,"ETF":0.07}
    random.seed(seed or months)
    progress = random.randint(5,40)
    return {
        "goal":goal_name,"target":target_amt,"months":months,"monthly":monthly,
        "mix":mix,"assumed_yields":assumed,"progress":progress
    }

# ============ 인텐트 라우팅 (자연어) ============
INTENT_HELP = """
**가능한 요청 (예시)**  
- 요약/분류/핸드오프: “정기예금 금리 불일치 정리해서 핸드오프 만들어줘”
- 결제 최적화: “스타커피 12800원 결제 예정 추천 카드 적용해줘”
  · 파라미터 직입력도 가능: `결제 merchant=스타커피 amount=12800 mcc=CAFE`
- 목표 플랜: “여행 자금 200만원 8개월 보통 위험으로 목표 플랜”
- 일반 대화: 그냥 물어보면 돼요.
"""

def parse_struct_kv(text: str) -> dict:
    """merchant=스타커피 amount=12000 mcc=CAFE 같은 KV 추출"""
    kv = {}
    for m in re.finditer(r'(\w+)\s*=\s*([^\s]+)', text):
        k, v = m.group(1).lower(), m.group(2)
        kv[k] = v
    return kv

def detect_intent(message: str) -> str:
    t = message.strip().lower()
    if t.startswith("/help") or "도움말" in t:
        return "help"
    if any(k in t for k in ["결제", "pay", "카드 추천", "추천 카드"]):
        return "pay"
    if any(k in t for k in ["목표", "포트폴리오", "플랜"]):
        return "goal"
    if any(k in t for k in ["요약", "핸드오프", "분류"]):
        return "handoff"
    return "chat"

# ============ UI(단일 챗) ============
st.title("아바타형 금융 코치 – 단일 대화형 UI")
st.caption("자유롭게 입력하면 필요한 기능(요약/핸드오프, 결제 최적화, 목표 플랜)이 자동 수행됩니다.")

# 아바타 미디어(선택)
media = st.file_uploader("아바타 미디어 업로드(선택, PNG/JPG/MP4)", type=["png","jpg","jpeg","mp4"])
if "avatar_media" not in st.session_state:
    st.session_state.avatar_media = None
if media:
    st.session_state.avatar_media = (media.read(), media.type=="video/mp4")
render_phone_avatar("어서 오세요. 어떤 금융 고민을 도와드릴까요?",
                    *(st.session_state.avatar_media or (None, False)))

# 세션 상태
if "chat_hist" not in st.session_state:
    st.session_state.chat_hist = []

# 과거 메시지 출력
for role, text in st.session_state.chat_hist:
    with st.chat_message("user" if role=="user" else "assistant"):
        st.markdown(text)

# 입력
msg = st.chat_input("메시지를 입력하세요. (/help: 사용법)")
if msg:
    st.session_state.chat_hist.append(("user", msg))
    intent = detect_intent(msg)

    if intent == "help":
        reply = INTENT_HELP

    elif intent == "handoff":
        # 요약/분류/핸드오프
        summary = llm_summary(msg)
        cls = llm_classify(msg)
        handoff = build_handoff(summary, cls)
        reply = (
            f"**요약**\n{summary}\n\n"
            f"**의도 분류**\n```json\n{json.dumps(cls, ensure_ascii=False, indent=2)}\n```\n"
            f"**상담사 핸드오프**\n```json\n{json.dumps(handoff, ensure_ascii=False, indent=2)}\n```"
        )
        # 아바타 말풍선 갱신
        render_phone_avatar(f"요약 완료: {summary}",
                            *(st.session_state.avatar_media or (None, False)))

    elif intent == "pay":
        # 결제 최적화
        kv = parse_struct_kv(msg)
        # 자연어 추정
        amount = int(re.search(r'(\d{3,})\s*원?', msg).group(1)) if re.search(r'(\d{3,})\s*원?', msg) else int(kv.get("amount", "12000"))
        merchant = kv.get("merchant") or ( "스타커피" if "커피" in msg else "메가시네마" if "시네마" in msg or "영화" in msg else "김밥왕")
        mcc = kv.get("mcc") or ( "CAFE" if "커피" in msg else "CINE" if "영화" in msg else "FNB")
        # 룰/누적 사용량 입력이 있으면 JSON 파싱
        rules = safe_json_loads(kv.get("rules",""), SAMPLE_RULES)
        usage = safe_json_loads(kv.get("usage",""), {"Alpha Card": 5000})

        name, save, reason = estimate_saving(amount, mcc, rules, usage)
        payload = {
            "merchant": merchant, "mcc": mcc, "amount": amount,
            "recommended_card": name, "expected_saving": save, "reason": reason,
            "ts": int(time.time())
        }
        reply = (
            f"**결제 직전 최적화(모의)**\n"
            f"- 가맹점: {merchant} / MCC: {mcc}\n"
            f"- 금액: {money(amount)}\n"
            f"- 추천 카드: **{name}**\n"
            f"- 예상 절약: **{money(save)}**\n"
            f"- 사유: {reason}\n\n"
            f"**적용 페이로드(모의)**\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
        )
        # 아바타 말풍선
        render_phone_avatar(f"{merchant} {money(amount)}—추천 {name} (절약 {money(save)})",
                            *(st.session_state.avatar_media or (None, False)))

    elif intent == "goal":
        # 목표 플랜
        # 자연어에서 금액/개월/위험 추출
        amt_match = re.search(r'(\d[\d,]{2,})\s*원', msg)
        months_match = re.search(r'(\d{1,2})\s*개월', msg)
        risk = "보통"
        if "낮음" in msg: risk = "낮음"
        elif "높음" in msg: risk = "높음"
        target_amt = int(amt_match.group(1).replace(",","")) if amt_match else 2_000_000
        months = int(months_match.group(1)) if months_match else 8
        # 목표명
        goal_name = "여행 자금"
        for key in ["여행", "장비", "학비", "이사", "자동차", "결혼", "기타"]:
            if key in msg:
                goal_name = f"{key} 자금"; break

        plan = plan_goal(goal_name, target_amt, months, risk)
        # 표(간단 스케줄)
        rows = [{"월":i+1, "권장 납입": plan["monthly"], "누적": plan["monthly"]*(i+1)} for i in range(plan["months"])]
        df = pd.DataFrame(rows)

        reply = (
            f"**목표 플랜 생성**\n"
            f"- 목표: {plan['goal']} / 기간: {plan['months']}개월\n"
            f"- 목표 금액: {money(plan['target'])}\n"
            f"- 권장 월 납입: **{money(plan['monthly'])}**\n"
            f"- 권장 배분: {json.dumps(plan['mix'], ensure_ascii=False)}\n"
            f"- 가정 수익(연): {json.dumps(plan['assumed_yields'], ensure_ascii=False)}\n"
            f"- 진행률(시작치): {plan['progress']}%\n"
        )
        # 아바타 말풍선
        render_phone_avatar(f"'{plan['goal']}' 월 {money(plan['monthly'])}로 {months}개월!",
                            *(st.session_state.avatar_media or (None, False)))
        # 데이터프레임도 함께 보여주기
        with st.chat_message("assistant"):
            st.markdown(reply)
            st.dataframe(df, use_container_width=True)
        st.session_state.chat_hist.append(("assistant", reply))
        st.stop()

    else:
        # 일반 대화 (LLM 있으면 LLM, 없으면 간단 응답)
        if USE_LLM and MODEL:
            try:
                history = "\n".join([("User: "+t if r=="user" else "Assistant: "+t) 
                                     for r,t in st.session_state.chat_hist if r in ("user","assistant")])
                prompt = f"{history}\nUser: {msg}\nAssistant:"
                res = MODEL.generate_content(prompt)
                reply = getattr(res, "text", str(res)).strip()
            except Exception as e:
                reply = f"[대화 오류: {e}]"
        else:
            # 폴백: 간단 규칙
            reply = "말씀 감사합니다. 자세한 기능은 '/help'를 참고해 주세요."
        render_phone_avatar(reply[:40] + ("..." if len(reply) > 40 else ""),
                            *(st.session_state.avatar_media or (None, False)))

    with st.chat_message("assistant"):
        st.markdown(reply)
    st.session_state.chat_hist.append(("assistant", reply))
    st.rerun()

st.markdown("---")
st.caption("본 PoC는 단일 대화형 UI에서 요약·핸드오프 / 결제 최적화 / 목표 플랜을 자연어로 트리거합니다.")
