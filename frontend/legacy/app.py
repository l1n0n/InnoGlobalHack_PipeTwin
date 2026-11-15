from math import ceil
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st


DEMO_RESULT = Path(__file__).resolve().parents[2] / "tests" / "result.csv"
COLUMNS = ["id", "segment_id", "thickness_pred"]

st.set_page_config(page_title="PipeTwin", layout="wide")
st.title("PipeTwin · Прогноз толщины стенки")
st.write("Выберите трубопровод, чтобы увидеть прогноз вдоль трассы и участки для первоочередной проверки.")
with st.sidebar:
    st.header("Данные прогноза")
    uploaded = st.file_uploader("Загрузить result.csv", type="csv")
    st.caption("Столбцы CSV: id, segment_id, thickness_pred. Толщина в миллиметрах.")
if uploaded is None:
    st.caption("Демо на синтетических данных")

try:
    result = pd.read_csv(
        DEMO_RESULT if uploaded is None else uploaded,
        usecols=COLUMNS,
        dtype={"id": "string", "segment_id": "Int64", "thickness_pred": "float64"},
        keep_default_na=False,
    )[COLUMNS]
except (OSError, ValueError) as error:
    st.error(f"Не удалось прочитать CSV. {error}")
    st.stop()

if (
    result.empty
    or result["id"].str.strip().eq("").any()
    or result["segment_id"].isna().any()
    or result["segment_id"].lt(0).any()
    or not result["thickness_pred"].between(0, float("inf"), inclusive="left").all()
    or result.duplicated(["id", "segment_id"]).any()
):
    st.error("Проверьте значения и уникальность пар id и segment_id.")
    st.stop()

pipe = st.selectbox("Трубопровод", sorted(result["id"].unique()))
segments = result.loc[result["id"].eq(pipe)].sort_values("segment_id").copy()
segments["Начало, м"] = segments["segment_id"] * 10
segments["Толщина, мм"] = segments["thickness_pred"]
table_columns = ["segment_id", "Начало, м", "Толщина, мм"]
if uploaded is None:
    passports = pd.read_csv(DEMO_RESULT.with_name("passport.csv")).set_index("id")
    segments["Конец, м"] = (segments["Начало, м"] + 10).clip(
        upper=passports.at[pipe, "length_m"]
    )
    table_columns.insert(2, "Конец, м")
priority = segments.nsmallest(ceil(len(segments) / 10), "thickness_pred")

total, minimum, first = st.columns(3)
total.metric("Сегментов в прогнозе", len(segments))
minimum.metric("Минимальная толщина", f"{priority.iloc[0]['Толщина, мм']:.2f} мм")
first.metric("Начало самого тонкого сегмента", f"{priority.iloc[0]['Начало, м']} м")

threshold = st.number_input(
    "Порог толщины, мм", min_value=0.01,
    value=6.5 if uploaded is None else None, step=0.1,
    key=f"threshold-{uploaded is None}",
    help="Для реальной трубы используйте порог из инженерного расчёта.",
)
if uploaded is None:
    st.caption("В демо начальный порог 6,5 мм выбран для примера.")
overview, inspection, scenario = st.tabs(
    ["Профиль толщины", "Сегменты для проверки", "Прогноз по годам"]
)
with overview:
    st.subheader("Толщина вдоль трассы")
segments["Статус"] = "Остальные сегменты"
segments.loc[priority.index, "Статус"] = "Очередь проверки"
if threshold is not None:
    at_threshold = segments["Толщина, мм"].le(threshold)
    segments.loc[at_threshold, "Статус"] = "На пороге или ниже"
    overview.write(f"**На пороге или ниже: {at_threshold.sum()} из {len(segments)} сегментов.**")
overview.caption(
    "Чем краснее линия, тем тоньше стенка на этой трубе. Красный пунктир показывает заданный порог. "
    "Наведите курсор на точку для подробностей. Колёсико мыши меняет масштаб."
)
profile = alt.Chart(segments).encode(
    x=alt.X("Начало, м:Q", title="Дистанция от начала трубы, м"),
    y=alt.Y("Толщина, мм:Q", scale=alt.Scale(zero=False)),
    tooltip=[
        alt.Tooltip("segment_id:Q", title="Сегмент"),
        alt.Tooltip("Начало, м:Q"),
        alt.Tooltip("Толщина, мм:Q", format=".2f"),
        alt.Tooltip("Статус:N"),
    ],
)
lowest = segments["Толщина, мм"].min()
highest = segments["Толщина, мм"].max()
thickness_scale = alt.Scale(
    domain=[lowest, (lowest + highest) / 2, highest]
    if lowest < highest else [lowest - 1, lowest, lowest + 1],
    range=["#dc2626", "#f59e0b", "#2563a8"],
    clamp=True,
)
thickness_color = alt.Color(
    "Толщина, мм:Q", scale=thickness_scale,
    legend=alt.Legend(orient="top", gradientLength=240) if lowest < highest else None,
)
links = segments.assign(
    next_distance=segments["Начало, м"].shift(-1),
    next_thickness=segments["Толщина, мм"].shift(-1),
).dropna(subset=["next_distance"])
links["Средняя толщина, мм"] = (links["Толщина, мм"] + links["next_thickness"]) / 2
line = alt.Chart(links).mark_rule(strokeWidth=4).encode(
    x="Начало, м:Q",
    x2="next_distance:Q",
    y="Толщина, мм:Q",
    y2="next_thickness:Q",
    color=alt.Color("Средняя толщина, мм:Q", scale=thickness_scale, legend=None),
)
chart = line + profile.mark_circle(opacity=1).encode(
    color=thickness_color,
    size=alt.condition(alt.datum["Статус"] == "На пороге или ниже", alt.value(125), alt.value(65)),
)
if threshold is not None:
    chart += alt.Chart(pd.DataFrame({"Порог, мм": [threshold]})).mark_rule(
        color="#dc2626", strokeDash=[6, 4],
    ).encode(y=alt.Y("Порог, мм:Q", title="Толщина, мм"), tooltip=["Порог, мм:Q"])
