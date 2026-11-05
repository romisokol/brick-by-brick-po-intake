import base64
import pandas as pd
import streamlit as st
import altair as alt
from database import load_lines, source_files
from logic import orders_from_lines, select_order

st.set_page_config(page_title='Solenne | Order review', page_icon='◈', layout='wide')
st.markdown('''<style>
.stApp{background:#f5f7fa} .block-container{padding-top:2rem;max-width:1500px}
h1,h2,h3{color:#18334b} [data-testid="stMetric"]{background:white;padding:18px;border-radius:10px;border:1px solid #e3e9ef}
[data-testid="stSidebar"]{background:#eaf0f5}
</style>''', unsafe_allow_html=True)
COLORS = {'READY':'#5B9476', 'REVIEW':'#C49A45', 'FAILED':'#BE6670'}
st.sidebar.title('MAISON SOLENNE')
st.sidebar.caption('ORDER OPERATIONS')
page = st.sidebar.radio('Workspace', ['Overview', 'Review queue', 'Order review', 'Deployment notes'])
st.sidebar.caption('Read-only first release · synthetic data')
if st.sidebar.button('Refresh from Gold'):
    st.session_state.pop('gold_lines', None)
    st.rerun()

st.title(page)
if page == 'Deployment notes':
    st.info('This release reads Gold and source documents. Persistent editing, approvals, audit, and ERP export are not enabled yet.')
    st.markdown('Use the app README for deployment and permissions. Do not grant MODIFY on Gold or pipeline tables. Region-specific access controls must be configured before sharing beyond the team.')
    st.stop()

try:
    if 'gold_lines' not in st.session_state:
        with st.spinner('Loading proposed orders…'):
            st.session_state.gold_lines = load_lines()
    lines = st.session_state.gold_lines.copy()
    required = ['source_doc_id','po_number','source_order_index','needs_review','quality_status','quantity_each','expected_net_price','overall_confidence','master_customer_name','channel','currency']
    missing = sorted(set(required) - set(lines))
    if missing:
        raise ValueError('Gold schema differs from the supplied SQL. Missing: ' + ', '.join(missing))
except Exception as exc:
    st.error('Could not load live Gold data. No sample data has been substituted.')
    st.code(str(exc))
    st.info('Attach the sql-warehouse resource and grant the app identity SELECT on Gold, with USE CATALOG / USE SCHEMA. See README.md.')
    st.stop()
if lines.empty:
    st.info('Gold currently contains no order lines.')
    st.stop()

channels = st.sidebar.multiselect('Channels', sorted(lines.channel.dropna().unique()))
if channels:
    lines = lines[lines.channel.isin(channels)]
if lines.empty:
    st.info('No lines match these filters.')
    st.stop()
orders = orders_from_lines(lines)
st.caption('Proposed sales orders · quantities in eaches · values kept in their original currencies')

if page == 'Overview':
    cols = st.columns(4)
    cols[0].metric('Proposed orders', f'{len(orders):,}')
    cols[1].metric('Order lines', f'{len(lines):,}')
    cols[2].metric('Orders requiring attention', f'{orders.status.ne("READY").sum():,}')
    cols[3].metric('Ready order share', f'{orders.status.eq("READY").mean():.1%}')
    st.caption('Ready share is a routing metric, not measured straight-through processing or accuracy.')
    left, right = st.columns(2)
    with left:
        st.subheader('Line quality')
        counts = lines.groupby('quality_status').size().reset_index(name='lines')
        st.altair_chart(alt.Chart(counts).mark_arc(innerRadius=65).encode(theta='lines:Q', color=alt.Color('quality_status:N', scale=alt.Scale(domain=list(COLORS), range=list(COLORS.values()))), tooltip=['quality_status','lines']).properties(height=300), use_container_width=True)
    with right:
        st.subheader('Lines by channel')
        counts = lines.groupby('channel').size().reset_index(name='lines')
        st.altair_chart(alt.Chart(counts).mark_bar(color='#527FA3').encode(x='lines:Q',y=alt.Y('channel:N',sort='-x'),tooltip=['channel','lines']).properties(height=300), use_container_width=True)
    left, right = st.columns(2)
    with left:
        st.subheader('Reasons for review')
        reasons = lines.loc[lines.needs_review.fillna(True), 'review_reasons'].dropna().str.split('; ').explode()
        counts = reasons[reasons.ne('')].value_counts().head(8).rename_axis('reason').reset_index(name='lines')
        st.altair_chart(alt.Chart(counts).mark_bar(color='#527FA3').encode(x='lines:Q', y=alt.Y('reason:N',sort='-x')).properties(height=300),use_container_width=True)
    with right:
        st.subheader('Inventory proposals')
        counts = lines.groupby('proposed_atp_outcome').size().reset_index(name='lines')
        st.altair_chart(alt.Chart(counts).mark_bar(color='#527FA3').encode(x='proposed_atp_outcome:N', y='lines:Q',tooltip=['proposed_atp_outcome','lines']).properties(height=300),use_container_width=True)
    st.info('Extraction gaps remain upstream. Order readiness does not prove completeness; evaluate accuracy separately against ground truth.')

