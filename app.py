import streamlit as st
import pandas as pd
import re

st.set_page_config(
    page_title="CellReprogramDB",
    page_icon="🧬",
    layout="wide",
)

# ── Load data ─────────────────────────────────────────────────────────────────
@st.cache_data
def load_data():
    df = pd.read_csv("recipes_master_v2.csv", dtype=str).fillna("")
    for col, default in {
        "single_tf_flag": "False",
        "single_tf_status": "",
        "conversion_scope": "unclear",
        "duplicate_reason": "",
        "preferred_pmid": "",
        "duplicate_group_id": "",
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
    # year列已预合并在CSV中
    if "year" in df.columns:
        df["year"] = pd.to_numeric(df["year"], errors="coerce").fillna(0).astype(int)
    else:
        df["year"] = 0
    # Merge journal names
    try:
        journals = pd.read_csv("journals.csv", dtype=str).fillna("")
        df = df.merge(journals, on="pmid", how="left")
        df["journal"] = df["journal"].fillna("")
    except FileNotFoundError:
        df["journal"] = ""
    return df

df = load_data()

SCOPE_LABELS = {
    "classical_reprogramming": "Classical reprogramming",
    "lineage_conversion": "Lineage conversion",
    "directed_differentiation": "Directed differentiation",
    "cell_state_modulation": "Cell-state modulation",
    "unclear": "Unclear",
}

HIDDEN_VALIDATION_ACTIONS = {"remove", "hide_incomplete_recipe", "hide_single_tf"}


def factors_are_unspecified(value: str) -> bool:
    text = str(value).strip().lower()
    return (not text) or text in {"not specified", "unknown", "not specified in text"}


def is_true(value: str) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}