overview.altair_chart(
    chart.resolve_scale(color="independent").properties(height=520).interactive(),
    width="stretch",
)

with inspection:
    st.subheader("Очередь проверки")
    st.caption(
        f"Показано {len(priority)} из {len(segments)} сегментов. "
        "Выбираем 10% с наименьшей прогнозной толщиной и округляем число сегментов вверх."
    )
    column_config = {
        "segment_id": "Сегмент",
        "Толщина, мм": st.column_config.NumberColumn(format="%.2f"),
    }
    st.dataframe(segments.loc[priority.index, table_columns + ["Статус"]],
                 column_config=column_config, hide_index=True)
    if threshold is not None and at_threshold.any():
        with st.expander("Все сегменты на пороге или ниже"):
            st.dataframe(
                segments.loc[at_threshold, table_columns].sort_values("Толщина, мм"),
                column_config=column_config, hide_index=True,
            )
    with st.expander("Все сегменты выбранной трубы"):
        st.dataframe(segments[table_columns], column_config=column_config, hide_index=True)

with scenario:
    st.subheader("Как будет меняться толщина")
    st.caption(
        "Сценарная оценка при постоянной скорости утонения. Для решения о дальнейшей "
        "эксплуатации нужна инженерная оценка допустимой толщины и состояния трубы."
    )
    segment = st.selectbox(
        "Сегмент для расчёта",
        segments["segment_id"].tolist(),
        index=segments["segment_id"].tolist().index(priority.iloc[0]["segment_id"]),
        format_func=lambda number: f"№ {number} · от {number * 10} м",
    )
    initial = segments.loc[segments["segment_id"].eq(segment), "thickness_pred"].iloc[0]
    estimated_rate = None
    if uploaded is None:
        inspections = pd.read_csv(DEMO_RESULT.with_name("vtd.csv"), parse_dates=["date"])
        history = inspections.loc[
            inspections["id"].eq(pipe) & inspections["Distance"].floordiv(10).eq(segment)
        ].groupby("date")["Residual Wall Thickness"].min().tail(2)
        if len(history) == 2 and history.iloc[0] > history.iloc[1]:
            years = (history.index[1] - history.index[0]).days / 365.25
            estimated_rate = float((history.iloc[0] - history.iloc[1]) / years)
            st.caption(
                f"По двум ВТД: {history.iloc[0]:.2f} → {history.iloc[1]:.2f} мм "
                f"за {years:.2f} года. Сравниваются минимумы в одном сегменте."
            )

    rate_col, horizon_col = st.columns(2)
    rate = rate_col.number_input(
        "Скорость утонения, мм/год", min_value=0.0, value=estimated_rate,
        step=0.05, format="%.3f", key=f"rate-{uploaded is None}-{pipe}-{segment}",
        help="При наличии двух ВТД в демо поле заполняется автоматически. Значение можно изменить.",
    )
    horizon = horizon_col.slider("Горизонт, лет", 1, 30, 5)
    if rate is None:
        st.info("Введите скорость утонения, чтобы построить сценарий для этого сегмента.")
    else:
        metrics = st.columns(3)
        metrics[0].metric("Толщина на дату прогноза", f"{initial:.2f} мм")
        metrics[1].metric(f"Толщина через {horizon} лет", f"{max(0, initial - rate * horizon):.2f} мм")
        if threshold is None:
            time_to_threshold = "Укажите порог"
        elif initial <= threshold:
            time_to_threshold = "Порог уже достигнут"
        elif rate == 0:
            time_to_threshold = "Порог не достигается"
        else:
            time_to_threshold = f"{(initial - threshold) / rate:.2f} года"
        metrics[2].metric("Время до порога", time_to_threshold)
        checkpoints = sorted({0, horizon, *range(1, horizon)})
        if rate > 0 and 0 < initial / rate < horizon:
            checkpoints.append(initial / rate)
        trajectory = pd.DataFrame({
            "Лет после даты прогноза": sorted(checkpoints),
            "Толщина, мм": [max(0, initial - rate * year) for year in sorted(checkpoints)],
        })
        forecast = alt.Chart(trajectory).mark_line(point=True).encode(
            x="Лет после даты прогноза:Q", y="Толщина, мм:Q",
            tooltip=["Лет после даты прогноза:Q", "Толщина, мм:Q"],
        )
        if threshold is not None:
            forecast += alt.Chart(pd.DataFrame({"Порог": [threshold]})).mark_rule(
                color="#b45309", strokeDash=[6, 4],
            ).encode(y=alt.Y("Порог:Q", title="Толщина, мм"), tooltip=["Порог:Q"])
        st.altair_chart(forecast.properties(height=240), width="stretch")

st.download_button(
    "Скачать прогноз по всем трубам",
    result.to_csv(index=False).encode("utf-8"),
    file_name="result.csv",
    mime="text/csv",
)
