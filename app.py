import streamlit as st
import pandas as pd
import re
from pathlib import Path

try:
    from mark_duplicates import split_factors, factor_key
except Exception:
    # Fallback: paren-aware splitter on , and ; only (never on "/").
    def split_factors(value):
        text = str(value or "").strip()
        if not text:
            return []
        parts, buf, depth = [], [], 0
        for ch in text:
            if ch == "(":
                depth += 1
            elif ch == ")" and depth:
                depth -= 1
            if ch in {",", ";"} and depth == 0:
                p = "".join(buf).strip()
                if p:
                    parts.append(p)
                buf = []
            else:
                buf.append(ch)
        p = "".join(buf).strip()
        if p:
            parts.append(p)
        return parts

    def factor_key(value):
        parts = [re.sub(r"[^A-Za-z0-9]+", "", p).upper() for p in split_factors(value)]
        parts = [p for p in parts if p]
        return " | ".join(sorted(parts))

st.set_page_config(
    page_title="CellReprogramDB",
    page_icon="🧬",
    layout="wide",
)

# ── Broad biological cell categories (for coarse filtering) ────────────────────
CELL_CATEGORY_PATTERNS = {
    "immune": r"\bt[\s-]?cell|t lymph|b[\s-]?cell|b lymph|treg|th1|th17|tfh|cd4|cd8|macrophage|monocyte|dendritic|nk cell|natural killer|microglia|mast cell|neutrophil|thymocyte|lymphocyte|myeloid|leukocyte|granulocyte",
    "neural": r"neuron|neural|astrocyte|oligodendrocyte|\bglia|dopaminergic|gabaergic|glutamatergic|motor neuron|photoreceptor|retinal|schwann|neuroblast",
    "hepatic": r"hepatocyte|hepatic|\bliver",
    "cardiac": r"cardiomyocyte|cardiac|myocard",
    "pancreatic": r"pancrea|islet|beta[\s-]?cell|insulin-producing|acinar|ductal",
    "pluripotent/stem": r"pluripotent|ips[c]?\b|embryonic stem|\besc\b|stem cell|totipotent|naive pluripotent",
    "fibroblast": r"fibroblast|\bmef\b",
    "endothelial": r"endothelial|huvec|vascular",
    "hematopoietic": r"hematopoietic|haematopoietic|erythrocyte|megakaryocyte|platelet|blood cell|hspc",
    "epithelial/skin": r"keratinocyte|epitheli|epiderm",
    "muscle": r"myoblast|myocyte|skeletal muscle|myotube|myofibroblast",
}


def _cell_categories(text: str) -> str:
    low = str(text).lower()
    cats = [c for c, pat in CELL_CATEGORY_PATTERNS.items() if re.search(pat, low)]
    return ", ".join(cats)


# ── Load data ─────────────────────────────────────────────────────────────────
DATA_PATH = Path("recipes_master_v2.csv")
JOURNALS_PATH = Path("journals.csv")
FULLTEXT_PATH = Path("fulltext.csv")


def _file_mtime(path: Path) -> float:
    return path.stat().st_mtime if path.exists() else 0.0


