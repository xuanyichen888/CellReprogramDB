"""快速诊断 DeepSeek API 返回内容 — 用真实条目测试"""
import os, csv
from openai import OpenAI

API_KEY  = os.environ.get("DEEPSEEK_API_KEY", "")
BASE_URL = "https://api.deepseek.com"
MODEL    = "deepseek-v4-flash"

if not API_KEY:
    raise SystemExit("请先: export DEEPSEEK_API_KEY=sk-...")

# 读 PMID=39604385 的全文（第一个失败的条目）
TARGET_PMID = "39604385"
ft_row = None
for row in csv.DictReader(open("fulltext.csv", encoding="utf-8")):
    if row["pmid"] == TARGET_PMID:
        ft_row = row
        break

if not ft_row:
    raise SystemExit(f"fulltext.csv 里找不到 PMID={TARGET_PMID}")

methods = ft_row.get("methods_text", "")
results = ft_row.get("results_text", "")
print(f"PMID: {TARGET_PMID}")
print(f"methods_text 长度: {len(methods)} 字符")
print(f"results_text 长度: {len(results)} 字符\n")

SYSTEM_PROMPT = """\
You are a biomedical expert in cell reprogramming.
Given a reprogramming entry and paper text, determine if the single TF is standalone or part of a cocktail.
Respond ONLY with valid JSON: {"status": "standalone_valid"|"cocktail_member"|"unclear", "reasoning": "..."}
"""

user_content = (
    f"PMID: {TARGET_PMID}\n"
    f"Source cell: fibroblast\nTarget cell: neuron\nSingle TF: MYT1L\n\n"
    f"[METHODS SECTION]\n{methods[:6000]}\n\n"
    f"[RESULTS SECTION]\n{results[:6000]}"
)
print(f"user_content 总长度: {len(user_content)} 字符\n")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
resp = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ],
    temperature=0.0,
    max_tokens=1024,
)

print(f"finish_reason: {resp.choices[0].finish_reason}")
print(f"原始返回 (全部): {repr(resp.choices[0].message.content)}")
# 检查是否有 reasoning_content（deepseek reasoner 模型才有）
rc = getattr(resp.choices[0].message, "reasoning_content", None)
if rc:
    print(f"reasoning_content 前200字: {repr(rc[:200])}")
