import streamlit as st
import pandas as pd
import plotly.express as px
import os
from io import BytesIO

# =========================
# 页面配置
# =========================
st.set_page_config(page_title="资讯平台数据分析", layout="wide")
st.title("📊 资讯平台文章审核数据分析")

# =========================
# Sidebar 菜单
# =========================
menu = st.sidebar.radio("选择功能", [
    "功能 1：单日 Provider 汇入数量分析",
    "功能 2：多日趋势分析（分组显示）"
])

# =========================
# 上传文件
# =========================
st.sidebar.markdown("### 上传文件")
provider_file = st.sidebar.file_uploader("上传 Provider 映射（xlsx，需含 ProviderName 和 ProviderId）", type=["xlsx"])
import_files = st.sidebar.file_uploader("上传汇入量文件（xlsx，可多选）", type=["xlsx"], accept_multiple_files=True)
holidays_file = st.sidebar.file_uploader("上传节假日文件（CSV，需含列：date，可选 holiday_name）", type=["csv"])

# =========================
# 功能 2 参数（白名单 + 报警阈值）
# =========================
st.sidebar.markdown("### 功能 2 参数设置")
alert_threshold_pct = st.sidebar.slider("异常报警阈值（%）", min_value=10, max_value=90, value=50, step=5,
                                        help="当最新日与历史均值的相对变化幅度 ≥ 阈值时触发报警。默认 50%")
# 白名单在数据加载后动态提供（见功能 2 代码块）

