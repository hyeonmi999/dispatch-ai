# ==================================================
# LLM 선택: Qwen3 (로컬, 기본) / OpenAI gpt-4o-mini
# ==================================================
# 교수님 RAG 예시(ai_cls_24-2)와 같은 방식으로 Qwen3를 로드합니다.
# - apply_chat_template(enable_thinking=False) : <think> 추론 과정 출력 방지
# - model.generate() 후 입력 길이 이후 Token만 디코딩
#
# 차이점
# - 서버 시작 시가 아니라 "처음 사용할 때" 한 번만 로드 (예측·지도 기능은 LLM 없이도 빠르게 시작)
# - 규정 근거 답변이므로 샘플링 대신 greedy decoding (재현성)

import threading

from app.config import (
    HF_CACHE_DIR,
    HF_TOKEN,
    LOCAL_MODEL_NAME,
    MAX_NEW_TOKENS,
    OPENAI_API_KEY,
    OPENAI_MODEL_NAME,
    REPETITION_PENALTY,
)

LLM_CHOICES = ("qwen", "openai")


class LLMConfigError(Exception):
    """LLM 설정 오류 (예: OpenAI 키 없음) → API에서 400으로 변환"""


# ==================================================
# 1. 실행 장치 선택
# ==================================================
# torch는 필요할 때만 import 합니다.
# (macOS에서 xgboost와 torch를 한 프로세스에 함께 로드하면 OpenMP 충돌로 segfault
#  → 학습 스크립트는 torch 없이, API 서버는 xgboost 없이 동작하도록 분리)

_device = {"name": None}


def get_device() -> str:
    if _device["name"] is None:
        import torch
        if torch.cuda.is_available():
            _device["name"] = "cuda"
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            _device["name"] = "mps"
        else:
            _device["name"] = "cpu"
    return _device["name"]


# ==================================================
# 2. Qwen3 Local LLM (지연 로드)
# ==================================================

_local = {"tokenizer": None, "model": None}
_local_lock = threading.Lock()


def load_local_llm():
    """Qwen3 Tokenizer와 Causal Language Model을 한 번만 로드합니다."""
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    DEVICE = get_device()

    with _local_lock:
        if _local["model"] is not None:
            return _local["tokenizer"], _local["model"]

        print("-" * 60)
        print(f"Qwen3 Local LLM 로딩 시작 | 모델: {LOCAL_MODEL_NAME} | 장치: {DEVICE}")
        print("-" * 60)

        tokenizer = AutoTokenizer.from_pretrained(
            LOCAL_MODEL_NAME, token=HF_TOKEN, cache_dir=HF_CACHE_DIR,
        )
        if tokenizer.pad_token_id is None:
            tokenizer.pad_token = tokenizer.eos_token

        # CUDA·MPS는 float16, CPU는 float32
        dtype = torch.float32 if DEVICE == "cpu" else torch.float16
        model = AutoModelForCausalLM.from_pretrained(
            LOCAL_MODEL_NAME,
            token=HF_TOKEN,
            cache_dir=HF_CACHE_DIR,
            dtype=dtype,
            low_cpu_mem_usage=True,
        ).to(DEVICE)
        model.eval()

        _local.update(tokenizer=tokenizer, model=model)
        print("✅ Qwen3 Local LLM 로딩 완료")
        return tokenizer, model


def is_local_loaded() -> bool:
    return _local["model"] is not None


def generate_local(system: str, user: str, max_new_tokens: int = MAX_NEW_TOKENS) -> str:
    import torch

    tokenizer, model = load_local_llm()

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    prompt = tokenizer.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True, enable_thinking=False,
    )
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    input_length = inputs["input_ids"].shape[1]

    with torch.inference_mode():
        output_ids = model.generate(
            **inputs,
            max_new_tokens=max_new_tokens,
            do_sample=False,
            repetition_penalty=REPETITION_PENALTY,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )

    answer = tokenizer.batch_decode(
        output_ids[:, input_length:], skip_special_tokens=True, clean_up_tokenization_spaces=False,
    )[0]
    return answer.strip()


# ==================================================
# 3. OpenAI gpt-4o-mini
# ==================================================

def generate_openai(system: str, user: str, max_new_tokens: int = MAX_NEW_TOKENS) -> str:
    if not OPENAI_API_KEY:
        raise LLMConfigError(
            "OPENAI_API_KEY가 설정되지 않았습니다. .env에 키를 추가하거나 LLM을 qwen으로 선택하세요."
        )
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(model=OPENAI_MODEL_NAME, api_key=OPENAI_API_KEY, temperature=0, max_tokens=max_new_tokens)
    try:
        res = llm.invoke([SystemMessage(content=system), HumanMessage(content=user)])
    except Exception as error:
        raise LLMConfigError(f"OpenAI 호출 실패: {error}") from error
    return res.content.strip()


# ==================================================
# 4. 공통 진입점
# ==================================================

def generate(provider: str, system: str, user: str, max_new_tokens: int = MAX_NEW_TOKENS) -> str:
    if provider not in LLM_CHOICES:
        raise LLMConfigError(f"지원하지 않는 LLM입니다: {provider} (선택: {', '.join(LLM_CHOICES)})")
    if provider == "openai":
        return generate_openai(system, user, max_new_tokens)
    return generate_local(system, user, max_new_tokens)


def llm_status() -> dict:
    return {
        "device": get_device(),
        "default": "qwen",
        "qwen": {"model": LOCAL_MODEL_NAME, "loaded": is_local_loaded()},
        "openai": {"model": OPENAI_MODEL_NAME, "configured": bool(OPENAI_API_KEY)},
    }
