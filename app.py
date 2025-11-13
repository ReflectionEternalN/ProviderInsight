import streamlit as st
import pandas as pd
import plotly.express as px
import os
from io import BytesIO

# =========================
# 页面配置与样式
# =========================
st.set_page_config(page_title="MSN Provider Insight", layout="wide")

st.markdown("""
<style>
.app-main-title h1 {
    font-size: 2.50rem !important;
    font-weight: 800;
    margin: 0 !important;
    padding: 0 !important;
}
.page-title {
    font-size: 1.60rem !important;
    font-weight: 700;
    margin: 0.2rem 0 0.6rem 0;
}
.page-subtitle { display: none; }
.alert-exclam { color: #d00000; font-weight: 800; font-size: 16px; margin-right: 6px; }
.alert-line { font-size: 14px; line-height: 1.6; }
.alert-box { padding: 8px 10px; background-color: #fff5f5; border-left: 4px solid #d00000; border-radius: 6px; margin-bottom: 12px; }
.section-title {
    font-size: 1.05rem !important;
    font-weight: 600;
    margin: 0.4rem 0 0.4rem 0;
}
</style>
""", unsafe_allow_html=True)

# 顶部主标题
st.markdown("<div class='app-main-title'><h1>MSN Provider Insight</h1></div>", unsafe_allow_html=True)

# =========================
# 菜单
# =========================
menu = st.sidebar.radio("选择功能", [
    "功能 1：单日分析",
    "功能 2：仅工作日",
    "功能 3：仅周末",
    "功能 4：全部数据"
])

# =========================
# 侧边栏：上传与参数
# =========================
st.sidebar.markdown("🗂️ 文件上传")
provider_file = st.sidebar.file_uploader("上传 Provider ID & Name", type=["xlsx"])
import_files = st.sidebar.file_uploader("上传汇入量文件", type=["xlsx"], accept_multiple_files=True)
holidays_file = st.sidebar.file_uploader("上传节假日", type=["csv"])

st.sidebar.markdown("⚙️ 参数设置")
alert_threshold_pct = st.sidebar.slider("报警阈值（%）", min_value=10, max_value=90, value=50, step=5)