# =========================
# 工具函数
# =========================
def export_excel(df, filename):
    """Excel 导出（自动选择 openpyxl / xlsxwriter 引擎）"""
    output = BytesIO()
    writer = None
    for eng in ("openpyxl", "xlsxwriter"):
        try:
            writer = pd.ExcelWriter(output, engine=eng)
            break
        except Exception:
            writer = None
    if writer is None:
        st.error("未找到可用的 Excel 写入引擎，请安装 openpyxl 或 XlsxWriter")
        st.stop()
    with writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    st.download_button(
        label="📥 下载分析结果",
        data=output.getvalue(),
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

def normalize_columns(df):
    df.columns = [col.strip().lower() for col in df.columns]
    return df

def parse_date_series(s):
    """
    将字符串解析为日期（支持 YYYY-MM-DD / YYYYMMDD 等），失败返回 NaT。
    通常用文件名（不含扩展名）作为日期来源。
    """
    dt = pd.to_datetime(s, errors='coerce')
    return dt.dt.date

def load_holidays_set(uploaded_csv) -> set:
    """
    从上传的 holidays.csv 读取节假日集合（逐日口径）。
    需要至少包含 date 列；holiday_name 可选。
    返回：set[date]
    """
    if uploaded_csv is None:
        return set()
    try:
        df = pd.read_csv(uploaded_csv)
        df = normalize_columns(df)
        if "date" not in df.columns:
            st.error("holidays.csv 必须包含列：date（格式建议 YYYY-MM-DD）")
            return set()
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
        df = df.dropna(subset=["date"]).reset_index(drop=True)
        return set(df["date"].tolist())
    except Exception as e:
        st.error(f"读取节假日文件失败：{e}")
        return set()

def anomaly_alerts_block(df_daily: pd.DataFrame, title_latest_day: str, anomaly_filename_prefix: str, threshold_pct: float):
    """
    顶部异常报警块：
    - df_daily：按 providerid / provider_label / date（日期）汇总后的 DataFrame（列：providerid, provider_label, date, importcount）
    - title_latest_day：用于展示的“最新日”标题（如 '最新工作日' / '最新一天'）
    - anomaly_filename_prefix：导出文件前缀（如 'WorkdaysOnly' / 'AllIncluded'）
    - threshold_pct：报警阈值（百分比），例如 50 表示 50%
    """
    if df_daily.empty or df_daily["date"].isna().all():
        st.warning("没有有效的日期数据，无法计算异常。")
        return

    latest_date = df_daily["date"].max()
    latest_df = df_daily[df_daily["date"] == latest_date].copy()
    history_df = df_daily[df_daily["date"] < latest_date].copy()

    st.markdown("""
        <style>
        .alert-exclam { color: #d00000; font-weight: 800; font-size: 18px; margin-right: 6px; }
        .alert-line { font-size: 16px; line-height: 1.6; }
        .alert-box { padding: 10px 12px; background-color: #fff5f5; border-left: 4px solid #d00000; border-radius: 6px; margin-bottom: 16px; }
        </style>
    """, unsafe_allow_html=True)

    if history_df.empty:
        st.markdown(
            f"<div class='alert-box'>仅有{title_latest_day}（{pd.to_datetime(latest_date).strftime('%Y/%m/%d')}），缺少过往数据用于对比，暂无法报警。</div>",
            unsafe_allow_html=True
        )
        return

    # 历史均值（不含最新日）
    hist_mean = (
        history_df.groupby(["providerid", "provider_label"], dropna=False)["importcount"]
        .mean()
        .reset_index()
        .rename(columns={"importcount": "hist_avg"})
    )

    # 合并最新日数据
    compare_df = pd.merge(
        latest_df[["providerid", "provider_label", "date", "importcount"]],
        hist_mean,
        on=["providerid", "provider_label"],
        how="left"
    )

    # 仅保留历史均值 > 500 的 Provider
    compare_df = compare_df[compare_df["hist_avg"] > 500].copy()

    # 变化比例与方向
    compare_df["change_ratio"] = (compare_df["importcount"] - compare_df["hist_avg"]) / compare_df["hist_avg"]
    compare_df["direction"] = compare_df["change_ratio"].apply(lambda x: "上升" if x >= 0 else "降低")
    compare_df["change_pct"] = (compare_df["change_ratio"] * 100).round(2)

    # 阈值（百分比 → 比例）
    threshold_ratio = float(threshold_pct) / 100.0
    alerts_df = compare_df[compare_df["change_ratio"].abs() >= threshold_ratio].copy()

    if alerts_df.empty:
        st.markdown(
            f"<div class='alert-box'>✅ {title_latest_day}（{pd.to_datetime(latest_date).strftime('%Y/%m/%d')}）未发现异常波动（满足条件的 Provider）。</div>",
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            f"<div class='alert-box'><b>🚩 异常报警（{title_latest_day}：{pd.to_datetime(latest_date).strftime('%Y/%m/%d')}，阈值：{threshold_pct}%）</b><br/>",
            unsafe_allow_html=True
        )
        for _, row in alerts_df.sort_values(by="change_ratio", key=lambda s: s.abs(), ascending=False).iterrows():
            date_str = pd.to_datetime(row["date"]).strftime("%Y/%m/%d")
            provider = row["provider_label"]
            msg = f"<span class='alert-exclam'>！</span><span class='alert-line'>{provider} 在 {date_str} 的汇入量异常{row['direction']}</span>"
            st.markdown(msg, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        # 异常明细导出
        show_cols = ["providerid", "provider_label", "date", "importcount", "hist_avg", "change_pct", "direction"]
        pretty_df = alerts_df[show_cols].copy()
        pretty_df = pretty_df.rename(columns={
            "providerid": "ProviderId",
            "provider_label": "Provider",
            "date": "最新日期",
            "importcount": "最新日汇入量",
            "hist_avg": "过往日均值",
            "change_pct": "变化百分比(%)",
            "direction": "方向"
        })
        pretty_df["最新日期"] = pd.to_datetime(pretty_df["最新日期"]).dt.strftime("%Y/%m/%d")
        with st.expander("查看异常明细（含下载）", expanded=False):
            st.dataframe(pretty_df, use_container_width=True)
            export_excel(pretty_df, f"Import_Anomaly_{anomaly_filename_prefix}_{pd.to_datetime(latest_date).strftime('%Y%m%d')}.xlsx")

def prepare_import_data(import_files, provider_map):
    """
    读取上传的 import xlsx 文件，合并 provider_map，解析日期，构造展示标签 provider_label。
    返回：import_data（含列：providerid、providername（可空）、provider_label、importcount、date_parsed）
    """
    import_data = pd.DataFrame()
    if import_files:
        for file in import_files:
            df = pd.read_excel(file)
            df = normalize_columns(df)
            date_str = os.path.splitext(file.name)[0]  # 用文件名作为日期来源
            df["date"] = date_str
            import_data = pd.concat([import_data, df], ignore_index=True)

    if import_data.empty:
        return import_data

    # 基础列校验
    if "providerid" not in import_data.columns or "importcount" not in import_data.columns:
        st.error("汇入量文件必须包含列：ProviderId 和 ImportCount")
        st.stop()

    # 合并 Provider 名称
    if not provider_map.empty:
        import_data = import_data.merge(provider_map, on="providerid", how="left")

    # 构造展示标签（优先 providername，否则用 providerid）
    # providerid 可能为数值，需转为字符串
    import_data["providerid_str"] = import_data["providerid"].astype(str)
    if "providername" in import_data.columns:
        import_data["provider_label"] = import_data["providername"].where(import_data["providername"].notna(), import_data["providerid_str"])
    else:
        import_data["provider_label"] = import_data["providerid_str"]

    # 解析日期
    import_data["date_parsed"] = parse_date_series(import_data["date"])
    if import_data["date_parsed"].isna().any():
        st.warning("⚠️ 检测到部分记录的日期无法从文件名解析（建议使用 2025-11-12 或 20251112），这些记录将被忽略。")
        import_data = import_data[~import_data["date_parsed"].isna()].copy()

    return import_data

# =========================
# Step 1: 处理 Provider 映射文件
# =========================
provider_map = pd.DataFrame()
if provider_file:
    try:
        provider_map = pd.read_excel(provider_file)
        provider_map = normalize_columns(provider_map)
        if "providername" not in provider_map.columns or "providerid" not in provider_map.columns:
            st.error("Provider 映射文件必须包含列：ProviderName 和 ProviderId")
            st.stop()
        provider_map = provider_map.drop_duplicates(subset=["providerid"]).reset_index(drop=True)
    except Exception as e:
        st.error(f"读取 Provider 映射失败：{e}")
        st.stop()

# =========================
# Step 2: 处理汇入量文件 & 节假日
# =========================
import_data = prepare_import_data(import_files, provider_map)
holidays_set = load_holidays_set(holidays_file)

# =========================
# 功能 1：单日 Provider 汇入数量分析
# =========================
if menu == "功能 1：单日 Provider 汇入数量分析":
    st.subheader("📌 单日 Provider 汇入数量分析")
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        avail_dates = sorted(import_data["date_parsed"].dropna().unique())
        if len(avail_dates) == 0:
            st.warning("没有可用的有效日期数据。")
        else:
            date_strs = [pd.to_datetime(d).strftime("%Y-%m-%d") for d in avail_dates]
            selected_date_str = st.selectbox("选择日期", date_strs)
            selected_date = pd.to_datetime(selected_date_str).date()

            day_data = import_data[import_data["date_parsed"] == selected_date]
            provider_counts = (
                day_data.groupby("provider_label", dropna=False)["importcount"]
                .sum()
                .reset_index()
                .sort_values(by="importcount", ascending=False)
            )

            st.write("各 Provider 汇入数量：")
            st.dataframe(provider_counts, use_container_width=True)

            fig = px.bar(provider_counts, x="provider_label", y="importcount",
                         labels={"provider_label": "Provider", "importcount": "汇入数量"},
                         title=f"{selected_date_str} 各 Provider 汇入数量")
            st.plotly_chart(fig, use_container_width=True)

            export_excel(provider_counts, f"Provider_Import_{selected_date_str}.xlsx")

# =========================
# 功能 2：多日趋势分析（分组显示） + 次级功能
# =========================
elif menu == "功能 2：多日趋势分析（分组显示）":
    st.subheader("📌 多日趋势分析（按 Provider 分组，每组 10 个）")

    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        # ---------- Provider 白名单（动态提供） ----------
        all_providers = sorted(import_data["provider_label"].dropna().unique().tolist())
        whitelist = st.sidebar.multiselect("Provider 白名单（只看选中项；留空表示查看全部）", options=all_providers, default=[])

        # 两个次级功能标签页
        tab_workdays, tab_all = st.tabs(["工作日统计（含节假日开关）", "全量统计（包含周末与节假日）"])

        # ===== 次级功能 1：工作日统计（含节假日开关） =====
        with tab_workdays:
            st.markdown("### 🗓️ 仅统计工作日（周一~周五），可选择是否排除节假日")
            df = import_data.copy()

            # Provider 白名单筛选
            if whitelist:
                df = df[df["provider_label"].isin(whitelist)].copy()

            # 仅工作日
            df["weekday"] = pd.to_datetime(df["date_parsed"]).dt.weekday  # 周一=0 ... 周日=6
            df = df[df["weekday"] < 5].copy()

            # 开关：是否同时排除节假日
            use_holidays = st.checkbox("同时排除节假日（来自上传的 holidays.csv）", value=True, key="workdays_use_holidays")
            if use_holidays:
                if len(holidays_set) > 0:
                    before_n = len(df)
                    df = df[~df["date_parsed"].isin(holidays_set)].copy()
                    st.caption(f"已排除法定节假日：移除 {before_n - len(df)} 行（holidays.csv 共 {len(holidays_set)} 天）")
                else:
                    st.warning("已开启“同时排除节假日”，但未上传 holidays.csv。当前仅排除周末。")

            if df.empty:
                st.warning("过滤后无数据，请检查日期或节假日设置、白名单选择。")
            else:
                # 顶部异常报警（基于过滤后的数据）
                daily_import = (
                    df.groupby(["providerid", "provider_label", "date_parsed"], dropna=False)["importcount"]
                    .sum()
                    .reset_index()
                    .rename(columns={"date_parsed": "date"})
                )
                anomaly_alerts_block(daily_import, title_latest_day="最新工作日", anomaly_filename_prefix="WorkdaysOnly",
                                     threshold_pct=alert_threshold_pct)

                # 趋势图（分组显示）
                provider_total = df.groupby("provider_label", dropna=False)["importcount"].sum().sort_values(ascending=False)
                providers_sorted = provider_total.index.tolist()
                group_size = 10
                provider_groups = [providers_sorted[i:i+group_size] for i in range(0, len(providers_sorted), group_size)]

                trend_data = (
                    df.groupby(["date_parsed", "provider_label"], dropna=False)["importcount"]
                    .sum()
                    .reset_index()
                    .rename(columns={"date_parsed": "date"})
                    .sort_values(by="date")
                )

                all_group_data = []
                for idx, group in enumerate(provider_groups, start=1):
                    st.markdown(f"#### 第 {idx} 组趋势图（工作日）")
                    group_data = trend_data[trend_data["provider_label"].isin(group)]
                    all_group_data.append(group_data)
                    fig = px.line(group_data, x="date", y="importcount", color="provider_label",
                                  labels={"provider_label": "Provider", "importcount": "汇入数量", "date": "日期"},
                                  title=f"Provider 趋势分析（第 {idx} 组，工作日）", markers=True)
                    st.plotly_chart(fig, use_container_width=True)

                if all_group_data:
                    export_excel(pd.concat(all_group_data), "Provider_Trend_WorkdaysOnly.xlsx")

        # ===== 次级功能 2：全量统计（包含周末与节假日） =====
        with tab_all:
            st.markdown("### 📅 全量数据集（包含周末与节假日）")
            df = import_data.copy()

            # Provider 白名单筛选
            if whitelist:
                df = df[df["provider_label"].isin(whitelist)].copy()

            if df.empty:
                st.warning("全量数据集为空（可能被白名单筛选为空）。")
            else:
                # 顶部异常报警（基于全量数据）
                daily_import = (
                    df.groupby(["providerid", "provider_label", "date_parsed"], dropna=False)["importcount"]
                    .sum()
                    .reset_index()
                    .rename(columns={"date_parsed": "date"})
                )
                anomaly_alerts_block(daily_import, title_latest_day="最新一天", anomaly_filename_prefix="AllIncluded",
                                     threshold_pct=alert_threshold_pct)

                # 趋势图（分组显示）
                provider_total = df.groupby("provider_label", dropna=False)["importcount"].sum().sort_values(ascending=False)
                providers_sorted = provider_total.index.tolist()
                group_size = 10
                provider_groups = [providers_sorted[i:i+group_size] for i in range(0, len(providers_sorted), group_size)]

                trend_data = (
                    df.groupby(["date_parsed", "provider_label"], dropna=False)["importcount"]
                    .sum()
                    .reset_index()
                    .rename(columns={"date_parsed": "date"})
                    .sort_values(by="date")
                )

                all_group_data = []
                for idx, group in enumerate(provider_groups, start=1):
                    st.markdown(f"#### 第 {idx} 组趋势图（全量）")
                    group_data = trend_data[trend_data["provider_label"].isin(group)]
                    all_group_data.append(group_data)
                    fig = px.line(group_data, x="date", y="importcount", color="provider_label",
                                  labels={"provider_label": "Provider", "importcount": "汇入数量", "date": "日期"},
                                  title=f"全量 Provider 趋势分析（第 {idx} 组）", markers=True)
                    st.plotly_chart(fig, use_container_width=True)

                if all_group_data:
                    export_excel(pd.concat(all_group_data), "Provider_Trend_AllIncluded.xlsx")