elif page == 'Review queue':
    st.write('Unknown or lowest confidence first; then currency and highest known value. Partial values are labeled by priced-line coverage.')
    statuses = st.multiselect('Order status', ['FAILED','REVIEW','READY'], default=['FAILED','REVIEW'])
    queue = orders[orders.status.isin(statuses)].copy()
    search = st.text_input('Search PO, document, or customer')
    if search:
        mask = queue[['source_doc_id','po_number','customer']].fillna('').astype(str).apply(lambda x: x.str.contains(search,case=False,regex=False)).any(axis=1)
        queue = queue[mask]
    st.dataframe(queue, hide_index=True, use_container_width=True,
        column_config={'confidence': st.column_config.NumberColumn(format='%.2f'), 'known_value':st.column_config.NumberColumn('Known value (original currency)',format='%.2f')})
    st.caption('Open Order review in the sidebar to inspect an order. Nothing in this screen changes source data.')

else:
    labels = [f'{r.source_doc_id} · {r.po_number or "Missing PO"} · order {r.source_order_index} · {r.status}' for r in orders.itertuples()]
    index = st.selectbox('Choose an order', range(len(orders)), format_func=lambda i: labels[i])
    order = orders.iloc[index]
    selected = select_order(lines, order)
    left, right = st.columns([1, 1.3])
    with left:
        st.subheader('Source evidence')
        # Attachment lineage resolves to the parent Bronze document without discarding the source ID.
        parent = selected.iloc[0].get('doc_id')
        try:
            files = source_files(parent if pd.notna(parent) else order.source_doc_id)
            if files.empty:
                st.warning('No original document was found for this order.')
            else:
                file_index = st.selectbox('Original file', range(len(files)), format_func=lambda i: str(files.iloc[i].file_name))
                source = files.iloc[file_index]
                st.caption(str(source.file_path))
                content = source.content
                if content is None:
                    st.warning('Source exceeds the 16 MB preview limit. Use governed storage to inspect it.')
                else:
                    data = bytes(content)
                    extension = str(source.file_extension).lower()
                    if extension in ['png','jpg','jpeg']:
                        st.image(data, use_container_width=True)
                    elif extension == 'pdf':
                        encoded = base64.b64encode(data).decode()
                        st.components.v1.html(f'<iframe title="Original PDF" src="data:application/pdf;base64,{encoded}" width="100%" height="650"></iframe>',height=665)
                    elif extension in ['txt','json','csv','edi','eml']:
                        st.code(data.decode('utf-8',errors='replace')[:150000], language=None)
                    else:
                        st.info('Preview unavailable for this format. Download the original to inspect it.')
                    st.download_button('Download original',data,file_name=str(source.file_name))
                    if '-ATT-' in str(order.source_doc_id):
                        st.caption('This is the parent source. For an email attachment, download the email to inspect its attached document.')
        except Exception as exc:
            st.warning('Source evidence could not be loaded. Gold order data is still available.')
            st.code(str(exc))
    with right:
        st.subheader('Proposed order')
        a,b,c = st.columns(3)
        a.metric('Status',order.status)
        b.metric('Lowest confidence','Unknown' if pd.isna(order.confidence) else f'{order.confidence:.0%}')
        c.metric('Lines',int(order.lines))
        first = selected.iloc[0]
        st.write('**Customer:**', str(first.get('master_customer_name') or first.get('sold_to_name_raw')))
        st.write('**Ship-to:**', str(first.get('master_ship_to_name') or first.get('ship_to_name_raw')))
        reasons = sorted(set(selected.review_reasons.dropna()))
        for reason in reasons:
            if reason:
                st.warning(reason)
        visible = ['line_number','item_code','item_description','material_id','quantity_as_written','uom_as_written','quantity_each','expected_net_price','currency','proposed_atp_outcome','proposed_ship_quantity','overall_confidence','source_pointer']
        visible = [x for x in visible if x in selected]
        st.dataframe(selected[visible],hide_index=True,use_container_width=True)
        with st.expander('All fields and lineage'):
            st.dataframe(selected,hide_index=True,use_container_width=True)
        st.info('Review actions are disabled in this first release. Persistent corrections, audit storage, and approval validation must be configured before enabling them.')
        a,b,c = st.columns(3)
        a.button('Approve order',disabled=True)
        b.button('Save corrections',disabled=True)
        c.button('Reject order',disabled=True)