# ── Header ────────────────────────────────────────────────────────────────────
st.title("🧬 CellReprogramDB")
st.markdown(
    "A curated database of cell reprogramming recipes extracted from PubMed literature.  \n"
    f"**{len(df)} recipes** · **{df['pmid'].nunique()} papers** · **1996–2026** "
    "<span style='font-size:0.82em;color:#888;'>(database total)</span>",
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
        st.session_state["sp"]             = []
        st.session_state["conf"]           = []
        st.session_state["pt"]             = []
        st.session_state["scope"]          = []
        st.session_state["journal_search"] = ""
        st.session_state["hide_dupes"]       = False
        st.session_state["hide_no_factors"]  = False
        st.session_state["hide_cocktail_tf"] = False
        st.session_state["hide_validation_rejected"] = False
        st.session_state["hide_needs_review"]        = False
        st.session_state["show_validation_review"]   = False
        st.session_state["year_range"]     = (int(df["year"][df["year"]>0].min()),
                                              int(df["year"].max()))
        st.rerun()
    if col_rst.button("↺ Defaults", use_container_width=True,
                      help="Reset to recommended default filters"):
        st.session_state["search"]         = ""
        st.session_state["target"]         = []
        st.session_state["source"]         = []
        st.session_state["ft"]             = []
        st.session_state["sp"]             = []
        st.session_state["conf"]           = ["high", "medium"]
        st.session_state["pt"]             = ["research"]
        st.session_state["scope"]          = []
        st.session_state["journal_search"] = ""
        st.session_state["hide_dupes"]       = True
        st.session_state["hide_no_factors"]  = True
        st.session_state["hide_cocktail_tf"] = True
        st.session_state["hide_validation_rejected"] = True
        st.session_state["hide_needs_review"]        = True
        st.session_state["show_validation_review"]   = False
        st.session_state["year_range"]     = (int(df["year"][df["year"]>0].min()),
                                              int(df["year"].max()))
        st.rerun()

    st.divider()

    search = st.text_input("Search (cell type, factor, PMID…)",
                           key="search", value=st.session_state.get("search",""))

    all_targets = sorted(df["target_cell"].dropna().unique().tolist())
    target_sel  = st.multiselect("Target cell type", all_targets,
                                  key="target",
                                  default=st.session_state.get("target",[]))

    all_sources = sorted(df["source_cell"].dropna().unique().tolist())
    source_sel  = st.multiselect("Source cell type", all_sources,
                                  key="source",
                                  default=st.session_state.get("source",[]))

    factor_types = ["TF", "small_molecule", "miRNA", "knockdown", "cytokine", "other"]
    ft_sel = st.multiselect("Factor type", factor_types,
                             key="ft",
                             default=st.session_state.get("ft",[]))

    species_opts = ["human", "mouse", "human, mouse", "mouse, human"]
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
    hide_dupes      = st.checkbox("Hide duplicate recipes",
                                   value=st.session_state.get("hide_dupes", True),
                                   key="hide_dupes",
                                   help="Keep only the earliest research paper per unique recipe")
    hide_no_factors = st.checkbox("Hide 'factors not specified'",
                                   value=st.session_state.get("hide_no_factors", True),
                                   key="hide_no_factors",
                                   help="Exclude recipes where no specific factors were identified")
    hide_cocktail_tf = st.checkbox(
        "Hide single-TF cocktail members",
        value=st.session_state.get("hide_cocktail_tf", True),
        key="hide_cocktail_tf",
        help="Hide entries where a single TF is a known member of a larger cocktail (e.g., SOX2 alone in an OSKM study). "
             "Standalone single-TF recipes (NGN2, ASCL1, ETV2, etc.) are always shown.",
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

    with st.expander("Confidence criteria", expanded=False):
        st.markdown(
            "**High**: source cell, target cell, and named factor/cocktail are explicitly supported.  \n"
            "**Medium**: conversion is likely, but source, target, or factors are partial, vague, or need full-text confirmation.  \n"
            "**Low**: recipe is inferred or weakly supported; low-confidence entries are hidden by default."
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
    mask = filtered.apply(lambda row: search.lower() in row.to_string().lower(), axis=1)
    filtered = filtered[mask]

if target_sel:
    filtered = filtered[filtered["target_cell"].isin(target_sel)]

if source_sel:
    filtered = filtered[filtered["source_cell"].isin(source_sel)]

if ft_sel:
    def has_ft(s):
        return any(t.strip() in ft_sel for t in s.split(","))
    filtered = filtered[filtered["factor_type"].apply(has_ft)]

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

if hide_dupes and not show_validation_review and "is_duplicate" in filtered.columns:
    filtered = filtered[filtered["is_duplicate"].astype(str).str.lower() != "true"]

if hide_no_factors and not show_validation_review:
    filtered = filtered[~filtered["factors"].apply(factors_are_unspecified)]

if hide_cocktail_tf and not show_validation_review and "single_tf_status" in filtered.columns:
    # Only hide entries explicitly classified as cocktail members; show standalone and unclear
    filtered = filtered[filtered["single_tf_status"] != "cocktail_member"]
elif "hide_single_tf" in st.session_state:
    # Legacy fallback for old session state key
    pass

if hide_needs_review and not show_validation_review:
    filtered = filtered[filtered["validation_needs_review"].apply(is_true).eq(False)]

if hide_validation_rejected and not show_validation_review:
    rejected = (
        (filtered["validation_action"].astype(str).str.lower().isin(HIDDEN_VALIDATION_ACTIONS)) |
        (filtered["validation_recipe_valid"].astype(str).str.lower() == "no")
    )
    filtered = filtered[~rejected]

if show_validation_review:
    filtered = filtered[filtered["validation_needs_review"].apply(is_true)]

filtered = filtered[
    (filtered["year"] == 0) |
    ((filtered["year"] >= year_range[0]) & (filtered["year"] <= year_range[1]))
]

# ── Stats row ─────────────────────────────────────────────────────────────────
c1, c2, c3, c4 = st.columns(4)
c1.metric("Recipes shown",     len(filtered))
c2.metric("Papers",            filtered["pmid"].nunique())
c3.metric("Target cell types", filtered["target_cell"].nunique())
c4.metric("Source cell types", filtered["source_cell"].nunique())

# Show a note when default filters are active and not all entries are displayed
_defaults_active = (
    st.session_state.get("conf", ["high","medium"]) == ["high","medium"] and
    st.session_state.get("pt",  ["research"])       == ["research"]      and
    st.session_state.get("hide_dupes",       True) and
    st.session_state.get("hide_no_factors",  True) and
    st.session_state.get("hide_cocktail_tf", True)
)
if _defaults_active and len(filtered) < len(df):
    st.caption(
        f"Showing {len(filtered):,} of {len(df):,} total recipes. "
        "Default filters are active (high/medium confidence · research papers · duplicates hidden). "
        "Click **✖ Clear all** in the sidebar to see all entries."
    )

st.divider()

# ── Main table ────────────────────────────────────────────────────────────────
display_cols = [
    "pmid","source_cell","target_cell",
    "factors","factor_type","species","year",
    "journal","confidence","paper_type","conversion_scope","evidence_sentence",
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

st.dataframe(
    display_df,
    use_container_width=True,
    height=540,
    column_config={
        "pmid":              st.column_config.TextColumn("PMID",        width=80),
        "pmid_link":         st.column_config.LinkColumn("↗",           width=40,
                                                          display_text="🔗"),
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
    target_counts = (
        filtered["target_cell"].value_counts().head(15)
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
