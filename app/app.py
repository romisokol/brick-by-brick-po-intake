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

# Databricks authenticates visitors before this presentation-only welcome screen.
if not st.session_state.get('workspace_entered', False):
    st.markdown("""<style>
    [data-testid="stSidebar"]{display:none}
    .stApp{background:#f7f5f0}
    .welcome-brand{font-size:13px;letter-spacing:.32em;color:#756b56;margin:18px 0 60px}
    .welcome-kicker{font-size:12px;letter-spacing:.22em;color:#89784f;margin-bottom:20px}
    .welcome-title{font-family:Georgia,serif;font-size:clamp(44px,6vw,78px);font-weight:400;line-height:1.06;color:#18334b;margin:0 0 26px}
    .welcome-copy{font-size:18px;line-height:1.7;color:#63717b;max-width:560px;margin-bottom:28px}
    .welcome-panel{background:#18334b;border-radius:20px;padding:48px 38px;color:#f7f5f0;margin-top:12px}
    .welcome-monogram{font-family:Georgia,serif;font-size:72px;color:#d4bf8c;border-bottom:1px solid #61707a;padding-bottom:30px;margin-bottom:32px}
    .welcome-step{font-size:18px;margin:26px 0 10px;color:#fff}
    .welcome-detail{font-size:14px;line-height:1.65;color:#c3ced6}
    div.stButton > button[kind="primary"]{background:#18334b;border:1px solid #18334b;color:white;border-radius:8px;padding:12px 28px}
    </style>""", unsafe_allow_html=True)
    st.markdown('<div class="welcome-brand">MAISON SOLENNE</div>', unsafe_allow_html=True)
    intro, spacer, panel = st.columns([1.3,.12,1])
    with intro:
        st.markdown('<div class="welcome-kicker">ORDER OPERATIONS WORKSPACE</div><div class="welcome-title">Every order.<br>A clearer picture.</div><div class="welcome-copy">From incoming purchase orders to informed decisions. Explore your order book, focus on exceptions, and review every detail alongside its source.</div>', unsafe_allow_html=True)
        if st.button('Enter workspace →', type='primary', key='enter_workspace'):
            st.session_state.workspace_entered = True
            st.rerun()
        st.caption('Access authenticated by Databricks')
        st.caption('Live workspace tables · Synthetic project documents · Read-only preview')
    with panel:
        st.markdown('<div class="welcome-panel"><div class="welcome-monogram">MS</div><div class="welcome-step">01 &nbsp; See the whole picture</div><div class="welcome-detail">Order activity and quality, in one overview.</div><div class="welcome-step">02 &nbsp; Focus your attention</div><div class="welcome-detail">Find orders requiring review and understand why.</div><div class="welcome-step">03 &nbsp; Follow the evidence</div><div class="welcome-detail">Compare extracted details with the original document.</div></div>', unsafe_allow_html=True)
    st.stop()

def status_row_style(row):
    status = row.get('status', row.get('quality_status', ''))
    styles = {'FAILED': 'background-color: #FCE8EB; color: #922B3E;',
              'REVIEW': 'background-color: #FFF7E2; color: #765719;',
              'READY': 'background-color: #EAF5EF; color: #285D43;'}
    return [styles.get(status, '')] * len(row)

COLORS = {'READY':'#5B9476', 'REVIEW':'#C49A45', 'FAILED':'#BE6670'}
st.sidebar.title('MAISON SOLENNE')
st.sidebar.caption('ORDER OPERATIONS')
if st.sidebar.button('← Welcome', key='return_to_welcome'):
    st.session_state.workspace_entered = False
    st.rerun()
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
    st.dataframe(queue.style.apply(status_row_style, axis=1), hide_index=True, use_container_width=True,
        column_config={'confidence': st.column_config.NumberColumn(format='%.2f'), 'known_value':st.column_config.NumberColumn('Known value (original currency)',format='%.2f')})
    st.caption('Open Order review in the sidebar to inspect an order. Nothing in this screen changes source data.')

else:
    def render_order_review(tab_orders, tab_key):
        if tab_orders.empty:
            st.info('No orders in this section match the current filters.')
            return
        labels = []
        for r in tab_orders.itertuples():
            parts = [str(r.source_doc_id), str(r.po_number) if pd.notna(r.po_number) and str(r.po_number).strip() else 'Missing PO']
            if pd.notna(r.source_order_index):
                order_index = r.source_order_index
                if isinstance(order_index, float) and order_index.is_integer():
                    order_index = int(order_index)
                parts.append(f'order {order_index}')
            parts.append(str(r.status))
            labels.append(' · '.join(parts))
        index = st.selectbox('Choose an order', range(len(tab_orders)), format_func=lambda i: labels[i], key=f'order_{tab_key}')
        order = tab_orders.iloc[index]
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
                    file_index = st.selectbox('Original file', range(len(files)), format_func=lambda i: str(files.iloc[i].file_name), key=f'file_{tab_key}_{index}')
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
                        st.download_button('Download original',data,file_name=str(source.file_name), key=f'download_{tab_key}_{index}')
                        if '-ATT-' in str(order.source_doc_id):
                            st.caption('This is the parent source. For an email attachment, download the email to inspect its attached document.')
            except Exception as exc:
                st.warning('Source evidence could not be loaded. Gold order data is still available.')
                st.code(str(exc))
        with right:
            st.subheader('Proposed order')
            if order.status == 'FAILED':
                st.error('FAILED · Extraction requires attention')
            elif order.status == 'READY':
                st.success('READY · Passed current readiness checks')
            else:
                st.warning('REVIEW · Needs human review')
            b,c = st.columns(2)
            b.metric('Confidence','Unknown' if pd.isna(order.confidence) else f'{order.confidence:.0%}')
            c.metric('Lines',int(order.lines))
            first = selected.iloc[0]
            st.write('**Customer:**', str(first.get('master_customer_name') or first.get('sold_to_name_raw')))
            st.write('**Ship-to:**', str(first.get('master_ship_to_name') or first.get('ship_to_name_raw')))
            reasons = sorted({reason.strip() for value in selected.review_reasons.dropna() for reason in str(value).split(';') if reason.strip()})
            if reasons:
                st.warning('**Issues requiring review**\n\n' + '\n'.join(f'- {reason}' for reason in reasons))
            visible = ['quality_status','line_number','item_code','item_description','material_id','quantity_as_written','uom_as_written','quantity_each','expected_net_price','currency','proposed_atp_outcome','proposed_ship_quantity','overall_confidence','source_pointer']
            visible = [x for x in visible if x in selected]
            st.dataframe(selected[visible].style.apply(status_row_style, axis=1),hide_index=True,use_container_width=True)
            with st.expander('All fields and lineage'):
                st.dataframe(selected,hide_index=True,use_container_width=True)

    needs_attention = orders[orders.status.isin(['REVIEW', 'FAILED'])]
    ready_orders = orders[orders.status.eq('READY')]
    attention_tab, ready_tab = st.tabs([
        f'Needs review & failed ({len(needs_attention)})',
        f'Ready ({len(ready_orders)})'
    ])
    with attention_tab:
        st.caption('Review orders needing attention. FAILED orders and lines are highlighted in red.')
        render_order_review(needs_attention, 'attention')
    with ready_tab:
        st.caption('Orders that passed current readiness checks. READY is not a recorded approval.')
        render_order_review(ready_orders, 'ready')
