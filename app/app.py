import io
import json
from email import policy
from email.parser import BytesParser
import pypdfium2 as pdfium
import pandas as pd
import streamlit as st
import altair as alt
from database import (load_lines, source_files, ensure_approval_tables, load_approved,
    record_decision, gold_rows_for_order, check_not_stale, StaleGoldError)
from logic import (orders_from_lines, select_order, ready_order_lines, line_identifier,
    row_hash, diff_editable_fields, EDITABLE_FIELDS)

def current_actor():
    """The signed-in reviewer, from the identity Databricks forwards to the
    app. No fallback: a local `streamlit run` has no forwarded identity, and
    approvals must never be attributed to the app's own service principal."""
    try:
        return st.context.headers.get('X-Forwarded-Email') or None
    except Exception:
        return None

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

def readable(value, fallback='Not available'):
    if value is None or pd.isna(value) or not str(value).strip():
        return fallback
    return str(value)


def render_source(data, extension, key):
    extension = extension.lstrip('.')
    if extension in ['png', 'jpg', 'jpeg']:
        st.image(data, use_container_width=True)
    elif extension == 'pdf':
        try:
            document = pdfium.PdfDocument(data)
            try:
                page_index = st.selectbox('Page', range(len(document)),
                    format_func=lambda i: f'Page {i + 1} of {len(document)}', key=f'pdf_{key}')
                page = document[page_index]
                try:
                    bitmap = page.render(scale=min(1.7, 1600 / max(page.get_size())))
                    try:
                        image = bitmap.to_pil()
                        output = io.BytesIO()
                        image.save(output, format='PNG')
                        st.image(output.getvalue(), use_container_width=True)
                    finally:
                        bitmap.close()
                finally:
                    page.close()
            finally:
                document.close()
        except Exception:
            st.warning('This document could not be previewed. Download the original below to view it.')
    else:
        text = data.decode('utf-8', errors='replace')
        if extension == 'json':
            try:
                payload = json.loads(text)
            except ValueError:
                payload = None
            if isinstance(payload, dict) and isinstance(payload.get('segments'), list):
                st.caption('Call transcript · Original wording')
                for segment in payload['segments']:
                    if not isinstance(segment, dict):
                        continue
                    speaker = str(segment.get('speaker', 'Speaker')).replace('_', ' ').title()
                    start = segment.get('start')
                    timestamp = ''
                    if isinstance(start, (int, float)) and start >= 0:
                        timestamp = f' · {int(start)//60:02d}:{int(start)%60:02d}'
                    with st.container(border=True):
                        st.caption(speaker + timestamp)
                        st.text(str(segment.get('text', '')))
                with st.expander('Transcript metadata and original JSON'):
                    st.json(payload)
            elif payload is not None:
                st.caption('Structured source document')
                st.json(payload, expanded=2)
            else:
                st.text(text[:150000])
        elif extension == 'csv':
            try:
                st.dataframe(pd.read_csv(io.StringIO(text), sep=None, engine='python', nrows=1000),
                    hide_index=True, use_container_width=True)
                st.caption('Preview shows up to 1,000 source rows.')
            except Exception:
                st.text(text[:150000])
        elif extension == 'eml':
            message = BytesParser(policy=policy.default).parsebytes(data)
            for label in ['From', 'To', 'Subject', 'Date']:
                st.text(f'{label}: {message.get(label, "Not available")}')
            body = message.get_body(preferencelist=('plain',))
            if body:
                st.text(str(body.get_content())[:150000])
            else:
                st.info('Download the email to inspect its formatted body and attachments.')
        elif extension in ['txt', 'edi']:
            st.caption('Original transcript' if extension == 'txt' else 'Original EDI document')
            st.text(text[:150000])
        else:
            st.info('Download the original file to view this format.')


COLORS = {'READY':'#5B9476', 'REVIEW':'#C49A45', 'FAILED':'#BE6670'}
st.sidebar.title('MAISON SOLENNE')
st.sidebar.caption('ORDER OPERATIONS')
if st.sidebar.button('← Welcome', key='return_to_welcome'):
    st.session_state.workspace_entered = False
    st.rerun()
page = st.sidebar.radio('Workspace', ['Overview', 'Review queue', 'Order review'])
st.sidebar.caption('Live Databricks data · View only')
if st.sidebar.button('Refresh data'):
    for key in ['gold_lines', 'approved', 'approvals_ready', 'approvals_error', 'auto_included']:
        st.session_state.pop(key, None)
    st.rerun()

st.title(page)
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
st.caption('Proposed sales orders · quantities in individual units · values in original currencies')