@st.cache_data
def load_data(data_mtime: float, journals_mtime: float, fulltext_mtime: float):
    df = pd.read_csv(DATA_PATH, dtype=str).fillna("")
    for col, default in {
        "title": "",
        "source": "",
        "single_tf_flag": "False",
        "single_tf_status": "",
        "conversion_scope": "unclear",
        "duplicate_reason": "",
        "preferred_pmid": "",
        "duplicate_group_id": "",
        "source_cell_broad": "",
        "target_cell_broad": "",
        "is_broad_duplicate": "False",
        "broad_duplicate_reason": "",
        "broad_preferred_pmid": "",
        "broad_duplicate_group_id": "",
        "validation_action": "",
        "validation_recipe_valid": "",
        "validation_error_category": "",
        "validation_needs_review": "False",
        "validation_known_issue": "",
        "validation_notes": "",
        "validation_resolution": "",
    }.items():
        if col not in df.columns:
            df[col] = default
    # Factor count
    def _count_factors(s):
        s = str(s).strip()
        if not s or s.lower() == "not specified":
            return 0
        return len(split_factors(s))
    df["factor_count"] = df["factors"].apply(_count_factors)

    # Evidence/full-text status. This is deliberately conservative: a cached
    # full-text row means local text is available, not that every recipe in that
    # paper has been manually confirmed from full text.
    fulltext_pmids = set()
    try:
        fulltext = pd.read_csv(FULLTEXT_PATH, dtype=str).fillna("")
        methods_text = fulltext["methods_text"] if "methods_text" in fulltext.columns else pd.Series("", index=fulltext.index)
        results_text = fulltext["results_text"] if "results_text" in fulltext.columns else pd.Series("", index=fulltext.index)
        has_text = methods_text.astype(str).str.strip().ne("") | results_text.astype(str).str.strip().ne("")
        fulltext_pmids = set(fulltext.loc[has_text, "pmid"].astype(str))
    except FileNotFoundError:
        pass

    source_label = {
        "abstract": "abstract",
        "fulltext": "full text",
        "manual": "manual",
    }
    df["evidence_source"] = (
        df["source"].astype(str).str.strip().str.lower().map(source_label).fillna(df["source"])
    )
    df["fulltext_status"] = "not in local full-text cache"
    df.loc[df["pmid"].astype(str).isin(fulltext_pmids), "fulltext_status"] = "full text available"
    df.loc[df["source"].astype(str).str.lower().eq("fulltext"), "fulltext_status"] = (
        "recipe extracted from full text"
    )

    def _norm_key_text(value):
        text = str(value or "").strip().lower()
        text = (
            text.replace("β", "beta")
                .replace("α", "alpha")
                .replace("γ", "gamma")
                .replace("δ", "delta")
        )
        text = re.sub(r"[-_/]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _unique_in_order(values):
        seen = set()
        out = []
        for value in values:
            value = str(value).strip()
            if value and value not in seen:
                seen.add(value)
                out.append(value)
        return out

    def _preview(items, limit):
        items = [str(item).strip() for item in items if str(item).strip()]
        if len(items) <= limit:
            return "; ".join(items)
        return "; ".join(items[:limit]) + f"; ... (+{len(items) - limit} more)"

    def _add_support_columns(prefix, source_col, target_col):
        src = df[source_col].where(df[source_col].astype(str).str.strip().ne(""), df["source_cell"])
        tgt = df[target_col].where(df[target_col].astype(str).str.strip().ne(""), df["target_cell"])
        keys = (
            src.apply(_norm_key_text)
            + "\x1f"
            + tgt.apply(_norm_key_text)
            + "\x1f"
            + df["factors"].apply(factor_key).astype(str)
        )
        work = df.assign(_cluster_key=keys)
        count_col = f"{prefix}_supporting_paper_count"
        pmids_col = f"{prefix}_supporting_pmids"
        pmids_preview_col = f"{prefix}_supporting_pmids_preview"
        papers_preview_col = f"{prefix}_supporting_papers_preview"
        df[count_col] = 1
        df[pmids_col] = df["pmid"].astype(str)
        df[pmids_preview_col] = df["pmid"].astype(str)
        df[papers_preview_col] = ""

        year_num = pd.to_numeric(df["year"], errors="coerce").fillna(9999).astype(int)
        for _, group in work.groupby("_cluster_key", sort=False):
            ordered = group.assign(_year_num=year_num.loc[group.index]).sort_values(
                ["_year_num", "pmid"], kind="mergesort"
            )
            pmids = _unique_in_order(ordered["pmid"].tolist())
            first_by_pmid = ordered.drop_duplicates("pmid", keep="first")
            paper_labels = []
            for _, row in first_by_pmid.iterrows():
                pmid = str(row.get("pmid", "")).strip()
                year = str(row.get("year", "")).strip()
                title = str(row.get("title", "")).strip()
                label = pmid
                if year and year != "0":
                    label += f" ({year})"
                if title:
                    label += ": " + title[:140]
                paper_labels.append(label)
            df.loc[group.index, count_col] = len(pmids)
            df.loc[group.index, pmids_col] = ", ".join(pmids)
            df.loc[group.index, pmids_preview_col] = _preview(pmids, 12)
            df.loc[group.index, papers_preview_col] = _preview(paper_labels, 6)

    exact_source_col = "source_cell_std" if "source_cell_std" in df.columns else "source_cell"
    exact_target_col = "target_cell_std" if "target_cell_std" in df.columns else "target_cell"
    broad_source_col = "source_cell_broad" if "source_cell_broad" in df.columns else exact_source_col
    broad_target_col = "target_cell_broad" if "target_cell_broad" in df.columns else exact_target_col
    _add_support_columns("exact", exact_source_col, exact_target_col)
    _add_support_columns("broad", broad_source_col, broad_target_col)

    # Search helper: make punctuation/case variants searchable as one concept.
    # Examples: "T-cell", "T cell", and "Tcell"; "β-cell" and "beta cell".
    def _normalize_search_text(value):
        text = str(value).lower()
        text = (
            text.replace("β", "beta")
                .replace("α", "alpha")
                .replace("γ", "gamma")
                .replace("δ", "delta")
        )
        text = re.sub(r"[-_/]+", " ", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    search_cols = [
        "pmid", "title", "source_cell", "target_cell", "source_cell_std", "target_cell_std",
        "source_cell_broad", "target_cell_broad", "factors", "factor_type", "species",
        "journal", "confidence", "paper_type", "conversion_scope", "evidence_sentence",
        "exact_supporting_pmids", "broad_supporting_pmids",
    ]

    def _make_search_blob(row):
        normalized = " ".join(
            _normalize_search_text(row.get(col, "")) for col in search_cols if col in row.index
        )
        compact = re.sub(r"[^a-z0-9]+", "", normalized)
        return f"{normalized} {compact}"

    df["_search_blob"] = df.apply(_make_search_blob, axis=1)

    # Broad cell category from source + target (a row matches if either side fits)
    _src = df["source_cell_std"] if "source_cell_std" in df.columns else df["source_cell"]
    _tgt = df["target_cell_std"] if "target_cell_std" in df.columns else df["target_cell"]
    df["_cell_cats"] = (_src.astype(str) + " " + _tgt.astype(str)).apply(_cell_categories)

    # year列已预合并在CSV中
    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").fillna(0).astype(int)
    else:
        df["year"] = 0
    # Merge journal names
    try:
        journals = pd.read_csv(JOURNALS_PATH, dtype=str).fillna("")
        df = df.merge(journals, on="pmid", how="left")
        df["journal"] = df["journal"].fillna("")
    except FileNotFoundError:
        df["journal"] = ""
    return df

df = load_data(_file_mtime(DATA_PATH), _file_mtime(JOURNALS_PATH), _file_mtime(FULLTEXT_PATH))

SCOPE_LABELS = {
    "classical_reprogramming": "Classical reprogramming",
    "lineage_conversion": "Lineage conversion",
    "directed_differentiation": "Directed differentiation",
    "cell_state_modulation": "Cell-state modulation",
    "unclear": "Unclear",
}

HIDDEN_VALIDATION_ACTIONS = {"remove", "hide_incomplete_recipe", "hide_single_tf", "hide_model_adjudicated"}

DEDUP_MODE_LABELS = {
    "broad": "Broad cell-type merge",
    "exact": "Exact recipe match",
}


def factors_are_unspecified(value: str) -> bool:
    text = str(value).strip().lower()
    return (not text) or text in {"not specified", "unknown", "not specified in text"}


def is_true(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


exact_dedup_df = df[df["is_duplicate"].astype(str).str.lower() != "true"]
broad_dedup_df = df[df["is_broad_duplicate"].astype(str).str.lower() != "true"]
year_values = df["year"][df["year"] > 0]
year_label = f"{int(year_values.min())}–{int(year_values.max())}" if len(year_values) else "year unknown"

# ── Header ────────────────────────────────────────────────────────────────────
st.title("🧬 CellReprogramDB")
st.markdown(
    "A curated database of cell reprogramming recipes extracted from PubMed literature.  \n"
    f"**{len(broad_dedup_df):,} broad-merged recipes** · "
    f"**{len(exact_dedup_df):,} exact recipes** · "
    f"**{len(df):,} raw records** · **{year_label}** "
    "<span style='font-size:0.82em;color:#888;'>(duplicates are flagged, not deleted)</span>",
    unsafe_allow_html=True,
)
st.divider()

# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔍 Filter")

    # Clear all button
    col_clr, col_rst = st.columns(2)
    if col_clr.button("✖ Clear all", use_container_width=True,
                      help="Remove all filters — show the full database"):
        st.session_state["search"]         = ""
        st.session_state["target"]         = []
        st.session_state["source"]         = []
        st.session_state["ft"]             = []
        st.session_state["factor_class"]   = "All"
        st.session_state["cell_cat"]       = []
        st.session_state["sp"]             = []
        st.session_state["conf"]           = []
        st.session_state["pt"]             = []
        st.session_state["scope"]          = []
        st.session_state["journal_search"] = ""
        st.session_state["record_mode"]      = "All paper records"
        st.session_state["dedup_mode"]       = "exact"
        st.session_state["hide_no_factors"]  = False
        st.session_state["hide_cocktail_tf"] = False
        st.session_state["hide_validation_rejected"] = False
        st.session_state["hide_needs_review"]        = False
        st.session_state["show_validation_review"]   = False
        st.session_state["single_tf_status_filter"]  = []
        st.session_state["evidence_source_filter"]   = []
        st.session_state["fulltext_status_filter"]   = []
        st.session_state["factor_count_range"] = (1, int(df["factor_count"].max()))
        st.session_state["year_range"]     = (int(df["year"][df["year"]>0].min()),
                                              int(df["year"].max()))
        st.rerun()
    if col_rst.button("↺ Defaults", use_container_width=True,
                      help="Reset to recommended default filters"):
        st.session_state["search"]         = ""
        st.session_state["target"]         = []
        st.session_state["source"]         = []
        st.session_state["ft"]             = []
        st.session_state["factor_class"]   = "All"
        st.session_state["cell_cat"]       = []
        st.session_state["sp"]             = []
        st.session_state["conf"]           = ["high", "medium"]
        st.session_state["pt"]             = ["research"]
        st.session_state["scope"]          = []
        st.session_state["journal_search"] = ""
        st.session_state["record_mode"]      = "Unique recipes"
        st.session_state["dedup_mode"]       = "broad"
        st.session_state["hide_no_factors"]  = True
        st.session_state["hide_cocktail_tf"] = True
        st.session_state["hide_validation_rejected"] = True
        st.session_state["hide_needs_review"]        = True
        st.session_state["show_validation_review"]   = False
        st.session_state["single_tf_status_filter"]  = []
        st.session_state["evidence_source_filter"]   = []
        st.session_state["fulltext_status_filter"]   = []
        st.session_state["factor_count_range"] = (1, int(df["factor_count"].max()))
        st.session_state["year_range"]     = (int(df["year"][df["year"]>0].min()),
                                              int(df["year"].max()))
        st.rerun()

    st.divider()

    search = st.text_input("Search (cell type, factor, PMID…)",
                           key="search", value=st.session_state.get("search",""))

    _tgt_col = "target_cell_std" if "target_cell_std" in df.columns else "target_cell"
    _src_col = "source_cell_std" if "source_cell_std" in df.columns else "source_cell"
    all_targets = sorted(df[_tgt_col].dropna().unique().tolist())
    target_sel  = st.multiselect("Target cell type", all_targets,
                                  key="target",
                                  default=st.session_state.get("target",[]))

    all_sources = sorted(df[_src_col].dropna().unique().tolist())
    source_sel  = st.multiselect("Source cell type", all_sources,
                                  key="source",
                                  default=st.session_state.get("source",[]))

    factor_types = ["TF", "small_molecule", "miRNA", "knockdown", "cytokine", "other"]
    ft_sel = st.multiselect("Factor type", factor_types,
                             key="ft",
                             default=st.session_state.get("ft",[]))

    factor_class = st.radio(
        "Factor class",
        ["All", "Contains a TF", "Chemical (small molecule)", "Non-TF only"],
        key="factor_class",
        index=["All", "Contains a TF", "Chemical (small molecule)", "Non-TF only"].index(
            st.session_state.get("factor_class", "All")),
        help="Quick view of chemical reprogramming and non-TF recipes. 'Non-TF only' = no transcription "
             "factor at all (small molecules, cytokines, miRNAs, knockdowns).",
    )

    cell_cat_opts = sorted(CELL_CATEGORY_PATTERNS.keys())
    cell_cat_sel = st.multiselect(
        "Cell category (broad)", cell_cat_opts,
        key="cell_cat",
        default=st.session_state.get("cell_cat", []),
        help="Coarse biological grouping by source or target cell — immune, neural, hepatic, cardiac, "
             "pancreatic, etc. — instead of the fine-grained cell name.",
    )

    preferred_species = ["human", "mouse", "human, mouse", "rat", "porcine", "bovine", "zebrafish"]
    observed_species = [
        s.strip()
        for s in df["species"].dropna().astype(str).unique().tolist()
        if s.strip()
    ]
    species_opts = [s for s in preferred_species if s in observed_species]
    species_opts.extend(sorted(s for s in observed_species if s not in species_opts))
    sp_sel = st.multiselect("Species", species_opts,
                             key="sp",
                             default=st.session_state.get("sp",[]))

    conf_sel = st.multiselect("Confidence", ["high", "medium", "low"],
                               key="conf",
                               default=st.session_state.get("conf",["high","medium"]))

    pt_sel = st.multiselect("Paper type", ["research", "review", "other"],
                             key="pt",
                             default=st.session_state.get("pt",["research"]))

    scope_opts = [s for s in SCOPE_LABELS if s in set(df["conversion_scope"])]
    scope_sel = st.multiselect(
        "Conversion scope",
        scope_opts,
        format_func=lambda s: SCOPE_LABELS.get(s, s),
        key="scope",
        default=st.session_state.get("scope", []),
    )

    journal_search = st.text_input("Journal (keyword)",
                                   key="journal_search",
                                   value=st.session_state.get("journal_search", ""))

    st.divider()
    record_modes = ["Unique recipes", "All paper records"]
    record_mode_default = st.session_state.get("record_mode", "Unique recipes")
    if record_mode_default not in record_modes:
        record_mode_default = "Unique recipes"
    record_mode = st.radio(
        "Record display",
        record_modes,
        key="record_mode",
        index=record_modes.index(record_mode_default),
        help="Unique recipes keeps one representative row per recipe cluster and lists supporting PMIDs. "
             "All paper records shows every extracted paper row, including duplicate recipes.",
    )
    hide_dupes = record_mode == "Unique recipes"
    dedup_mode = st.selectbox(
        "Deduplication mode",
        ["broad", "exact"],
        format_func=lambda mode: DEDUP_MODE_LABELS.get(mode, mode),
        key="dedup_mode",
        index=["broad", "exact"].index(st.session_state.get("dedup_mode", "broad")),
        help="Broad mode merges high-frequency cell-type variants such as fibroblast subtypes, iPSC variants, "
             "and generic neuron-like targets. Exact mode only merges rows with the same standardized source, "
             "target, and factor set.",
        disabled=not hide_dupes,
    )
    hide_no_factors = st.checkbox("Hide 'factors not specified'",
                                   value=st.session_state.get("hide_no_factors", True),
                                   key="hide_no_factors",
                                   help="Exclude recipes where no specific factors were identified")
    hide_cocktail_tf = st.checkbox(
        "Hide unverified single-TF entries",
        value=st.session_state.get("hide_cocktail_tf", True),
        key="hide_cocktail_tf",
        help="Hide single-TF entries that are either known cocktail members (e.g., SOX2 alone in an OSKM study) "
             "or could not be verified as standalone conversions without full-text review. "
             "Well-established standalone single-TF recipes (NGN2, ASCL1, MYOD1, ETV2, GATA1, etc.) are always shown.",
    )

    with st.expander("Validation QA", expanded=False):
        hide_validation_rejected = st.checkbox(
            "Hide validation-rejected entries",
            value=st.session_state.get("hide_validation_rejected", True),
            key="hide_validation_rejected",
            help="Hide rows manually rejected or marked incomplete after v3 validation/known-issue QA",
        )
        hide_needs_review = st.checkbox(
            "Hide unverified evidence entries",
            value=st.session_state.get("hide_needs_review", True),
            key="hide_needs_review",
            help="Hide entries whose evidence sentence was flagged as a methods description or prior-work citation. "
                 "The recipe may be valid but hasn't been independently confirmed.",
        )
        show_validation_review = st.checkbox(
            "Show validation needs-review only",
            value=st.session_state.get("show_validation_review", False),
            key="show_validation_review",
            help="Show all rows flagged for follow-up, including remove/no rows",
        )
        single_tf_status_filter = st.multiselect(
            "Single-TF status",
            ["standalone_valid", "unclear", "cocktail_member"],
            key="single_tf_status_filter",
            default=st.session_state.get("single_tf_status_filter", []),
            help="Inspect single-transcription-factor entries by curation status",
        )
        evidence_source_opts = sorted(s for s in df["evidence_source"].unique().tolist() if str(s).strip())
        evidence_source_filter = st.multiselect(
            "Evidence source",
            evidence_source_opts,
            key="evidence_source_filter",
            default=st.session_state.get("evidence_source_filter", []),
            help="Where the extracted recipe/evidence came from: abstract, full text, or manual curation.",
        )
        fulltext_status_opts = [
            s for s in [
                "recipe extracted from full text",
                "full text available",
                "not in local full-text cache",
            ] if s in set(df["fulltext_status"])
        ]
        fulltext_status_filter = st.multiselect(
            "Full-text status",
            fulltext_status_opts,
            key="fulltext_status_filter",
            default=st.session_state.get("fulltext_status_filter", []),
            help="Local full-text cache status. 'Full text available' does not mean the row has been manually checked.",
        )

    with st.expander("Recipe / confidence criteria", expanded=False):
        st.markdown(
            "**Included recipe**: the cited paper reports a successful source-cell to target-cell "
            "conversion with named factor(s), chemicals, cytokines, miRNAs, or perturbations.  \n\n"
            "**Excluded / flagged**: prior-work citations, failed attempts, methods-only setup, "
            "review-only claims, missing factor names, or evidence that does not support actual conversion.  \n\n"
            "**High**: source cell, target cell, and named factor/cocktail are explicitly supported "
            "by the evidence text.  \n"
            "**Medium**: conversion is likely, but source, target, factors, or evidence provenance "
            "need fuller confirmation.  \n"
            "**Low**: recipe is inferred or weakly supported; low-confidence entries are hidden by default."
        )

    # Factor count range
    max_fc = int(df["factor_count"].max()) if len(df) else 10
    default_fc = st.session_state.get("factor_count_range", (1, max_fc))
    factor_count_range = st.slider(
        "Number of factors", 1, max_fc,
        value=default_fc, key="factor_count_range",
        help="Filter by how many factors are in a recipe. "
             "Single-factor entries that are well-established (NGN2, ASCL1, etc.) are included at count=1.",
    )

    # Year range
    year_vals = df["year"][df["year"] > 0]
    min_year  = int(year_vals.min()) if len(year_vals) else 2000
    max_year  = int(year_vals.max()) if len(year_vals) else 2026
    default_yr = st.session_state.get("year_range", (min_year, max_year))
    year_range = st.slider("Publication year", min_year, max_year,
                            value=default_yr, key="year_range")

    st.divider()
    st.markdown("Pre-release v0.9")

# ── Apply filters ─────────────────────────────────────────────────────────────
filtered = df.copy()

if search:
    def _normalize_query(value):
        text = str(value).lower()
        text = (
            text.replace("β", "beta")
                .replace("α", "alpha")
                .replace("γ", "gamma")
                .replace("δ", "delta")
        )
        text = re.sub(r"[-_/]+", " ", text)
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    query = _normalize_query(search)
    if query:
        # Word-boundary match so "T cell" / "T-cell" hit genuine T cells, not
        # substrings inside words like "pluripoten[t cell]s" or "fibroblas[t cell]".
        mask = filtered["_search_blob"].str.contains(
            r"\b" + re.escape(query) + r"\b", na=False, regex=True
        )
        filtered = filtered[mask]

if target_sel:
    filtered = filtered[filtered[_tgt_col].isin(target_sel)]

if source_sel:
    filtered = filtered[filtered[_src_col].isin(source_sel)]

if ft_sel:
    def has_ft(s):
        return any(t.strip() in ft_sel for t in s.split(","))
    filtered = filtered[filtered["factor_type"].apply(has_ft)]

if factor_class and factor_class != "All":
    def _ftypes(s):
        return {t.strip().lower() for t in str(s).split(",") if t.strip()}
    if factor_class == "Contains a TF":
        filtered = filtered[filtered["factor_type"].apply(lambda s: any("tf" in t for t in _ftypes(s)))]
    elif factor_class == "Chemical (small molecule)":
        filtered = filtered[filtered["factor_type"].apply(lambda s: any("small" in t for t in _ftypes(s)))]
    elif factor_class == "Non-TF only":
        filtered = filtered[filtered["factor_type"].apply(lambda s: bool(_ftypes(s)) and not any("tf" in t for t in _ftypes(s)))]

if cell_cat_sel:
    filtered = filtered[filtered["_cell_cats"].apply(
        lambda c: any(cat in [x.strip() for x in str(c).split(",")] for cat in cell_cat_sel))]

if sp_sel:
    filtered = filtered[filtered["species"].isin(sp_sel)]

if conf_sel:
    filtered = filtered[filtered["confidence"].isin(conf_sel)]

if pt_sel:
    filtered = filtered[filtered["paper_type"].isin(pt_sel)]

if scope_sel:
    filtered = filtered[filtered["conversion_scope"].isin(scope_sel)]

if journal_search:
    filtered = filtered[filtered["journal"].str.contains(journal_search, case=False, na=False)]

if evidence_source_filter:
    filtered = filtered[filtered["evidence_source"].isin(evidence_source_filter)]

if fulltext_status_filter:
    filtered = filtered[filtered["fulltext_status"].isin(fulltext_status_filter)]

if hide_dupes and not show_validation_review:
    duplicate_col = "is_broad_duplicate" if dedup_mode == "broad" else "is_duplicate"
    if duplicate_col in filtered.columns:
        filtered = filtered[filtered[duplicate_col].astype(str).str.lower() != "true"]

if hide_no_factors and not show_validation_review:
    filtered = filtered[~filtered["factors"].apply(factors_are_unspecified)]

if single_tf_status_filter and "single_tf_status" in filtered.columns:
    filtered = filtered[filtered["single_tf_status"].isin(single_tf_status_filter)]
elif hide_cocktail_tf and not show_validation_review and "single_tf_status" in filtered.columns:
    # Hide cocktail members and unverified unclear entries; always show standalone_valid
    filtered = filtered[~filtered["single_tf_status"].isin(["cocktail_member", "unclear"])]

if hide_needs_review and not show_validation_review and "validation_needs_review" in filtered.columns:
    # Treat blank as "not flagged"; only rows explicitly set to True are hidden.
    filtered = filtered[~filtered["validation_needs_review"].apply(is_true)]

if hide_validation_rejected and not show_validation_review:
    rejected_mask = pd.Series(False, index=filtered.index)
    if "validation_action" in filtered.columns:
        rejected_mask |= filtered["validation_action"].astype(str).str.lower().isin(HIDDEN_VALIDATION_ACTIONS)
    if "validation_recipe_valid" in filtered.columns:
        rejected_mask |= filtered["validation_recipe_valid"].astype(str).str.lower() == "no"
    filtered = filtered[~rejected_mask]

if show_validation_review and "validation_needs_review" in filtered.columns:
    filtered = filtered[filtered["validation_needs_review"].apply(is_true)]

fc_min, fc_max = factor_count_range
if (fc_min, fc_max) != (1, int(df["factor_count"].max())):
    filtered = filtered[
        (filtered["factor_count"] >= fc_min) & (filtered["factor_count"] <= fc_max)
    ]

filtered = filtered[
    (filtered["year"] == 0) |
    ((filtered["year"] >= year_range[0]) & (filtered["year"] <= year_range[1]))
]

# ── Stats row ─────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
_metric_tgt_col = "target_cell_broad" if hide_dupes and dedup_mode == "broad" and "target_cell_broad" in filtered.columns else _tgt_col
_metric_src_col = "source_cell_broad" if hide_dupes and dedup_mode == "broad" and "source_cell_broad" in filtered.columns else _src_col
_support_prefix = "broad" if dedup_mode == "broad" else "exact"
_support_pmids_col = f"{_support_prefix}_supporting_pmids"
if hide_dupes and _support_pmids_col in filtered.columns:
    _paper_set = set()
    for values in filtered[_support_pmids_col]:
        _paper_set.update(p.strip() for p in str(values).split(",") if p.strip())
    papers_metric = len(_paper_set)
else:
    papers_metric = filtered["pmid"].nunique()
c1.metric("Recipes shown",     len(filtered))
c2.metric("Papers represented", papers_metric)
c3.metric("Target cell types", filtered[_metric_tgt_col].nunique())
c4.metric("Source cell types", filtered[_metric_src_col].nunique())

# Show a note when default filters are active and not all entries are displayed
_defaults_active = (
    st.session_state.get("conf", ["high","medium"]) == ["high","medium"] and
    st.session_state.get("pt",  ["research"])       == ["research"]      and
    st.session_state.get("record_mode", "Unique recipes") == "Unique recipes" and
    st.session_state.get("dedup_mode", "broad") == "broad" and
    st.session_state.get("hide_no_factors",  True) and
    st.session_state.get("hide_cocktail_tf", True) and
    not st.session_state.get("single_tf_status_filter", [])
)
if _defaults_active and len(filtered) < len(df):
    st.caption(
        f"Showing {len(filtered):,} of {len(df):,} total recipes. "
        "Default filters are active (high/medium confidence · research papers · "
        "unique broad recipe clusters · specified factors · validated single-TF entries). "
        "Click **✖ Clear all** in the sidebar to see all entries."
    )

if hide_dupes:
    st.caption(
        "Unique recipe view: each visible row is the representative record for a recipe cluster; "
        "supporting PMIDs list other papers with the same recipe under the selected deduplication mode."
    )
else:
    st.caption("All paper records view: duplicate recipe rows are shown instead of clustered.")

st.divider()

# ── Main table ────────────────────────────────────────────────────────────────
support_count_col = f"{_support_prefix}_supporting_paper_count"
support_pmids_preview_col = f"{_support_prefix}_supporting_pmids_preview"
support_papers_preview_col = f"{_support_prefix}_supporting_papers_preview"
display_cols = [
    "pmid","title",
    support_count_col, support_pmids_preview_col,
    "source_cell","target_cell",
    "factors","factor_type","species","year",
    "journal","confidence","paper_type","conversion_scope",
    "evidence_source","fulltext_status","evidence_sentence",
    support_papers_preview_col,
]
if show_validation_review:
    display_cols.extend([
        "validation_action",
        "validation_resolution",
        "validation_error_category",
        "validation_known_issue",
        "validation_notes",
    ])

display_df = filtered[[c for c in display_cols if c in filtered.columns]].copy()
display_df["conversion_scope"] = display_df["conversion_scope"].map(
    lambda s: SCOPE_LABELS.get(s, s)
)

# PMID → clickable number only
display_df["pmid_link"] = display_df["pmid"].apply(
    lambda p: f"https://pubmed.ncbi.nlm.nih.gov/{p}"
)
_cols = display_df.columns.tolist()
if "pmid" in _cols and "pmid_link" in _cols:
    _cols.remove("pmid_link")
    _cols.insert(_cols.index("pmid") + 1, "pmid_link")
    display_df = display_df[_cols]

st.dataframe(
    display_df,
    use_container_width=True,
    height=540,
    column_config={
        "pmid":              st.column_config.TextColumn("PMID",        width=80),
        "pmid_link":         st.column_config.LinkColumn("↗",           width=40,
                                                          display_text="🔗"),
        "title":             st.column_config.TextColumn("Title",       width=300),
        support_count_col:    st.column_config.NumberColumn("Supporting papers", width=120, format="%d"),
        support_pmids_preview_col: st.column_config.TextColumn("Supporting PMIDs", width=260),
        support_papers_preview_col: st.column_config.TextColumn("Supporting papers preview", width=420),
        "source_cell":       st.column_config.TextColumn("Source cell", width=170),
        "target_cell":       st.column_config.TextColumn("Target cell", width=170),
        "factors":           st.column_config.TextColumn("Factors",     width=230),
        "factor_type":       st.column_config.TextColumn("Type",        width=130),
        "species":           st.column_config.TextColumn("Species",     width=80),
        "year":              st.column_config.NumberColumn("Year",      width=65,
                                                            format="%d"),
        "journal":           st.column_config.TextColumn("Journal",     width=200),
        "confidence":        st.column_config.TextColumn("Conf.",       width=70),
        "paper_type":        st.column_config.TextColumn("Paper",       width=75),
        "conversion_scope":   st.column_config.TextColumn("Scope",       width=150),
        "evidence_source":    st.column_config.TextColumn("Evidence source", width=95),
        "fulltext_status":    st.column_config.TextColumn("Full-text status", width=170),
        "evidence_sentence": st.column_config.TextColumn("Evidence sentence", width=380),
        "validation_action": st.column_config.TextColumn("Validation action", width=120),
        "validation_resolution": st.column_config.TextColumn("Validation resolution", width=180),
        "validation_error_category": st.column_config.TextColumn("Validation error", width=180),
        "validation_known_issue": st.column_config.TextColumn("Known issue", width=220),
        "validation_notes": st.column_config.TextColumn("Validation notes", width=320),
    },
    hide_index=True,
)

# ── Download ──────────────────────────────────────────────────────────────────
st.download_button(
    label="⬇️ Download filtered CSV",
    data=filtered.to_csv(index=False).encode("utf-8"),
    file_name="CellReprogramDB_filtered.csv",
    mime="text/csv",
)

# ── Charts ────────────────────────────────────────────────────────────────────
st.divider()
st.subheader("📊 Overview")

chart_col1, chart_col2 = st.columns(2)

with chart_col1:
    st.markdown("**Top 15 target cell types**")
    _chart_tgt_col = "target_cell_std" if "target_cell_std" in filtered.columns else "target_cell"
    target_counts = (
        filtered[_chart_tgt_col].value_counts().head(15)
        .reset_index()
    )
    target_counts.columns = ["Target cell type", "Count"]
    # Truncate long names
    target_counts["Target cell type"] = target_counts["Target cell type"].str[:28]
    st.bar_chart(target_counts.set_index("Target cell type"), height=320)

with chart_col2:
    st.markdown("**Factor type distribution**")
    ft_counts = {}
    for row in filtered["factor_type"]:
        for ft in row.split(","):
            ft = ft.strip()
            if ft: ft_counts[ft] = ft_counts.get(ft, 0) + 1
    ft_df = pd.DataFrame(
        sorted(ft_counts.items(), key=lambda x: -x[1]),
        columns=["Factor type", "Count"]
    )
    st.bar_chart(ft_df.set_index("Factor type"), height=320)

# Year trend
if "year" in filtered.columns:
    st.markdown("**Recipes by year**")
    year_data = filtered[filtered["year"] > 0]["year"].value_counts().sort_index()
    if not year_data.empty:
        # Fill gaps so the x-axis is evenly spaced
        full_idx   = range(int(year_data.index.min()), int(year_data.index.max()) + 1)
        year_counts = (
            year_data.reindex(full_idx, fill_value=0)
            .reset_index()
        )
        year_counts.columns = ["Year", "Recipes"]
    else:
        year_counts = pd.DataFrame(columns=["Year", "Recipes"])
    st.bar_chart(year_counts.set_index("Year"), height=220)
