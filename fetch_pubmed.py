"""
Step 1: Download reprogramming paper abstracts from PubMed
Output: papers.csv with columns: pmid, title, abstract, year
"""

import time
import argparse
import pandas as pd
from Bio import Entrez

Entrez.email = "xuanyichen888@gmail.com"

MAX_PER_QUERY = 500  # 每个查询最多抓多少篇（提高以覆盖早期文献）

# ── 搜索词分四批：TF / small molecule / miRNA+knockdown / 经典早期论文 ──────────

# 早期论文（2006-2015）常用 "defined factors" / "reprogramming factors"，
# 而非 "transcription factor"，因此所有TF查询都加入这两个备选词

TF_FACTOR_TERMS = (
    '(transcription factor OR defined factors OR reprogramming factors)'
)

TF_QUERIES = [
    # 主查询
    f'(cell reprogramming[Title/Abstract]) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(direct conversion OR direct reprogramming OR transdifferentiation OR induced pluripotent)[Title/Abstract]',

    # 神经元
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(neuron OR dopaminergic OR GABAergic OR motor neuron OR interneuron)[Title/Abstract]',

    # 心肌细胞
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(cardiomyocyte OR cardiac muscle OR induced cardiomyocyte)[Title/Abstract]',

    # 肝细胞
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(hepatocyte OR hepatic cell OR liver reprogramming)[Title/Abstract]',

    # 胰岛β细胞
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(beta cell OR pancreatic beta OR insulin-producing OR islet)[Title/Abstract]',

    # 内皮/造血
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(endothelial cell OR hematopoietic OR erythrocyte OR megakaryocyte)[Title/Abstract]',

    # 免疫细胞
    f'(direct reprogramming OR transdifferentiation) AND {TF_FACTOR_TERMS}[Title/Abstract] AND '
    '(dendritic cell OR macrophage OR T cell OR B cell OR natural killer)[Title/Abstract]',
]

SMALL_MOLECULE_QUERIES = [
    # chemical reprogramming 主查询
    '(chemical reprogramming[Title/Abstract]) AND '
    '(small molecule OR drug[Title/Abstract])',

    # 小分子诱导iPSC
    '(small molecule reprogramming[Title/Abstract]) AND '
    '(induced pluripotent OR iPSC OR pluripotency)[Title/Abstract]',

    # 小分子直接转分化
    '(small molecule[Title/Abstract]) AND (transdifferentiation OR direct conversion[Title/Abstract]) AND '
    '(neuron OR cardiomyocyte OR hepatocyte OR beta cell)[Title/Abstract]',

    # chemical-only reprogramming
    '(chemical-only reprogramming[Title/Abstract]) OR '
    '(chemically induced pluripotent[Title/Abstract])',
]

MIRNA_QUERIES = [
    # miRNA重编程
    '(miRNA reprogramming[Title/Abstract]) OR (microRNA reprogramming[Title/Abstract])',

    # miRNA + 直接转分化
    '(miRNA[Title/Abstract] OR microRNA[Title/Abstract]) AND '
    '(direct reprogramming OR transdifferentiation[Title/Abstract]) AND '
    '(neuron OR cardiomyocyte OR iPSC)[Title/Abstract]',

    # 基因敲除/knockdown辅助重编程
    '(knockdown OR knockout OR shRNA OR siRNA[Title/Abstract]) AND '
    '(reprogramming OR transdifferentiation[Title/Abstract]) AND '
    '(transcription factor OR cell conversion)[Title/Abstract]',
]

# ── 专门针对经典早期论文的查询（按landmark因子名称搜索）────────────────────────
# 这些查询覆盖2006-2015年的里程碑文章，这些文章通常用具体因子名而非通用术语

