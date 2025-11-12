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
provider_file = st.sidebar.file_uploader("上传 Provider 映射文件（ProviderName 和 ProviderId）", type=["xlsx"])
import_files = st.sidebar.file_uploader("上传汇入量文件（可多选）", type=["xlsx"], accept_multiple_files=True)

# =========================
# 工具函数
# =========================
def export_excel(df, filename):
    output = BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Sheet1')
    st.download_button(label="📥 下载分析结果", data=output.getvalue(),
                       file_name=filename, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

def normalize_columns(df):
    df.columns = [col.strip().lower() for col in df.columns]
    return df

def parse_date_series(s):
    """
    将字符串解析为日期（YYYY-MM-DD / YYYYMMDD 等），失败返回 NaT。
    这里使用文件名（不含扩展名）作为日期来源。
    """
    dt = pd.to_datetime(s, errors='coerce')
    return dt.dt.date

# =========================
# Step 1: 处理 Provider 映射文件
# =========================
provider_map = pd.DataFrame()
if provider_file:
    provider_map = pd.read_excel(provider_file)
    provider_map = normalize_columns(provider_map)
    if "providername" not in provider_map.columns or "providerid" not in provider_map.columns:
        st.error("Provider 映射文件必须包含列：ProviderName 和 ProviderId")
        st.stop()
    provider_map = provider_map.drop_duplicates(subset=["providerid"]).reset_index(drop=True)

# =========================
# Step 2: 处理汇入量文件
# =========================
import_data = pd.DataFrame()
if import_files:
    for file in import_files:
        df = pd.read_excel(file)
        df = normalize_columns(df)
        # 使用文件名（不含扩展名）作为日期来源
        date_str = os.path.splitext(file.name)[0]
        df["date"] = date_str
        import_data = pd.concat([import_data, df], ignore_index=True)

    if "providerid" not in import_data.columns or "importcount" not in import_data.columns:
        st.error("汇入量文件必须包含列：ProviderId 和 ImportCount")
        st.stop()

    # 合并 Provider 名称
    if not provider_map.empty:
        import_data = import_data.merge(provider_map, on="providerid", how="left")

    # 解析日期
    import_data["date_parsed"] = parse_date_series(import_data["date"])
    # 提示无效日期并剔除
    if import_data["date_parsed"].isna().any():
        st.warning("⚠️ 检测到部分汇入量文件的日期无法从文件名解析，请确认文件名为有效日期格式（如 2025-01-31 或 20250131）。这些记录将被忽略。")
        import_data = import_data[~import_data["date_parsed"].isna()].copy()

# =========================
# 功能 1：单日 Provider 汇入数量分析
# =========================
if menu == "功能 1：单日 Provider 汇入数量分析":
    st.subheader("📌 单日 Provider 汇入数量分析")
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        # 使用解析后的日期
        avail_dates = sorted(import_data["date_parsed"].dropna().unique())
        if len(avail_dates) == 0:
            st.warning("没有可用的有效日期数据。")
        else:
            date_strs = [pd.to_datetime(d).strftime("%Y-%m-%d") for d in avail_dates]
            selected_date_str = st.selectbox("选择日期", date_strs)
            selected_date = pd.to_datetime(selected_date_str).date()

            day_data = import_data[import_data["date_parsed"] == selected_date]
            provider_counts = (
                day_data.groupby("providername", dropna=False)["importcount"]
                .sum()
                .reset_index()
                .sort_values(by="importcount", ascending=False)
            )

            st.write("各 Provider 汇入数量：")
            st.dataframe(provider_counts, use_container_width=True)

            fig = px.bar(provider_counts, x="providername", y="importcount",
                         labels={"providername": "Provider", "importcount": "汇入数量"},
                         title=f"{selected_date_str} 各 Provider 汇入数量")
            st.plotly_chart(fig, use_container_width=True)

            export_excel(provider_counts, f"Provider_Import_{selected_date_str}.xlsx")

# =========================
# 功能 2：多日趋势分析（分组显示）+ 顶部异常报警
# =========================
elif menu == "功能 2：多日趋势分析（分组显示）":
    st.subheader("📌 多日趋势分析（按 Provider 分组，每组 10 个）")
    if import_data.empty:
        st.warning("请上传汇入量文件")
    else:
        # ------- 顶部异常报警（整合原功能4） -------
        # 1) 按天汇总
        daily_import = (
            import_data.groupby(["providerid", "providername", "date_parsed"], dropna=False)["importcount"]
            .sum()
            .reset_index()
            .rename(columns={"date_parsed": "date"})
        )

        if daily_import["date"].isna().all():
            st.warning("没有有效的日期数据，无法计算异常。")
        else:
            # 2) 全局最新日期
            global_latest_date = daily_import["date"].max()

            latest_df = daily_import[daily_import["date"] == global_latest_date].copy()
            history_df = daily_import[daily_import["date"] < global_latest_date].copy()

            # 顶部样式
            st.markdown("""
                <style>
                .alert-exclam { color: #d00000; font-weight: 800; font-size: 18px; margin-right: 6px; }
                .alert-line { font-size: 16px; line-height: 1.6; }
                .alert-box { padding: 10px 12px; background-color: #fff5f5; border-left: 4px solid #d00000; border-radius: 6px; margin-bottom: 16px; }
                </style>
            """, unsafe_allow_html=True)

            # 仅当有历史数据时才计算报警
            if history_df.empty:
                st.markdown(
                    f"<div class='alert-box'>仅有最新一天数据（{pd.to_datetime(global_latest_date).strftime('%Y/%m/%d')}），缺少过往数据用于对比，暂无法报警。</div>",
                    unsafe_allow_html=True
                )
            else:
                # 3) 历史日均值
                hist_mean = (
                    history_df.groupby(["providerid", "providername"], dropna=False)["importcount"]
                    .mean()
                    .reset_index()
                    .rename(columns={"importcount": "hist_avg"})
                )

                # 4) 合并最新日数据
                compare_df = pd.merge(
                    latest_df[["providerid", "providername", "date", "importcount"]],
                    hist_mean,
                    on=["providerid", "providername"],
                    how="left"
                )

                # 5) 仅保留历史均值 > 500 的 Provider
                compare_df = compare_df[compare_df["hist_avg"] > 500].copy()

                # 6) 变化比例与方向
                compare_df["change_ratio"] = (compare_df["importcount"] - compare_df["hist_avg"]) / compare_df["hist_avg"]
                compare_df["direction"] = compare_df["change_ratio"].apply(lambda x: "上升" if x >= 0 else "降低")
                compare_df["change_pct"] = (compare_df["change_ratio"] * 100).round(2)

                # 7) 阈值：|变化比例| >= 50%
                alerts_df = compare_df[compare_df["change_ratio"].abs() >= 0.5].copy()

                # 顶部展示
                if alerts_df.empty:
                    st.markdown(
                        f"<div class='alert-box'>✅ 最新一天（{pd.to_datetime(global_latest_date).strftime('%Y/%m/%d')}）未发现异常波动（满足条件的 Provider）。</div>",
                        unsafe_allow_html=True
                    )
                else:
                    st.markdown(
                        f"<div class='alert-box'><b>🚩 异常报警（最新一天：{pd.to_datetime(global_latest_date).strftime('%Y/%m/%d')}）</b><br/>",
                        unsafe_allow_html=True
                    )
                    for _, row in alerts_df.sort_values(by="change_ratio", key=lambda s: s.abs(), ascending=False).iterrows():
                        date_str = pd.to_datetime(row["date"]).strftime("%Y/%m/%d")
                        provider = row["providername"] if pd.notna(row["providername"]) else str(row["providerid"])
                        msg = f"<span class='alert-exclam'>！</span><span class='alert-line'>{provider} 在 {date_str} 的汇入量异常{row['direction']}</span>"
                        st.markdown(msg, unsafe_allow_html=True)
                    st.markdown("</div>", unsafe_allow_html=True)

                    # 可选：异常明细与下载（折叠）
                    with st.expander("查看异常明细（含下载）", expanded=False):
                        show_cols = ["providerid", "providername", "date", "importcount", "hist_avg", "change_pct", "direction"]
                        pretty_df = alerts_df[show_cols].copy()
                        pretty_df = pretty_df.rename(columns={
                            "providerid": "ProviderId",
                            "providername": "ProviderName",
                            "date": "最新日期",
                            "importcount": "最新日汇入量",
                            "hist_avg": "过往日均值",
                            "change_pct": "变化百分比(%)",
                            "direction": "方向"
                        })
                        pretty_df["最新日期"] = pd.to_datetime(pretty_df["最新日期"]).dt.strftime("%Y/%m/%d")
                        st.dataframe(pretty_df, use_container_width=True)
                        export_excel(pretty_df, f"Import_Anomaly_{pd.to_datetime(global_latest_date).strftime('%Y%m%d')}.xlsx")

        # ------- 趋势图（分组显示） -------
        provider_total = import_data.groupby("providername", dropna=False)["importcount"].sum().sort_values(ascending=False)
        providers_sorted = [p for p in provider_total.index.tolist()]
        group_size = 10
        provider_groups = [providers_sorted[i:i+group_size] for i in range(0, len(providers_sorted), group_size)]

        trend_data = (
            import_data.groupby(["date_parsed", "providername"], dropna=False)["importcount"]
            .sum()
            .reset_index()
            .rename(columns={"date_parsed": "date"})
            .sort_values(by="date")
        )

        all_group_data = []
        for idx, group in enumerate(provider_groups, start=1):
            st.markdown(f"### 第 {idx} 组 Provider 趋势图")
            group_data = trend_data[trend_data["providername"].isin(group)]
            all_group_data.append(group_data)
            fig = px.line(group_data, x="date", y="importcount", color="providername",
                          title=f"Provider 趋势分析（第 {idx} 组）", markers=True)
            st.plotly_chart(fig, use_container_width=True)

        if all_group_data:
            export_excel(pd.concat(all_group_data), "Provider_Trend.xlsx")