if 'approvals_ready' not in st.session_state:
    try:
        ensure_approval_tables()
        st.session_state.approved = load_approved()
        st.session_state.approvals_ready = True
        st.session_state.approvals_error = None
    except Exception as exc:
        st.session_state.approved = pd.DataFrame()
        st.session_state.approvals_ready = False
        st.session_state.approvals_error = str(exc)

if st.session_state.approvals_ready and not st.session_state.get('auto_included'):
    # Mentor-suggested rule: READY orders passed every automated check already,
    # so they're included without waiting on a reviewer click. Still logged.
    ready_lines = ready_order_lines(lines, orders)
    approved_ids = set(st.session_state.approved.get('gold_line_id', []))
    to_write, events = [], []
    for _, row in ready_lines.iterrows():
        gold_id, _ = line_identifier(row)
        if gold_id in approved_ids:
            continue
        to_write.append(row)
        events.append({'action': 'AUTO_APPROVE', 'gold_line_id': gold_id,
            'source_doc_id': row.get('source_doc_id'), 'po_number': row.get('po_number'),
            'source_order_index': row.get('source_order_index'), 'line_number': row.get('line_number'),
            'reason': 'READY order auto-included per mentor-suggested rule.',
            'source_row_hash': row_hash(row)})
    if to_write:
        try:
            record_decision(to_write, 'system:auto-ready-rule', 'AUTO', events)
            st.session_state.approved = load_approved()
            st.toast(f'Auto-included {len(to_write)} line(s) from READY orders into approved orders.')
        except Exception as exc:
            st.warning('Could not auto-include READY orders into approved orders.')
            st.code(str(exc))
    st.session_state.auto_included = True

if page == 'Overview':
    cols = st.columns(4)
    cols[0].metric('Proposed orders', f'{len(orders):,}')
    cols[1].metric('Order lines', f'{len(lines):,}')
    cols[2].metric('Needs attention', f'{orders.status.ne("READY").sum():,}')
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
        st.altair_chart(alt.Chart(counts).mark_bar(color='#527FA3').encode(x='lines:Q',y=alt.Y('channel:N',sort='-x', title=None, axis=alt.Axis(labelLimit=220)),tooltip=['channel','lines']).properties(height=300), use_container_width=True)
    left, right = st.columns(2)
    with left:
        st.subheader('Reasons for review')
        reasons = lines.loc[lines.needs_review.fillna(True), 'review_reasons'].dropna().str.split('; ').explode()
        counts = reasons[reasons.ne('')].value_counts().head(8).rename_axis('reason').reset_index(name='lines')
        st.altair_chart(alt.Chart(counts).mark_bar(color='#527FA3').encode(x='lines:Q', y=alt.Y('reason:N',sort='-x', title=None, axis=alt.Axis(labelLimit=280)), tooltip=['reason','lines']).properties(height=300),use_container_width=True)
    with right:
        st.subheader('Inventory proposals')
        counts = lines.groupby('proposed_atp_outcome').size().reset_index(name='lines')
        st.altair_chart(alt.Chart(counts).mark_bar(color='#527FA3').encode(x=alt.X('proposed_atp_outcome:N', title='Availability proposal', axis=alt.Axis(labelAngle=0)), y='lines:Q',tooltip=['proposed_atp_outcome','lines']).properties(height=300),use_container_width=True)

