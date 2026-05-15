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

# ── Header ────────────────────────────────────────────────────────────────────
st.title("🧬 CellReprogramDB")
st.markdown(
    "A curated database of cell reprogramming recipes extracted from PubMed literature.  \n"
    f"**{len(df)} recipes** · **{df['pmid'].nunique()} papers** · **1996–2026**"
)
st.divider()

# ── Sidebar filters ───────────────────────────────────────────────────────────
with st.sidebar:
    st.header("🔍 Filter")

    # Clear all button
    if st.button("✖ Clear all filters", use_container_width=True):
        st.session_state["search"]         = ""
        st.session_state["target"]         = []
        st.session_state["source"]         = []
        st.session_state["ft"]             = []
        st.session_state["sp"]             = []
        st.session_state["conf"]           = ["high", "medium"]
        st.session_state["pt"]             = ["research"]
        st.session_state["journal_search"] = ""
        st.session_state["hide_dupes"]     = True
        st.session_state["hide_no_factors"]= True
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

    # Year range
    year_vals = df["year"][df["year"] > 0]
    min_year  = int(year_vals.min()) if len(year_vals) else 2000
    max_year  = int(year_vals.max()) if len(year_vals) else 2026
    default_yr = st.session_state.get("year_range", (min_year, max_year))
    year_range = st.slider("Publication year", min_year, max_year,
                            value=default_yr, key="year_range")

    st.divider()
    st.markdown("**Wang Lab · UCSD**  \nPre-release v0.6")

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

if journal_search:
    filtered = filtered[filtered["journal"].str.contains(journal_search, case=False, na=False)]

if hide_dupes and "is_duplicate" in filtered.columns:
    filtered = filtered[filtered["is_duplicate"].astype(str).str.lower() != "true"]

if hide_no_factors:
    filtered = filtered[filtered["factors"] != "not specified"]

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

st.divider()

# ── Main table ────────────────────────────────────────────────────────────────
display_df = filtered[[
    "pmid","source_cell","target_cell",
    "factors","factor_type","species","year",
    "journal","confidence","paper_type","evidence_sentence",
]].copy()

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
        "evidence_sentence": st.column_config.TextColumn("Evidence sentence", width=380),
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
    st.markdown("**Papers by year**")
    year_counts = (
        filtered[filtered["year"] > 0]["year"]
        .value_counts().sort_index()
        .reset_index()
    )
    year_counts.columns = ["Year", "Recipes"]
    st.bar_chart(year_counts.set_index("Year"), height=220)
