import streamlit as st

from src.analysis_workspace import render_analysis

render_analysis(st.session_state._source_snapshot_path)
