"""Streamlit front end for the RFQ parser.

Run:   pip install -r requirements-app.txt
       streamlit run app.py

It only takes a message and shows what parse_rfq() returns. All the parsing logic
lives in parse.py; this file does not change it.
"""
import json

import streamlit as st

from parse import parse_rfq

EXAMPLES = [
    "bopp bags chahiye 40 hazaar, 65 gsm, Nagpur",
    "jumbo bag 1000 kg capacity, 500 pcs, Vadodara",
    "need quote for 500 kg shrink wrap 25 mic",
]

st.set_page_config(page_title="RFQ Parser", page_icon="📦", layout="centered")
st.title("RFQ Parser")
st.caption("Type a buyer's packaging requirement. The parser extracts six fields.")

if "text" not in st.session_state:
    st.session_state.text = ""
    st.session_state.submitted = None   # the message that was last parsed

st.write("Try an example:")
for col, example in zip(st.columns(len(EXAMPLES)), EXAMPLES):
    if col.button(example, use_container_width=True):
        st.session_state.text = example        # fill the box
        st.session_state.submitted = None      # wait for Enter

# the parser runs only when the user presses Enter (the button below)
with st.form("rfq"):
    text = st.text_area("Buyer message", key="text", height=100,
                        placeholder="e.g. woven sack 2.5 lac, 90 gsm, delivery Indore")
    if st.form_submit_button("Enter", type="primary"):
        st.session_state.submitted = text

submitted = st.session_state.submitted
if submitted is not None and not submitted.strip():
    st.warning("Please type a message first.")
elif submitted:
    st.caption(f"Parsed: {submitted}")
    result = parse_rfq(submitted)

    def show(value):
        return "—" if value is None else str(value)

    row1 = st.columns(3)
    row1[0].metric("Category", show(result["category"]))
    row1[1].metric("Quantity", show(result["quantity"]))
    row1[2].metric("Unit", show(result["unit"]))
    row2 = st.columns(3)
    row2[0].metric("GSM", show(result["gsm"]))
    row2[1].metric("Micron", show(result["micron"]))
    row2[2].metric("Location", show(result["location"]))

    st.subheader("Output of parse_rfq()")
    st.code(json.dumps(result, indent=2, ensure_ascii=False), language="json")
else:
    st.info("Type a message and press Enter to see the parsed fields.")