CLASSIC_QUERIES = [
    # iPSC经典：Yamanaka (Oct4/Sox2/Klf4/c-Myc) + Thomson (OCT4/SOX2/NANOG/LIN28)
    '(Oct4 OR Sox2 OR Klf4[Title/Abstract]) AND '
    '(induced pluripotent OR iPSC OR pluripotent stem cell[Title/Abstract]) AND '
    '(reprogramming OR induction)[Title/Abstract]',

    # 心脏重编程：Ieda GMT (Gata4+Mef2c+Tbx5)
    '(Gata4[Title/Abstract] OR Mef2c[Title/Abstract] OR Tbx5[Title/Abstract]) AND '
    '(cardiomyocyte OR cardiac reprogramming OR cardiac fibroblast)[Title/Abstract]',

    # 神经元直接转化：Vierbuchen (Ascl1+Brn2+Myt1l), Pang (miR-9/137)
    '(Ascl1[Title/Abstract] OR Brn2[Title/Abstract] OR Ngn2[Title/Abstract]) AND '
    '(direct conversion OR direct reprogramming OR induced neuron)[Title/Abstract]',

    # 胰腺β细胞：Zhou (Ngn3+Pdx1+Mafa)
    '(Ngn3[Title/Abstract] OR Pdx1[Title/Abstract] OR Mafa[Title/Abstract]) AND '
    '(pancreatic reprogramming OR beta cell OR exocrine)[Title/Abstract]',

    # 肝细胞：Huang/Morris (Foxa1/2/3, Hnf4a)
    '(Foxa2[Title/Abstract] OR Hnf4a[Title/Abstract] OR Foxa1[Title/Abstract]) AND '
    '(hepatocyte OR hepatic reprogramming OR liver cell)[Title/Abstract]',

    # 广谱 "defined factors" 直接转化（早期论文核心词）
    # 加入 "induction" 覆盖 Huang 2011 (iHep) 等用 "induction" 而非 "reprogramming" 的文章
    '(defined factors[Title/Abstract]) AND '
    '(direct conversion OR reprogramming OR transdifferentiation OR induction)[Title/Abstract] AND '
    '(fibroblast OR somatic cell)[Title/Abstract]',

    # iHep / induced hepatocyte（Huang 2011: Gata4+Hnf1a+Foxa3）
    '(iHep OR induced hepatocyte[Title/Abstract] OR hepatocyte-like cells[Title/Abstract]) AND '
    '(fibroblast OR reprogramming OR conversion OR induction)[Title/Abstract]',

    # 胰腺外分泌细胞体内重编程（Zhou 2008: in vivo, exocrine→beta-cell）
    '(pancreatic exocrine[Title/Abstract] OR exocrine cells[Title/Abstract]) AND '
    '(reprogramming OR conversion OR beta-cell OR beta cell)[Title/Abstract]',

    # In vivo reprogramming（Kurita 2018 皮肤, Qian 2012 心脏等）
    # 这类论文标题/摘要用 "in vivo reprogramming" 而非 "direct reprogramming"
    '(in vivo reprogramming[Title/Abstract]) AND '
    '(transcription factor OR defined factors OR cocktail)[Title/Abstract]',

    # "fate conversion" / "cell fate switch" — 部分论文不用 reprogramming 一词
    '(fate conversion[Title/Abstract] OR cell fate switch[Title/Abstract] OR '
    'fate switching[Title/Abstract]) AND '
    '(transcription factor OR defined factors)[Title/Abstract] AND '
    '(fibroblast OR somatic cell OR neuron OR cardiomyocyte OR hepatocyte)[Title/Abstract]',

    # 皮肤/角质细胞重编程（Kurita: DNp63/GRHL2/TFAP2A）
    '(keratinocyte[Title/Abstract]) AND '
    '(direct reprogramming OR transdifferentiation OR lineage conversion OR in vivo reprogramming)'
    '[Title/Abstract] AND (fibroblast OR somatic cell)[Title/Abstract]',

    # 多巴胺能神经元：Caiazzo/Kim (Ascl1/Nurr1/Lmx1a)
    '(Nurr1[Title/Abstract] OR Lmx1a[Title/Abstract]) AND '
    '(dopaminergic OR dopamine neuron OR midbrain)[Title/Abstract] AND '
    '(reprogramming OR conversion)[Title/Abstract]',

    # 造血细胞：Riddell/Graf (C/EBPa, GATA1, PU.1)
    '(GATA1[Title/Abstract] OR PU.1[Title/Abstract] OR CEBPa[Title/Abstract]) AND '
    '(reprogramming OR conversion OR transdifferentiation)[Title/Abstract] AND '
    '(hematopoietic OR blood cell OR macrophage OR neutrophil)[Title/Abstract]',
]