# =========================
# 工具函数
# =========================
def export_excel(df, filename):
    output = BytesIO()
    writer = None
    for eng in ("openpyxl", "xlsxwriter"):
        try:
            writer = pd.ExcelWriter(output, engine=eng)
            break
        except Exception:
            writer = None
    if writer is None:
        st.error("缺少 Excel 写入引擎，请安装 openpyxl 或 XlsxWriter")
        st.stop()
    with writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    st.download_button("📥 下载结果", output.getvalue(), file_name=filename,
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def normalize_columns(df):
    df.columns = [col.strip().lower() for col in df.columns]
    return df

def parse_date_series(s):
    return pd.to_datetime(s, errors='coerce').dt.date

def load_holidays_set(uploaded_csv) -> set:
    if uploaded_csv is None:
        return set()
    try:
        df = pd.read_csv(uploaded_csv)
        df = normalize_columns(df)
        if "date" not in df.columns:
            st.error("节假日文件需包含列：date")
            return set()
        df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
        df = df.dropna(subset=["date"]).reset_index(drop=True)
        return set(df["date"].tolist())
    except Exception as e:
        st.error(f"读取节假日文件失败：{e}")
        return set()

def anomaly_alerts_block(df_daily: pd.DataFrame, title_latest_day: str, filename_prefix: str, threshold_pct: float):
    if df_daily.empty or df_daily["date"].isna().all():
        st.warning("无可用日期数据")
        return

    latest_date = df_daily["date"].max()
    latest_df = df_daily[df_daily["date"] == latest_date].copy()
    history_df = df_daily[df_daily["date"] < latest_date].copy()

    if history_df.empty:
        st.markdown(
            f"<div class='alert-box'>仅有{title_latest_day} {pd.to_datetime(latest_date).strftime('%Y/%m/%d')}，无历史对比</div>",
            unsafe_allow_html=True
        )
        return

    hist_mean = (
        history_df.groupby(["providerid", "provider_label"], dropna=False)["importcount"]
        .mean().reset_index().rename(columns={"importcount": "hist_avg"})
    )

    compare_df = pd.merge(
        latest_df[["providerid", "provider_label", "date", "importcount"]],
        hist_mean, on=["providerid", "provider_label"], how="left"
    )
    compare_df = compare_df[compare_df["hist_avg"] > 500].copy()

    compare_df["change_ratio"] = (compare_df["importcount"] - compare_df["hist_avg"]) / compare_df["hist_avg"]
    compare_df["direction"] = compare_df["change_ratio"].apply(lambda x: "上升" if x >= 0 else "降低")
    compare_df["change_pct"] = (compare_df["change_ratio"] * 100).round(2)

    threshold_ratio = float(threshold_pct) / 100.0
    alerts_df = compare_df[compare_df["change_ratio"].abs() >= threshold_ratio].copy()

    if alerts_df.empty:
        st.markdown(
            f"<div class='alert-box'>✅ {title_latest_day} {pd.to_datetime(latest_date).strftime('%Y/%m/%d')} 未发现异常</div>",
            unsafe_allow_html=True
        )
    else:
        st.markdown(
            f"<div class='alert-box'><b>🚨 异常报警（阈值 {threshold_pct}%）</b><br/>",
            unsafe_allow_html=True
        )
        for _, row in alerts_df.sort_values(by="change_ratio", key=lambda s: s.abs(), ascending=False).iterrows():
            date_str = pd.to_datetime(row["date"]).strftime("%Y/%m/%d")
            msg = f"<span class='alert-exclam'>！</span><span class='alert-line'>{row['provider_label']} 在 {date_str} 的汇入量异常{row['direction']}</span>"
            st.markdown(msg, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

        show_cols = ["providerid", "provider_label", "date", "importcount", "hist_avg", "change_pct", "direction"]
        pretty_df = alerts_df[show_cols].copy()
        pretty_df = pretty_df.rename(columns={
            "providerid": "ProviderId",
            "provider_label": "Provider",
            "date": "日期",
            "importcount": "最新日汇入量",
            "hist_avg": "过往均值",
            "change_pct": "变化百分比",
            "direction": "方向"
        })
        pretty_df["日期"] = pd.to_datetime(pretty_df["日期"]).dt.strftime("%Y/%m/%d")
        with st.expander("查看异常明细", expanded=False):
            st.dataframe(pretty_df, use_container_width=True)
            export_excel(pretty_df, f"{filename_prefix}_异常_{pd.to_datetime(latest_date).strftime('%Y%m%d')}.xlsx")

def prepare_import_data(import_files, provider_map):
    """
    读取上传的 import xlsx 文件，合并 provider_map，解析日期，构造 provider_label，
    并且忽略 ProviderId == 'BBPIRCh' 的全部数据。
    """
    import_data = pd.DataFrame()
    if import_files:
        for file in import_files:
            df = pd.read_excel(file)
            df = normalize_columns(df)
            date_str = os.path.splitext(file.name)[0]
            df["date"] = date_str
            import_data = pd.concat([import_data, df], ignore_index=True)

    if import_data.empty:
        return import_data

    if "providerid" not in import_data.columns or "importcount" not in import_data.columns:
        st.error("汇入量文件需包含列：ProviderId 与 ImportCount")
        st.stop()

    # 统一字符串并过滤 BBPIRCh
    import_data["providerid_str"] = import_data["providerid"].astype(str)
    import_data = import_data[import_data["providerid_str"] != "BBPIRCh"].copy()

    # 合并 Provider 名称
    if not provider_map.empty:
        import_data = import_data.merge(provider_map, on="providerid", how="left")

    # Provider 显示标签
    if "providername" in import_data.columns:
        import_data["provider_label"] = import_data["providername"].where(import_data["providername"].notna(),
                                                                         import_data["providerid_str"])
    else:
        import_data["provider_label"] = import_data["providerid_str"]

    # 解析日期
    import_data["date_parsed"] = parse_date_series(import_data["date"])
    if import_data["date_parsed"].isna().any():
        st.warning("发现无效日期记录，已忽略")
        import_data = import_data[~import_data["date_parsed"].isna()].copy()

    return import_data

# =========================
# Provider 映射
# =========================
provider_map = pd.DataFrame()
if provider_file:
    try:
        provider_map = pd.read_excel(provider_file)
        provider_map = normalize_columns(provider_map)
        if "providername" not in provider_map.columns or "providerid" not in provider_map.columns:
            st.error("Provider 映射需包含：ProviderName 与 ProviderId")
            st.stop()
        provider_map = provider_map.drop_duplicates(subset=["providerid"]).reset_index(drop=True)
    except Exception as e:
        st.error(f"读取 Provider 映射失败：{e}")
        st.stop()

# =========================
# 汇入量与节假日
# =========================
import_data = prepare_import_data(import_files, provider_map)
holidays_set = load_holidays_set(holidays_file)

# =========================
# 功能 1：单日分析
# =========================
if menu == "功能 1：单日分析":
    st.markdown("<div class='page-title'>🗓️📊 单日分析</div>", unsafe_allow_html=True)
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        dates = sorted(import_data["date_parsed"].dropna().unique())
        if not dates:
            st.warning("无有效日期")
        else:
            date_strs = [pd.to_datetime(d).strftime("%Y-%m-%d") for d in dates]
            selected_date_str = st.selectbox("选择日期", date_strs)
            selected_date = pd.to_datetime(selected_date_str).date()

            day_data = import_data[import_data["date_parsed"] == selected_date]
            provider_counts = (day_data.groupby("provider_label", dropna=False)["importcount"]
                               .sum().reset_index().sort_values(by="importcount", ascending=False))
            provider_counts = provider_counts.rename(columns={"provider_label": "Provider", "importcount": "汇入数量"})

            st.dataframe(provider_counts, use_container_width=True)
            fig = px.bar(provider_counts, x="Provider", y="汇入数量", title=f"{selected_date_str} 汇入数量")
            st.plotly_chart(fig, use_container_width=True)

            export_excel(provider_counts, f"单日_汇入_{selected_date_str}.xlsx")

# =========================
# 功能 2：仅工作日
# =========================
elif menu == "功能 2：仅工作日":
    st.markdown("<div class='page-title'>🧑‍💼📈 仅工作日</div>", unsafe_allow_html=True)
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        all_providers = sorted(import_data["provider_label"].dropna().unique().tolist())
        whitelist = st.sidebar.multiselect("Provider 筛选", options=all_providers, default=[])

        df = import_data.copy()
        if whitelist:
            df = df[df["provider_label"].isin(whitelist)].copy()

        df["weekday"] = pd.to_datetime(df["date_parsed"]).dt.weekday
        df = df[df["weekday"] < 5].copy()  # 周一~周五

        use_holidays = st.checkbox("排除节假日", value=True, key="workdays_holiday_toggle")
        if use_holidays:
            if len(holidays_set) > 0:
                df = df[~df["date_parsed"].isin(holidays_set)].copy()
            else:
                st.info("未提供节假日文件")

        if df.empty:
            st.warning("无数据")
        else:
            # 报警
            daily_import = (df.groupby(["providerid", "provider_label", "date_parsed"], dropna=False)["importcount"]
                            .sum().reset_index().rename(columns={"date_parsed": "date"}))
            anomaly_alerts_block(daily_import, "最新工作日", "仅工作日", alert_threshold_pct)

            # 趋势图（每日日期全部显示）
            trend_data = (df.groupby(["date_parsed", "provider_label"], dropna=False)["importcount"]
                          .sum().reset_index().rename(columns={"date_parsed": "date"}))
            trend_data["date_str"] = pd.to_datetime(trend_data["date"]).dt.strftime("%Y-%m-%d")

            provider_total = trend_data.groupby("provider_label")["importcount"].sum().sort_values(ascending=False)
            providers_sorted = provider_total.index.tolist()
            group_size = 10
            provider_groups = [providers_sorted[i:i+group_size] for i in range(0, len(providers_sorted), group_size)]

            all_group_data = []
            for idx, group in enumerate(provider_groups, start=1):
                st.markdown(f"<div class='section-title'>📈 第 {idx} 组</div>", unsafe_allow_html=True)
                group_data = trend_data[trend_data["provider_label"].isin(group)].copy()
                all_group_data.append(group_data)
                fig = px.line(group_data, x="date_str", y="importcount", color="provider_label",
                              labels={"provider_label": "Provider", "importcount": "汇入数量", "date_str": "日期"},
                              title="")
                fig.update_xaxes(type="category", categoryorder="category ascending", tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)

            if all_group_data:
        # ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓ 这里务必保持缩进 ↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓↓
                export_excel(pd.concat(all_group_data), "趋势_仅工作日.xlsx")

# =========================
# 功能 3：仅周末
# =========================
elif menu == "功能 3：仅周末":
    st.markdown("<div class='page-title'>🛌📈 仅周末</div>", unsafe_allow_html=True)
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        all_providers = sorted(import_data["provider_label"].dropna().unique().tolist())
        whitelist = st.sidebar.multiselect("Provider 筛选", options=all_providers, default=[], key="wl_weekends")

        df = import_data.copy()
        if whitelist:
            df = df[df["provider_label"].isin(whitelist)].copy()

        df["weekday"] = pd.to_datetime(df["date_parsed"]).dt.weekday
        df = df[df["weekday"] >= 5].copy()  # 周六/周日

        if df.empty:
            st.warning("无数据")
        else:
            daily_import = (df.groupby(["providerid", "provider_label", "date_parsed"], dropna=False)["importcount"]
                            .sum().reset_index().rename(columns={"date_parsed": "date"}))
            anomaly_alerts_block(daily_import, "最新周末日", "仅周末", alert_threshold_pct)

            trend_data = (df.groupby(["date_parsed", "provider_label"], dropna=False)["importcount"]
                          .sum().reset_index().rename(columns={"date_parsed": "date"}))
            trend_data["date_str"] = pd.to_datetime(trend_data["date"]).dt.strftime("%Y-%m-%d")

            provider_total = trend_data.groupby("provider_label")["importcount"].sum().sort_values(ascending=False)
            providers_sorted = provider_total.index.tolist()
            group_size = 10
            provider_groups = [providers_sorted[i:i+group_size] for i in range(0, len(providers_sorted), group_size)]

            all_group_data = []
            for idx, group in enumerate(provider_groups, start=1):
                st.markdown(f"<div class='section-title'>📈 第 {idx} 组</div>", unsafe_allow_html=True)
                group_data = trend_data[trend_data["provider_label"].isin(group)].copy()
                all_group_data.append(group_data)
                fig = px.line(group_data, x="date_str", y="importcount", color="provider_label",
                              labels={"provider_label": "Provider", "importcount": "汇入数量", "date_str": "日期"},
                              title="")
                fig.update_xaxes(type="category", categoryorder="category ascending", tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)

            if all_group_data:
                export_excel(pd.concat(all_group_data), "趋势_仅周末.xlsx")

# =========================
# 功能 4：全部数据
# =========================
elif menu == "功能 4：全部数据":
    st.markdown("<div class='page-title'>📚📈 全部数据</div>", unsafe_allow_html=True)
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        all_providers = sorted(import_data["provider_label"].dropna().unique().tolist())
        whitelist = st.sidebar.multiselect("Provider 筛选", options=all_providers, default=[], key="wl_all")

        df = import_data.copy()
        if whitelist:
            df = df[df["provider_label"].isin(whitelist)].copy()

        if df.empty:
            st.warning("无数据")
        else:
            daily_import = (df.groupby(["providerid", "provider_label", "date_parsed"], dropna=False)["importcount"]
                            .sum().reset_index().rename(columns={"date_parsed": "date"}))
            anomaly_alerts_block(daily_import, "最新一天", "全部数据", alert_threshold_pct)

            trend_data = (df.groupby(["date_parsed", "provider_label"], dropna=False)["importcount"]
                          .sum().reset_index().rename(columns={"date_parsed": "date"}))
            trend_data["date_str"] = pd.to_datetime(trend_data["date"]).dt.strftime("%Y-%m-%d")

            provider_total = trend_data.groupby("provider_label")["importcount"].sum().sort_values(ascending=False)
            providers_sorted = provider_total.index.tolist()
            group_size = 10
            provider_groups = [providers_sorted[i:i+group_size] for i in range(0, len(providers_sorted), group_size)]

            all_group_data = []
            for idx, group in enumerate(provider_groups, start=1):
                st.markdown(f"<div class='section-title'>📈 第 {idx} 组</div>", unsafe_allow_html=True)
                group_data = trend_data[trend_data["provider_label"].isin(group)].copy()
                all_group_data.append(group_data)
                fig = px.line(group_data, x="date_str", y="importcount", color="provider_label",
                              labels={"provider_label": "Provider", "importcount": "汇入数量", "date_str": "日期"},
                              title="")
                fig.update_xaxes(type="category", categoryorder="category ascending", tickangle=-45)
                st.plotly_chart(fig, use_container_width=True)

            if all_group_data:
