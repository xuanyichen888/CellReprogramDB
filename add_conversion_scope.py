"""
Add rule-based conversion_scope and reduce high-frequency target_cell synonyms.

This intentionally avoids a full LLM re-run. The scope labels are initial,
rule-based annotations meant for filtering and review:
  - classical_reprogramming
  - lineage_conversion
  - directed_differentiation
  - cell_state_modulation
  - unclear
"""

import re
import shutil

import pandas as pd

FILE = "recipes_master_v2.csv"


def text_has(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def canonical_target(name: str) -> str:
    t = name.strip()
    low = t.lower()
    if not t:
        return t

    # iPSC / pluripotency variants. Keep naive pluripotent states separate.
    if "naive" not in low and "naïve" not in low:
        if text_has(low, r"\bhips?c\b|\bips cells?\b|induced pluripotent|chemically induced pluripotent|footprint-free human ipsc|integration-free human induced pluripotent"):
            return "induced pluripotent stem cell (iPSC)"
        if low in {"pluripotent cell", "pluripotent state", "pluripotent stem cell (cipsc)"}:
            return "induced pluripotent stem cell (iPSC)"

    # Cardiomyocyte/iCM variants, preserving clear chamber/subtype identities.
    if not text_has(low, r"sinoatrial|atrial|ventricular|left ventricle|right ventricle"):
        if text_has(low, r"cardiomyocyte-like|induced[- ]cardiomyocyte|\bicm\b|beating cardiomyocyte|functional cardiomyocyte|cardiac[- ]like cell|cardiac[- ]like myocyte|cardiac myocyte|cardiac cell"):
            return "cardiomyocyte"

    # Hepatocyte-like/iHep variants.
    if text_has(low, r"iHep|induced hepatocyte|hepatocyte-like|hepatic-like cell"):
        return "hepatocyte-like cell"
    if low in {"hepatic cell", "functional hepatocytes", "mature hepatocyte"}:
        return "hepatocyte"

    # Beta-cell / insulin-producing variants.
    if text_has(low, r"insulin[- ]producing|insulin[- ]secreting|insulin[- ]expressing|beta[- ]like|β[- ]like|pancreatic beta|pancreatic β|β-cell|beta cell"):
        return "insulin-producing cell (β-cell)"

    # Generic induced neuron variants. Preserve named neuronal subtypes.
    if text_has(low, r"induced neuronal|induced neuron|ineuron|neuronal-like|neuron-like"):
        if not text_has(low, r"dopaminergic|motor|gabaergic|glutamatergic|cortical|cholinergic|sensory|serotonergic|striatal|retinal|photoreceptor"):
            return "induced neuron (iN)"

    # Endothelial variants. Preserve explicit endothelial subtypes.
    if text_has(low, r"endothelial-like|induced endothelial|functional endothelial|vascular endothelial"):
        if not text_has(low, r"brain microvascular|lymphatic|aortic|arterial"):
            return "endothelial cell"

    # Macrophage variants.
    if text_has(low, r"macrophage-like|functional macrophages|nonleukemic macrophages"):
        if not text_has(low, r"\bM1\b|\bM2\b|tumou?r-associated|anti-inflammatory"):
            return "macrophage"

    return t


def infer_scope(row: pd.Series) -> str:
    source = row.get("source_cell", "")
    target = row.get("target_cell", "")
    title = row.get("title", "")
    evidence = row.get("evidence_sentence", "")
    combined = " ".join([source, target, title, evidence]).lower()

    if text_has(target, r"induced pluripotent|ips[c]?|pluripotent state|pluripotent cell"):
        return "classical_reprogramming"

    # Epiblast stem cell / primed → naïve pluripotent = classical reprogramming
    if text_has(target, r"na[ïi]ve pluripotent|na[ïi]ve.*stem cell|ground state"):
        return "classical_reprogramming"

    if text_has(source, r"induced pluripotent|ips[c]?|pluripotent stem|embryonic stem|hesc|mesc|esc\b|hpsc|epiblast stem"):
        return "directed_differentiation"

    if text_has(combined, r"transdifferentiation|trans-differentiation|lineage conversion|lineage switch|direct conversion|direct reprogramming"):
        return "lineage_conversion"

    if text_has(combined, r"partial reprogramming|state transition|phenotypic conversion|polarization|m1|m2|emt|endmt|metastable"):
        return "cell_state_modulation"

    # Cancer lineage plasticity (e.g. adenocarcinoma → neuroendocrine)
    if text_has(combined, r"neuroendocrine|cancer.*lineage|lineage.*cancer|lineage plasticity|tumor.*transdifferentiation|cancer.*reprogramming"):
        return "cell_state_modulation"
    if text_has(source, r"cancer|carcinoma|leukemia|lymphoma|tumor|tumour") and \
       text_has(target, r"cancer|carcinoma|neuroendocrine|small cell|mesenchymal"):
        return "cell_state_modulation"

    # MSC / progenitor / mesenchymal → osteogenic/chondrogenic/adipogenic
    if text_has(target, r"osteoblast|osteocyte|osteogenic|chondrocyte|adipocyte|bone cell|cartilage cell"):
        if text_has(source, r"mesenchymal|msc\b|progenitor|precursor|stem cell|stromal"):
            return "directed_differentiation"

    # Non-stem cell → osteoblast (BMP-2 etc.) = lineage conversion
    if text_has(target, r"osteoblast|osteogenic"):
        if source.strip() and target.strip():
            return "lineage_conversion"

    # Cochlear / vestibular hair cell regeneration (lineage conversion within inner ear)
    if text_has(combined, r"hair cell|cochlear|vestibular|supporting cell|sox2.*atoh|atoh.*sox2|inner ear"):
        if source.strip() and target.strip() and source.strip().lower() != target.strip().lower():
            return "lineage_conversion"

    # Retinal regeneration from Muller glia / RPE / progenitors
    if text_has(combined, r"muller glia|müller glia|retinal pigment|retinal progenitor|retinal ganglion"):
        if source.strip() and target.strip():
            return "lineage_conversion"

    # Spermatogonial / germline conversions
    if text_has(combined, r"spermatogon|germline|germ cell"):
        if source.strip() and target.strip() and source.strip().lower() != target.strip().lower():
            return "lineage_conversion"

    # SVZ neuroblast / brain in vivo conversions
    if text_has(combined, r"svz|subventricular zone|in vivo.*reprogramming|reprogramming.*in vivo"):
        return "lineage_conversion"

    # Immune cell lineage switches (B↔T, NK, DC, macrophage polarization)
    if text_has(combined, r"\bnk cell\b|natural killer|dendritic cell|\bdc\b|innate lymphoid|ilc\b"):
        if source.strip() and target.strip() and source.strip().lower() != target.strip().lower():
            return "lineage_conversion"

    # Common source/target lineage changes even when the title does not name the process.
    if source.strip() and target.strip() and source.strip().lower() != target.strip().lower():
        if text_has(combined, r"\bb cell\b|\bt cell\b|fibroblast|astrocyte|hepatocyte|macrophage|cardiomyocyte|neuron|endothelial|keratinocyte|beta|β|oligodendrocyte|microglia|pericyte|schwann"):
            return "lineage_conversion"

    return "unclear"


def main():
    df = pd.read_csv(FILE, dtype=str).fillna("")

    if "target_cell_raw" not in df.columns:
        df["target_cell_raw"] = df["target_cell"]
    if "source_cell_raw" not in df.columns:
        df["source_cell_raw"] = df["source_cell"]

    before_target = df["target_cell"].nunique()
    df["target_cell"] = df["target_cell"].apply(canonical_target)
    after_target = df["target_cell"].nunique()

    df["conversion_scope"] = df.apply(infer_scope, axis=1)

    shutil.copy(FILE, FILE + ".bak")
    df.to_csv(FILE, index=False, encoding="utf-8")

    print(f"target_cell unique: {before_target} -> {after_target}")
    print("conversion_scope 分布:")
    print(df["conversion_scope"].value_counts().to_string())
    print(f"保存至 {FILE}")


if __name__ == "__main__":
    main()