# ── 覆盖缺口补检索 ──────────────────────────────────────────────────────────
# 针对 postdoc 指出的遗漏术语：in vivo / lineage reprogramming、wound-resident
# 细胞、in situ 命运转换等。这些老/特殊术语不被上面的主查询覆盖。
COVERAGE_GAP_QUERIES = [
    '("wound-resident"[Title/Abstract] OR "wound resident"[Title/Abstract]) AND '
    '(reprogramming OR conversion OR transdifferentiation OR fate)[Title/Abstract]',

    '"in vivo reprogramming"[Title/Abstract]',

    '("in vivo"[Title/Abstract]) AND '
    '("direct reprogramming"[Title/Abstract] OR "lineage reprogramming"[Title/Abstract])',

    '"lineage reprogramming"[Title/Abstract]',

    '("in vivo"[Title/Abstract]) AND (transdifferentiation[Title/Abstract])',

    '("in situ"[Title/Abstract]) AND (reprogramming OR "fate conversion")[Title/Abstract] AND '
    '(transcription factor OR defined factors)[Title/Abstract]',
]

# ── 宽检索：先多抓候选，再交给 LLM / curation 过滤 ────────────────────────────
# Dr. Wang 的反馈是原 query 可能过于严格，尤其会漏掉不用 "reprogramming"
# 但实际是 cell fate / lineage conversion 的论文。这里保持为独立 category，
# 便于先做免费候选量化，再决定是否花 API 成本抽取。
BROAD_DISCOVERY_QUERIES = [
    '"cell fate conversion"[Title/Abstract]',

    '("fate conversion"[Title/Abstract] OR "cell fate switch"[Title/Abstract] OR '
    '"fate switching"[Title/Abstract]) AND '
    '(cell OR fibroblast OR somatic OR neuron OR hepatocyte OR cardiomyocyte OR beta cell)'
    '[Title/Abstract]',

    '("direct conversion"[Title/Abstract] OR "direct lineage conversion"[Title/Abstract]) AND '
    '(cell OR fibroblast OR somatic OR neuron OR hepatocyte OR cardiomyocyte OR beta cell)'
    '[Title/Abstract]',

    '"lineage conversion"[Title/Abstract]',

    '(transdifferentiation[Title/Abstract]) AND '
    '(cell OR fibroblast OR somatic OR neuron OR hepatocyte OR cardiomyocyte OR beta cell OR '
    'macrophage OR endothelial)[Title/Abstract]',

    '("cell identity conversion"[Title/Abstract] OR "cell-type conversion"[Title/Abstract] OR '
    '"cell type conversion"[Title/Abstract] OR "somatic cell conversion"[Title/Abstract])',

    '("induced neuron"[Title/Abstract] OR "induced neurons"[Title/Abstract] OR '
    '"induced neuronal"[Title/Abstract] OR "induced neural"[Title/Abstract]) AND '
    '(conversion OR reprogramming OR transdifferentiation OR fibroblast OR somatic)'
    '[Title/Abstract]',

    '("induced hepatocyte"[Title/Abstract] OR "induced hepatocytes"[Title/Abstract] OR '
    '"hepatocyte-like cell"[Title/Abstract] OR "hepatocyte-like cells"[Title/Abstract] OR '
    'iHep[Title/Abstract]) AND '
    '(conversion OR reprogramming OR transdifferentiation OR fibroblast OR somatic)'
    '[Title/Abstract]',

    '("induced cardiomyocyte"[Title/Abstract] OR "induced cardiomyocytes"[Title/Abstract] OR '
    '"cardiomyocyte-like cell"[Title/Abstract] OR "cardiomyocyte-like cells"[Title/Abstract] OR '
    'iCM[Title/Abstract]) AND '
    '(conversion OR reprogramming OR transdifferentiation OR fibroblast OR cardiac fibroblast)'
    '[Title/Abstract]',

    '("induced beta cell"[Title/Abstract] OR "induced beta cells"[Title/Abstract] OR '
    '"insulin-producing cell"[Title/Abstract] OR "insulin-producing cells"[Title/Abstract] OR '
    '"beta-like cell"[Title/Abstract] OR "beta-like cells"[Title/Abstract]) AND '
    '(conversion OR reprogramming OR transdifferentiation OR pancreatic OR fibroblast)'
    '[Title/Abstract]',

    '("induced endothelial cell"[Title/Abstract] OR "induced endothelial cells"[Title/Abstract] OR '
    '"endothelial-like cell"[Title/Abstract] OR "endothelial-like cells"[Title/Abstract]) AND '
    '(conversion OR reprogramming OR transdifferentiation OR fibroblast OR somatic)'
    '[Title/Abstract]',

    '("master regulator"[Title/Abstract] OR "lineage-determining factor"[Title/Abstract] OR '
    '"lineage determining factor"[Title/Abstract]) AND '
    '(conversion OR reprogramming OR transdifferentiation OR cell fate)[Title/Abstract]',
]