elif page == 'Review queue':
    st.caption('Prioritized by missing or low confidence, then by currency and known order value.')
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
                    with st.expander('Source location'):
                        st.caption(str(source.file_path))
                    content = source.content
                    if content is None:
                        st.warning('Source exceeds the 16 MB preview limit. Use governed storage to inspect it.')
                    else:
                        data = bytes(content)
                        extension = str(source.file_extension).lower()
                        render_source(data, extension, f'{tab_key}_{index}_{file_index}')
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
            st.write('**Customer:**', readable(first.get('master_customer_name'), readable(first.get('sold_to_name_raw'))))
            st.write('**Deliver to:**', readable(first.get('master_ship_to_name'), readable(first.get('ship_to_name_raw'))))
            reasons = sorted({reason.strip() for value in selected.review_reasons.dropna() for reason in str(value).split(';') if reason.strip()})
            if reasons:
                st.warning('**Order notes and review flags**\n\n' + '\n'.join(f'- {reason}' for reason in reasons))
            visible = ['quality_status','line_number','item_code','item_description','material_id','quantity_as_written','uom_as_written','quantity_each','expected_net_price','currency','proposed_atp_outcome','proposed_ship_quantity','overall_confidence','source_pointer']
            visible = [x for x in visible if x in selected]
            st.subheader('Order lines')
            names = {'quality_status':'Status', 'line_number':'Line', 'item_code':'Source item code',
                'item_description':'Product description', 'material_id':'Matched SKU',
                'quantity_as_written':'Source quantity', 'uom_as_written':'Source unit',
                'quantity_each':'Quantity (units)', 'expected_net_price':'Expected unit price',
                'currency':'Currency', 'proposed_atp_outcome':'Availability proposal',
                'proposed_ship_quantity':'Proposed ship quantity', 'overall_confidence':'Confidence',
                'source_pointer':'Source reference'}
            st.dataframe(selected[visible].style.apply(status_row_style, axis=1),hide_index=True,
                use_container_width=True, column_config={name: st.column_config.Column(label) for name,label in names.items()})

            st.subheader('Approve this order')
            if not st.session_state.get('approvals_ready'):
                st.info('Approval writes are unavailable: ' + str(st.session_state.get('approvals_error')))
            else:
                approved_df = st.session_state.approved
                line_records = selected.to_dict('records')
                identifiers = [line_identifier(r) for r in line_records]
                line_ids = [i for i, _ in identifiers]
                already = approved_df[approved_df.gold_line_id.isin(line_ids)] if not approved_df.empty else approved_df
                if not already.empty:
                    first_approved = already.iloc[0]
                    st.success(f"In approved orders · {first_approved['approved_by']} · "
                        f"{first_approved['approved_at']} ({first_approved['approval_type']})")
                if any(is_fallback for _, is_fallback in identifiers):
                    st.caption('Gold has no durable line_id for one or more lines here; a content-based '
                        'fallback id is used, which will not survive a re-extraction of the source document.')
                editable = [c for c in EDITABLE_FIELDS if c in selected]
                with st.form(key=f'approve_form_{tab_key}_{index}'):
                    edited = st.data_editor(selected[['line_number'] + editable].reset_index(drop=True),
                        hide_index=True, use_container_width=True, disabled=['line_number'],
                        num_rows='fixed', key=f'editor_{tab_key}_{index}')
                    reason = st.text_input('Reason / note', key=f'reason_{tab_key}_{index}',
                        placeholder='Why approve, or what you corrected')
                    submitted = st.form_submit_button('Approve order', type='primary')
                if submitted:
                    actor = current_actor()
                    if not actor:
                        st.error('No authenticated reviewer identity was forwarded for this session. '
                            'Approval is blocked. (Use the deployed Databricks App, not a local streamlit run.)')
                    else:
                        try:
                            live = gold_rows_for_order(order)
                            check_not_stale(line_records, live)
                            updated_rows, decision_events = [], []
                            for original, (_, edit_row) in zip(line_records, edited.iterrows()):
                                merged = dict(original)
                                merged.update({c: edit_row[c] for c in editable})
                                gold_id, _ = line_identifier(original)
                                base = {'gold_line_id': gold_id, 'source_doc_id': original.get('source_doc_id'),
                                    'po_number': original.get('po_number'),
                                    'source_order_index': original.get('source_order_index'),
                                    'line_number': original.get('line_number')}
                                for field, before, after in diff_editable_fields(original, merged):
                                    decision_events.append({**base, 'action': 'EDIT', 'field_name': field,
                                        'before_value': before, 'after_value': after, 'reason': reason})
                                decision_events.append({**base, 'action': 'APPROVE', 'reason': reason,
                                    'source_row_hash': row_hash(merged)})
                                updated_rows.append(merged)
                            record_decision(updated_rows, actor, 'MANUAL', decision_events)
                            st.session_state.approved = load_approved()
                            st.success(f'Approved by {actor}.')
                            st.rerun()
                        except StaleGoldError as exc:
                            st.error(str(exc))
                        except Exception as exc:
                            st.error('Approval failed.')
                            st.code(str(exc))

            with st.expander('Technical details and source lineage'):
                st.dataframe(selected,hide_index=True,use_container_width=True)

    needs_attention = orders[orders.status.isin(['REVIEW', 'FAILED'])]
    ready_orders = orders[orders.status.eq('READY')]
    attention_tab, ready_tab = st.tabs([
        f'Needs attention ({len(needs_attention)})',
        f'Ready ({len(ready_orders)})'
    ])
    with attention_tab:
        st.caption('Review orders needing attention. FAILED orders and lines are highlighted in red.')
        render_order_review(needs_attention, 'attention')
    with ready_tab:
        st.caption('Passed current readiness checks · Auto-included in approved orders; re-approve here after any correction.')
        render_order_review(ready_orders, 'ready')
