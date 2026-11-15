"""Python interface to NeqSim's NORSOK M-506 corrosion model."""

import math


def co2_rate(
    temperature_c: float,
    total_pressure_bar: float,
    co2_mol_percent: float,
    ph: float,
    flow_velocity_ms: float,
    diameter_mm: float,
) -> float:
    """Return the NeqSim corrected general corrosion rate in mm/year."""
    for value, minimum, maximum, name in (
        (temperature_c, 5, 150, "Температура, °C"),
        (total_pressure_bar, 1, 1000, "Абсолютное давление, бар"),
        (co2_mol_percent, 0, 100, "CO₂, мол. %"),
        (ph, 3.5, 6.5, "pH"),
        (flow_velocity_ms, 0, 30, "Скорость потока, м/с"),
        (diameter_mm, 10, 2000, "Внутренний диаметр, мм"),
    ):
        if not minimum <= value <= maximum:
            raise ValueError(f"{name}: допустимый диапазон {minimum}–{maximum}.")
    co2_fraction = co2_mol_percent / 100
    if not 0 < total_pressure_bar * co2_fraction <= 10:
        raise ValueError("Парциальное давление CO₂ должно быть больше 0 и не выше 10 бар.")

    try:
        from neqsim import jneqsim
    except (ImportError, OSError, RuntimeError) as error:
        raise RuntimeError(
            "NeqSim недоступен. Установите зависимости и 64-битную Java. См. README."
        ) from error

    model = jneqsim.process.corrosion.NorsokM506CorrosionRate()
    model.setTemperatureCelsius(temperature_c)
    model.setTotalPressureBara(total_pressure_bar)
    model.setCO2MoleFraction(co2_fraction)
    model.setActualPH(ph)
    model.setFlowVelocityMs(flow_velocity_ms)
    model.setPipeDiameterM(diameter_mm / 1000)
    model.calculate()
    rate = float(model.getCorrectedCorrosionRate())
    if not math.isfinite(rate) or rate < 0:
        raise ValueError("NeqSim вернул некорректную скорость. Проверьте входные условия.")
    return rate


def remaining_thickness(start_mm: float, rate: float, years: float) -> float:
    return max(0.0, start_mm - rate * years)