ALL_QUERIES = [
    ("TF",             q) for q in TF_QUERIES
] + [
    ("small_molecule", q) for q in SMALL_MOLECULE_QUERIES
] + [
    ("miRNA",          q) for q in MIRNA_QUERIES
] + [
    ("classic",        q) for q in CLASSIC_QUERIES
] + [
    ("coverage_gap",   q) for q in COVERAGE_GAP_QUERIES
] + [
    ("broad_discovery", q) for q in BROAD_DISCOVERY_QUERIES
]


def search_pubmed(query, max_results):
    handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results)
    record = Entrez.read(handle)
    handle.close()
    return record["IdList"]


def search_pubmed_with_count(query, max_results):
    handle = Entrez.esearch(db="pubmed", term=query, retmax=max_results)
    record = Entrez.read(handle)
    handle.close()
    return record["IdList"], int(record.get("Count", 0))


def fetch_abstracts(pmids, batch_size=100):
    all_papers = []
    for i in range(0, len(pmids), batch_size):
        batch = pmids[i:i + batch_size]
        handle = Entrez.efetch(db="pubmed", id=batch, rettype="xml", retmode="xml")
        records = Entrez.read(handle)
        handle.close()
        for article in records["PubmedArticle"]:
            try:
                medline = article["MedlineCitation"]
                pmid    = str(medline["PMID"])
                title   = str(medline["Article"]["ArticleTitle"])
                abstract = ""
                if "Abstract" in medline["Article"]:
                    texts = medline["Article"]["Abstract"]["AbstractText"]
                    abstract = " ".join(str(t) for t in texts) if isinstance(texts, list) else str(texts)
                year = ""
                try:
                    year = str(medline["Article"]["Journal"]["JournalIssue"]["PubDate"]["Year"])
                except Exception:
                    try:
                        year = str(medline["Article"]["Journal"]["JournalIssue"]["PubDate"]["MedlineDate"])[:4]
                    except Exception:
                        year = "unknown"
                all_papers.append({"pmid": pmid, "title": title, "abstract": abstract, "year": year})
            except Exception as e:
                print(f"  Parse error: {e}")
        time.sleep(0.5)
    return all_papers


def load_existing_pmids(output_path, compare_master=True, baseline_paths=None):
    existing_pmids = set()
    existing_df = pd.DataFrame()
    try:
        existing_df = pd.read_csv(output_path, dtype=str)
        existing_pmids = set(existing_df["pmid"].tolist())
    except FileNotFoundError:
        pass

    for path in baseline_paths or []:
        if not path or path == output_path:
            continue
        try:
            baseline_df = pd.read_csv(path, dtype=str)
            existing_pmids.update(baseline_df["pmid"].dropna().astype(str).tolist())
        except FileNotFoundError:
            pass

    if compare_master:
        try:
            master_df = pd.read_csv("recipes_master_v2.csv", dtype=str)
            existing_pmids.update(master_df["pmid"].dropna().astype(str).tolist())
        except FileNotFoundError:
            pass

    return existing_df, existing_pmids


def selected_queries(categories):
    if not categories:
        return ALL_QUERIES
    wanted = {c.strip() for c in categories.split(",") if c.strip()}
    return [(category, query) for category, query in ALL_QUERIES if category in wanted]


def preview_candidates(queries, existing_pmids, max_per_query):
    seen = set()
    rows = []
    for category, query in queries:
        print(f"\n[{category}] 预览: {query[:100]}...")
        pmids, total_count = search_pubmed_with_count(query, max_per_query)
        new_pmids = [p for p in pmids if p not in existing_pmids and p not in seen]
        seen.update(new_pmids)
        rows.append({
            "category": category,
            "query": query,
            "pubmed_count": total_count,
            "retrieved_cap": len(pmids),
            "new_within_cap": len(new_pmids),
        })
        cap_note = " (capped)" if total_count > max_per_query else ""
        print(
            f"  PubMed count {total_count}{cap_note}; retrieved {len(pmids)}; "
            f"new within cap {len(new_pmids)}"
        )
        time.sleep(0.35)

    summary = pd.DataFrame(rows)
    if len(summary):
        print("\n=== Preview summary by category ===")
        print(
            summary.groupby("category")[["pubmed_count", "retrieved_cap", "new_within_cap"]]
            .sum()
            .sort_values("new_within_cap", ascending=False)
            .to_string()
        )
        print(f"\nUnique new PMIDs within per-query caps: {len(seen)}")
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Download or preview PubMed candidates for CellReprogramDB."
    )
    parser.add_argument(
        "--preview-only",
        action="store_true",
        help="Only count/search PMIDs; do not fetch abstracts or write papers.csv.",
    )
    parser.add_argument(
        "--categories",
        default="",
        help="Comma-separated categories to run, e.g. broad_discovery,coverage_gap. Default: all.",
    )
    parser.add_argument(
        "--output",
        default="papers.csv",
        help="Output CSV for fetched abstracts. Default: papers.csv.",
    )
    parser.add_argument(
        "--baseline",
        default="papers.csv",
        help="Optional existing candidate CSV to count as already covered even when writing to another output. "
             "Default: papers.csv.",
    )
    parser.add_argument(
        "--max-per-query",
        type=int,
        default=MAX_PER_QUERY,
        help=f"Maximum PMID records retrieved per query. Default: {MAX_PER_QUERY}.",
    )
    parser.add_argument(
        "--no-compare-master",
        action="store_true",
        help="Do not count PMIDs already present in recipes_master_v2.csv as existing.",
    )
    args = parser.parse_args()

    queries = selected_queries(args.categories)
    if not queries:
        raise SystemExit(f"No queries selected for categories={args.categories!r}")

    # 读已有 PMID。默认同时把当前 master table 的 PMID 当作已覆盖，避免
    # 宽检索 preview 把已经入库的论文当成新增候选。
    existing_df, existing_pmids = load_existing_pmids(
        args.output,
        compare_master=not args.no_compare_master,
        baseline_paths=[args.baseline],
    )
    print(f"已有/已覆盖 PMID: {len(existing_pmids)} 篇")

    if args.preview_only:
        preview_candidates(queries, existing_pmids, args.max_per_query)
        return

    all_new = []
    seen    = set()

    for category, query in queries:
        print(f"\n[{category}] 搜索: {query[:80]}...")
        pmids     = search_pubmed(query, args.max_per_query)
        new_pmids = [p for p in pmids if p not in existing_pmids and p not in seen]
        print(f"  找到 {len(pmids)} 篇，新增 {len(new_pmids)} 篇")
        if not new_pmids:
            continue
        papers = fetch_abstracts(new_pmids)
        all_new.extend(papers)
        seen.update(p["pmid"] for p in papers)

    if not all_new:
        print("\n没有新论文。")
        return

    new_df   = pd.DataFrame(all_new)
    combined = pd.concat([existing_df, new_df], ignore_index=True)
    combined.drop_duplicates(subset="pmid", inplace=True)
    combined.to_csv(args.output, index=False, encoding="utf-8")
    print(f"\n完成！新增 {len(new_df)} 篇，总计 {len(combined)} 篇 → {args.output}")


if __name__ == "__main__":
    main()
