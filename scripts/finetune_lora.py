"""
Qwen3 LoRA 파인튜닝 — 규정 RAG 답변 (근거 인용 + 근거 없으면 '찾을 수 없음')

수업 PEFT/LoRA 실습(ai_cls_22-2) 방식을 Apple Silicon(MPS)에서 돌 수 있게 단순화했습니다.
- 데이터: scripts/build_lora_data.py 결과 (data/processed/lora/train.jsonl)
- 입력 형식은 서비스와 동일한 chat template (enable_thinking=False)
- 손실은 답변 토큰에만 계산 (프롬프트·근거 부분은 -100으로 마스킹)
- 결과: models/llm/<이름>/  (.env 의 LOCAL_ADAPTER 에 경로를 넣으면 서비스에서 사용)

사용: python scripts/finetune_lora.py [--model Qwen/Qwen3-1.7B] [--epochs 3]
"""

import argparse
import json
import math
import random
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import HF_CACHE_DIR, HF_TOKEN, MODELS_DIR, PROCESSED_DIR  # noqa: E402
from app.core.llm import get_device  # noqa: E402


def build_examples(tokenizer, rows, max_len):
    examples = []
    for r in rows:
        messages = [{"role": "system", "content": r["system"]}, {"role": "user", "content": r["prompt"]}]
        prompt = tokenizer.apply_chat_template(messages, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        p_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
        a_ids = tokenizer(r["answer"] + "<|im_end|>", add_special_tokens=False)["input_ids"]
        if len(p_ids) + len(a_ids) > max_len:
            p_ids = p_ids[-(max_len - len(a_ids)):]   # 긴 근거는 앞쪽을 잘라 답변 부분은 항상 남김
        examples.append({"input_ids": p_ids + a_ids, "labels": [-100] * len(p_ids) + a_ids})
    return examples


def main():
    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3-1.7B")
    ap.add_argument("--name", default="qwen3-1.7b-rag-lora")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--accum", type=int, default=4)
    ap.add_argument("--max-len", type=int, default=2048)
    args = ap.parse_args()

    random.seed(42)
    torch.manual_seed(42)
    device = get_device()
    rows = [json.loads(l) for l in open(PROCESSED_DIR / "lora" / "train.jsonl")]

    tokenizer = AutoTokenizer.from_pretrained(args.model, token=HF_TOKEN, cache_dir=HF_CACHE_DIR)
    model = AutoModelForCausalLM.from_pretrained(
        args.model, token=HF_TOKEN, cache_dir=HF_CACHE_DIR, dtype=torch.float32, low_cpu_mem_usage=True,
    ).to(device)
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model.config.use_cache = False

    lora = LoraConfig(
        r=args.rank, lora_alpha=args.rank * 2, lora_dropout=0.05, task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    )
    model = get_peft_model(model, lora)
    model.print_trainable_parameters()

    examples = build_examples(tokenizer, rows, args.max_len)
    print(f"학습 예시 {len(examples)}건 | 평균 길이 {sum(len(e['input_ids']) for e in examples) / len(examples):.0f} 토큰 | 장치 {device}")

    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.0)
    total_steps = math.ceil(len(examples) * args.epochs / args.accum)
    warmup = max(1, total_steps // 10)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / warmup) * max(0.0, (total_steps - s) / max(1, total_steps - warmup)),
    )

    model.train()
    step, t0 = 0, time.time()
    for epoch in range(1, args.epochs + 1):
        random.shuffle(examples)
        losses = []
        for i, ex in enumerate(examples, start=1):
            ids = torch.tensor([ex["input_ids"]], device=device)
            labels = torch.tensor([ex["labels"]], device=device)
            loss = model(input_ids=ids, labels=labels).loss / args.accum
            loss.backward()
            losses.append(loss.item() * args.accum)
            if i % args.accum == 0 or i == len(examples):
                torch.nn.utils.clip_grad_norm_(params, 1.0)
                opt.step()
                sched.step()
                opt.zero_grad()
                step += 1
        print(f"epoch {epoch}/{args.epochs} | loss {sum(losses) / len(losses):.4f} | step {step}/{total_steps} | {time.time() - t0:.0f}s", flush=True)

    out = MODELS_DIR / "llm" / args.name
    out.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(out)
    (out / "train_info.json").write_text(json.dumps({
        "base_model": args.model, "examples": len(examples), "epochs": args.epochs, "lr": args.lr,
        "rank": args.rank, "seconds": round(time.time() - t0),
    }, ensure_ascii=False, indent=2))
    print(f"✅ LoRA 어댑터 저장: {out}")


if __name__ == "__main__":
    main()
