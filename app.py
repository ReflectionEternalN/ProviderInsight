
import streamlit as st
import pandas as pd
import plotly.express as px
import os
from io import BytesIO
from datetime import date as date_cls

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
alert_threshold_pct = st.sidebar.number_input("报警阈值（%）", min_value=5.0, value=50.0, step=1.0, format="%.1f")

# 汇入总结忽略名单（仅功能 2）
EXCLUDED_PROVIDERS_IN_SUMMARY = {"一点资讯-视频", "虎牙视频"}

# 全局统计忽略名单（所有功能页都会剔除）
EXCLUDED_PROVIDERS_GLOBAL = {"NOWNEWS(簡中)"}

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

def prepare_import_data(import_files, provider_map):
    """
    读取上传的 import xlsx，合并 provider_map，解析日期，构造 provider_label，
    并忽略 ProviderId == 'BBPIRCh' 的全部数据。
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

    # Provider 显示标签（优先 providername，否则用 providerid_str）
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

def filter_cn_named(df: pd.DataFrame) -> pd.DataFrame:
    """
    仅保留有中文 ProviderName 的记录（用于功能 2/3/4）。
    无映射时（无 providername 列）返回空，以满足“只有 ID 的排除不用统计”的要求。
    """
    if df.empty:
        return df
    if "providername" not in df.columns:
        return df.iloc[0:0].copy()
    mask = df["providername"].notna() & (df["providername"].astype(str).str.strip() != "")
    return df[mask].copy()

def filter_excluded_providers(df: pd.DataFrame, excluded_names: set) -> pd.DataFrame:
    """全局剔除指定 Provider（按 provider_label 匹配）。"""
    if df.empty or "provider_label" not in df.columns:
        return df
    return df[~df["provider_label"].astype(str).str.strip().isin(excluded_names)].copy()

# =========================
# ⭐ 关键：构造完整日期列表 & 补齐零值趋势数据
# =========================
def make_date_list(min_d: date_cls, max_d: date_cls, policy: str, use_holidays: bool, holidays_set: set) -> list:
    """
    根据场景生成完整日期列表：
    - policy: 'workdays'（仅工作日）、'weekends'（仅周末）、'all'（所有日）
    - use_holidays: 仅在 workdays 下有效，True 则剔除节假日
    """
    if pd.isna(min_d) or pd.isna(max_d):
        return []
    all_days = pd.date_range(min_d, max_d, freq="D").date
    if policy == "workdays":
        days = [d for d in all_days if pd.Timestamp(d).weekday() < 5]
        if use_holidays and holidays_set:
            days = [d for d in days if d not in holidays_set]
        return days
    elif policy == "weekends":
        return [d for d in all_days if pd.Timestamp(d).weekday() >= 5]
    else:
        return list(all_days)

def build_complete_trend_data(df: pd.DataFrame, date_list: list, providers_list: list) -> pd.DataFrame:
    """用“完整日期列表 × Provider 列表”做笛卡尔补齐，缺失填 0，并添加分类轴用的 date_str。"""
    if df.empty or len(date_list) == 0 or len(providers_list) == 0:
        return pd.DataFrame(columns=["date_parsed", "provider_label", "importcount", "date", "date_str"])

    agg = (df.groupby(["date_parsed", "provider_label"], dropna=False)["importcount"]
             .sum().reset_index())

    grid = pd.MultiIndex.from_product([date_list, providers_list], names=["date_parsed", "provider_label"]).to_frame(index=False)
    trend = grid.merge(agg, on=["date_parsed", "provider_label"], how="left")
    trend["importcount"] = trend["importcount"].fillna(0)

    trend["date"] = trend["date_parsed"]
    trend["date_str"] = pd.to_datetime(trend["date"]).dt.strftime("%Y-%m-%d")
    return trend

def plot_grouped_trends(trend_data: pd.DataFrame, providers_sorted: list, date_list: list, group_size: int, export_name: str):
    """分组绘图（X 轴每日显示；强制显示所有刻度），并导出 Excel。"""
    if trend_data.empty:
        st.warning("无趋势数据")
        return

    x_categories = [pd.to_datetime(d).strftime("%Y-%m-%d") for d in date_list]

    provider_groups = [providers_sorted[i:i+group_size] for i in range(0, len(providers_sorted), group_size)]
    all_group_data = []
    for idx, group in enumerate(provider_groups, start=1):
        st.markdown(f"<div class='section-title'>📈 第 {idx} 组</div>", unsafe_allow_html=True)
        group_data = trend_data[trend_data["provider_label"].isin(group)].copy()
        all_group_data.append(group_data)
        fig = px.line(group_data, x="date_str", y="importcount", color="provider_label",
                      labels={"provider_label": "Provider", "importcount": "汇入数量", "date_str": "日期"},
                      title="")
        fig.update_xaxes(
            type="category",
            categoryorder="array",
            categoryarray=x_categories,
            tickmode="array",
            tickvals=x_categories,
            tickangle=-60
        )
        st.plotly_chart(fig, use_container_width=True)

    if all_group_data:
        export_excel(pd.concat(all_group_data), export_name)

# =========================
# 报警逻辑（最新一天 vs 前一天，含“降至 0”例外）
# ✅ 调整：报警明细与报警列表均按“前一日汇入量”从高到低排序
# =========================
def anomaly_alerts_block_dod_latest_gt500(
    df_daily: pd.DataFrame,
    title_latest_day: str,
    filename_prefix: str,
    threshold_pct: float,
    show_summary: bool = False,
    excluded_providers_in_summary: set = None
):
    """
    DoD 报警：
    候选 Provider：
      (1) 最新一天 > 500；或
      (2) 最新一天 = 0 且 前一天 > 0（降至 0 例外）
    条件：|最新 - 前一日| / 前一日 ≥ 阈值（前一日=0且最新>0 → ∞；前一日>0且最新=0 → -100%）
    文案：🚨异常报警 XX Provider最新一天汇入量异常升高/减少
    show_summary=True 时，显示“最新一天汇入总结”（忽略指定 Provider；明细按最新日降序）
    """
    if df_daily.empty or df_daily["date"].isna().all():
        st.warning("无可用日期数据")
        return

    dates_sorted = sorted(df_daily["date"].dropna().unique())
    if len(dates_sorted) < 2:
        st.markdown("<div class='alert-box'>数据不足 2 天，无法进行与前一天的比较</div>", unsafe_allow_html=True)
        return

    latest_date = dates_sorted[-1]
    prev_date = dates_sorted[-2]

    latest_df = (df_daily[df_daily["date"] == latest_date]
                 .groupby(["providerid", "provider_label"], dropna=False)["importcount"]
                 .sum().reset_index().rename(columns={"importcount": "latest_count"}))
    prev_df = (df_daily[df_daily["date"] == prev_date]
               .groupby(["providerid", "provider_label"], dropna=False)["importcount"]
               .sum().reset_index().rename(columns={"importcount": "prev_count"}))

    comp = pd.merge(latest_df, prev_df, on=["providerid", "provider_label"], how="outer").fillna(0)

    normal_candidates = comp["latest_count"] > 500
    drop_to_zero_candidates = (comp["latest_count"] == 0) & (comp["prev_count"] > 0)
    comp_alert = comp[normal_candidates | drop_to_zero_candidates].copy()

    st.markdown(
        f"<div class='alert-box'><b>🚨 异常报警（{title_latest_day}：{pd.to_datetime(latest_date).strftime('%Y/%m/%d')} 对比 {pd.to_datetime(prev_date).strftime('%Y/%m/%d')}，阈值 {threshold_pct:.1f}%）</b></div>",
        unsafe_allow_html=True
    )

    if comp_alert.empty:
        st.markdown("<div class='alert-box'>✅ 未发现异常波动（满足条件的 Provider）</div>", unsafe_allow_html=True)
    else:
        def calc_ratio(row):
            prev, now = row["prev_count"], row["latest_count"]
            if prev == 0:
                return float("inf") if now > 0 else 0.0
            return (now - prev) / prev

        comp_alert["change_ratio"] = comp_alert.apply(calc_ratio, axis=1)
        comp_alert["direction"] = comp_alert.apply(lambda r: "升高" if r["latest_count"] >= r["prev_count"] else "减少", axis=1)
        threshold_ratio = float(threshold_pct) / 100.0
        alerts_df = comp_alert[comp_alert["change_ratio"].abs() >= threshold_ratio].copy()

        if alerts_df.empty:
            st.markdown("<div class='alert-box'>✅ 未发现异常波动（满足条件的 Provider）</div>", unsafe_allow_html=True)
        else:
            # ✅ 报警列表：按“前一日汇入量”从高到低输出
            alerts_df_sorted = alerts_df.sort_values(by="prev_count", ascending=False)

            for _, row in alerts_df_sorted.iterrows():
                msg = f"🚨异常报警 {row['provider_label']}最新一天汇入量异常{row['direction']}"
                st.markdown(f"<div class='alert-line'>{msg}</div>", unsafe_allow_html=True)

            # ✅ 报警明细：按“前一日汇入量”从高到低展示与导出
            pretty = alerts_df_sorted.rename(columns={
                "providerid": "ProviderId",
                "provider_label": "Provider",
                "latest_count": "最新日汇入量",
                "prev_count": "前一日汇入量",
            }).copy()
            pretty["最新日期"] = pd.to_datetime(latest_date).strftime("%Y/%m/%d")
            pretty["前一日期"] = pd.to_datetime(prev_date).strftime("%Y/%m/%d")
            pretty["变化比例"] = pretty["change_ratio"].apply(lambda x: "∞" if x == float("inf") else f"{(x*100):.2f}%")

            cols = ["ProviderId", "Provider", "前一日期", "前一日汇入量", "最新日期", "最新日汇入量", "变化比例", "direction"]
            pretty = pretty[cols]
            with st.expander("查看报警明细", expanded=False):
                st.dataframe(pretty, use_container_width=True)
                export_excel(pretty, f"{filename_prefix}_报警明细_{pd.to_datetime(latest_date).strftime('%Y%m%d')}.xlsx")

    if show_summary:
        full_latest = latest_df.rename(columns={"latest_count": "最新日汇入量"})
        full_prev   = prev_df.rename(columns={"prev_count": "前一日汇入量"})
        full = pd.merge(full_latest, full_prev, on=["providerid", "provider_label"], how="outer").fillna(0)

        if excluded_providers_in_summary:
            full = full[~full["provider_label"].astype(str).str.strip().isin(excluded_providers_in_summary)].copy()

        full["变化量"] = full["最新日汇入量"] - full["前一日汇入量"]
        full["方向"]  = full["变化量"].apply(lambda x: "升高" if x > 0 else ("减少" if x < 0 else "持平"))

        inc_df = full[full["变化量"] > 0]
        dec_df = full[full["变化量"] < 0]

        inc_count = int(inc_df.shape[0])
        dec_count = int(dec_df.shape[0])
        inc_total = int(inc_df["变化量"].sum()) if not inc_df.empty else 0
        dec_total = int((-dec_df["变化量"]).sum()) if not dec_df.empty else 0
        net_change = int(full["变化量"].sum())
        net_label = "升高" if net_change > 0 else ("减少" if net_change < 0 else "持平")

        st.markdown("**🧾 最新一天汇入总结：**")
        st.markdown(f"- 升高 Provider 数：**{inc_count}**，总计升高数量：**{inc_total}**")
        st.markdown(f"- 降低 Provider 数：**{dec_count}**，总计降低数量：**{dec_total}**")
        st.markdown(f"- 净变化：**{net_label} {abs(net_change)}**")

        summary_detail = full.rename(columns={
            "providerid": "ProviderId",
            "provider_label": "Provider",
        }).copy()
        summary_detail["最新日期"] = pd.to_datetime(latest_date).strftime("%Y/%m/%d")
        summary_detail["前一日期"] = pd.to_datetime(prev_date).strftime("%Y/%m/%d")
        summary_detail = summary_detail.sort_values(by="最新日汇入量", ascending=False)
        summary_detail = summary_detail[["ProviderId", "Provider", "前一日期", "前一日汇入量", "最新日期", "最新日汇入量", "变化量", "方向"]]

        with st.expander("查看汇入总结明细", expanded=False):
            st.dataframe(summary_detail, use_container_width=True)
            export_excel(summary_detail, f"{filename_prefix}_汇入总结明细_{pd.to_datetime(latest_date).strftime('%Y%m%d')}.xlsx")

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

# ===== 全局统计忽略：NOWNEWS(簡中) =====
import_data = filter_excluded_providers(import_data, EXCLUDED_PROVIDERS_GLOBAL)

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
# 功能 2：仅工作日（最新日排序分组，每 10 个一组 + 汇入总结）
# =========================
elif menu == "功能 2：仅工作日":
    st.markdown("<div class='page-title'>🧑‍💼📈 仅工作日</div>", unsafe_allow_html=True)
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        df = import_data.copy()
        df["weekday"] = pd.to_datetime(df["date_parsed"]).dt.weekday
        df = df[df["weekday"] < 5].copy()

        use_holidays = st.checkbox("排除节假日", value=True, key="workdays_holiday_toggle")
        if use_holidays:
            if len(holidays_set) > 0:
                df = df[~df["date_parsed"].isin(holidays_set)].copy()
            else:
                st.info("未提供节假日文件")

        # 仅中文 ProviderName
        df = filter_cn_named(df)
        if df.empty:
            st.warning("无数据")
        else:
            all_providers = sorted(df["provider_label"].dropna().unique().tolist())
            whitelist = st.sidebar.multiselect("Provider 筛选", options=all_providers, default=[])
            if whitelist:
                df = df[df["provider_label"].isin(whitelist)].copy()

            if df.empty:
                st.warning("无数据")
            else:
                daily_import = (df.groupby(["providerid", "provider_label", "date_parsed"], dropna=False)["importcount"]
                                .sum().reset_index().rename(columns={"date_parsed": "date"}))
                anomaly_alerts_block_dod_latest_gt500(
                    daily_import, "最新工作日", "仅工作日", alert_threshold_pct,
                    show_summary=True, excluded_providers_in_summary=EXCLUDED_PROVIDERS_IN_SUMMARY
                )

                # 完整日期（仅工作日，考虑节假日开关）
                min_d, max_d = df["date_parsed"].min(), df["date_parsed"].max()
                date_list = make_date_list(min_d, max_d, policy="workdays", use_holidays=use_holidays, holidays_set=holidays_set)

                providers_list = sorted(df["provider_label"].dropna().unique().tolist())
                trend_complete = build_complete_trend_data(df, date_list, providers_list)

                latest_date = date_list[-1] if len(date_list) > 0 else None
                if latest_date is not None:
                    latest_day_counts = (trend_complete[trend_complete["date"] == latest_date]
                                         .groupby("provider_label", dropna=False)["importcount"]
                                         .sum().sort_values(ascending=False))
                    providers_sorted = latest_day_counts.index.tolist()
                else:
                    providers_sorted = providers_list

                plot_grouped_trends(trend_complete, providers_sorted, date_list, group_size=10, export_name="趋势_仅工作日.xlsx")

# =========================
# 功能 3：仅周末（总量排序分组）
# =========================
elif menu == "功能 3：仅周末":
    st.markdown("<div class='page-title'>🛌📈 仅周末</div>", unsafe_allow_html=True)
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        df = import_data.copy()
        df["weekday"] = pd.to_datetime(df["date_parsed"]).dt.weekday
        df = df[df["weekday"] >= 5].copy()

        # 仅中文 ProviderName
        df = filter_cn_named(df)
        if df.empty:
            st.warning("无数据")
        else:
            all_providers = sorted(df["provider_label"].dropna().unique().tolist())
            whitelist = st.sidebar.multiselect("Provider 筛选", options=all_providers, default=[], key="wl_weekends")
            if whitelist:
                df = df[df["provider_label"].isin(whitelist)].copy()

            if df.empty:
                st.warning("无数据")
            else:
                daily_import = (df.groupby(["providerid", "provider_label", "date_parsed"], dropna=False)["importcount"]
                                .sum().reset_index().rename(columns={"date_parsed": "date"}))
                anomaly_alerts_block_dod_latest_gt500(daily_import, "最新周末日", "仅周末", alert_threshold_pct)

                min_d, max_d = df["date_parsed"].min(), df["date_parsed"].max()
                date_list = make_date_list(min_d, max_d, policy="weekends", use_holidays=False, holidays_set=set())

                providers_list = sorted(df["provider_label"].dropna().unique().tolist())
                trend_complete = build_complete_trend_data(df, date_list, providers_list)

                provider_total = trend_complete.groupby("provider_label", dropna=False)["importcount"].sum().sort_values(ascending=False)
                providers_sorted = provider_total.index.tolist()

                plot_grouped_trends(trend_complete, providers_sorted, date_list, group_size=10, export_name="趋势_仅周末.xlsx")

# =========================
# 功能 4：全部数据（总量排序分组）
# =========================
elif menu == "功能 4：全部数据":
    st.markdown("<div class='page-title'>📚📈 全部数据</div>", unsafe_allow_html=True)
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        df = import_data.copy()
        # 仅中文 ProviderName
        df = filter_cn_named(df)
        if df.empty:
            st.warning("无数据")
        else:
            all_providers = sorted(df["provider_label"].dropna().unique().tolist())
            whitelist = st.sidebar.multiselect("Provider 筛选", options=all_providers, default=[], key="wl_all")
            if whitelist:
                df = df[df["provider_label"].isin(whitelist)].copy()

            if df.empty:
                st.warning("无数据")
            else:
                daily_import = (df.groupby(["providerid", "provider_label", "date_parsed"], dropna=False)["importcount"]
                                .sum().reset_index().rename(columns={"date_parsed": "date"}))
                anomaly_alerts_block_dod_latest_gt500(daily_import, "最新一天", "全部数据", alert_threshold_pct)

                min_d, max_d = df["date_parsed"].min(), df["date_parsed"].max()
                date_list = make_date_list(min_d, max_d, policy="all", use_holidays=False, holidays_set=set())

                providers_list = sorted(df["provider_label"].dropna().unique().tolist())
                trend_complete = build_complete_trend_data(df, date_list, providers_list)

                provider_total = trend_complete.groupby("provider_label", dropna=False)["importcount"].sum().sort_values(ascending=False)
                providers_sorted = provider_total.index.tolist()

                plot_grouped_trends(trend_complete, providers_sorted, date_list, group_size=10, export_name="趋势_全部数据.xlsx")
