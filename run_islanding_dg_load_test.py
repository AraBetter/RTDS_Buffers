# run_islanding_dg_load_test.py  (CTRL version, Option 1 ramp + W_DETECTED interlocks)
from __future__ import annotations

import time
import math
from collections import deque
from typing import Dict, Optional, Union

from Comms.data_bus import DataBus, ts_now
from Comms.gtnet_channel import GtnetChannel
from Comms.channel_specs import CHANNEL_1, CHANNEL_2, CHANNEL_3, CHANNEL_4

from DG_controller import DGController, DGControllerConfig, DGMeasurements

Number = Union[int, float]


# -------------------------
# Helpers
# -------------------------

def _get(bus: DataBus, ch: str, key: str, default=None):
    tf = bus.get_meas(ch)
    if not tf:
        return default
    return tf.data.get(key, default)

def _as_float(x) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None

def _as_int(x) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except Exception:
        return None

def pulse_cmd(bus: DataBus, ch_obj, ch_name: str, key: str, pulse_s: float = 0.10, note: str = ""):
    """
    Pushbutton pulse: set key=1 then key=0.
    Use for REM_CHKRESET, REM_RESETGEN, REM_LGFLTx, etc.
    """
    bus.emit_cmd(ch_obj, ch_name, {key: 1}, note=(note or f"pulse {key} ON"))
    time.sleep(pulse_s)
    bus.emit_cmd(ch_obj, ch_name, {key: 0}, note=(note or f"pulse {key} OFF"))

def wait_for_remote_mode(bus: DataBus, timeout_s: float = 10.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        mode = _get(bus, "CH1", "MODE", None)
        if mode is not None and int(mode) == 1:
            return True
        time.sleep(0.05)
    return False

def wait_for_breaker(bus: DataBus, ch: str, key: str, target: int, timeout_s: float = 10.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        v = _get(bus, ch, key, None)
        if v is not None and int(v) == int(target):
            return True
        time.sleep(0.05)
    return False

def wait_for_grid_pos(bus: DataBus, target: int, timeout_s: float = 10.0) -> bool:
    """
    Wait until CH1.GRID == target.
    target: 1 = grid breaker closed, 0 = grid breaker open
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        v = _get(bus, "CH1", "GRID", None)
        if v is not None and int(v) == int(target):
            return True
        time.sleep(0.05)
    return False

def wait_wdet_clear(bus: DataBus, *, timeout_s: float = 10.0, dt: float = 0.05) -> bool:
    """
    Interlock: wait until W_DETECTED == 0.
    If signal is missing/None, treat as NOT cleared (fail safe).
    """
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        wdet = _as_int(_get(bus, "CH3", "W_DETECTED", None))
        if wdet == 0:
            return True
        time.sleep(dt)
    return False

def ensure_dg_closed(bus: DataBus, ch3, *,
                     max_tries: int = 5,
                     reset_pulse_s: float = 0.1,
                     wait_after_try_s: float = 0.30,
                     brk_timeout_s: float = 3.0,
                     wdet_timeout_s: float = 5.0,
                     dt: float = 0.05) -> bool:
    """
    Try to get the DG breaker to close by:
      - re-asserting REM_BLOCKGEN=1
      - (INTERLOCK) verify W_DETECTED==0 BEFORE pulsing REM_RESETGEN
      - pulsing REM_RESETGEN
      - waiting for BRKGEN==1

    If W_DETECTED does not clear in time, that try is skipped (fail-safe).
    """
    for i in range(1, max_tries + 1):
        bus.emit_cmd(ch3, "CH3", {"REM_BLOCKGEN": 1, "REM_RESETGEN": 0}, note=f"DG ensure: arm try {i}")

        # NEW: Interlock before the pushbutton pulse
        if not wait_wdet_clear(bus, timeout_s=wdet_timeout_s, dt=dt):
            wdet = _get(bus, "CH3", "W_DETECTED", None)
            print(f"[{ts_now()}] DG ensure try {i}: W_DETECTED did not clear (W_DETECTED={wdet}). Skipping RESETGEN pulse.")
            time.sleep(wait_after_try_s)
            continue

        pulse_cmd(bus, ch3, "CH3", "REM_RESETGEN", pulse_s=reset_pulse_s, note=f"DG ensure: RESETGEN try {i}")
        time.sleep(wait_after_try_s)

        if wait_for_breaker(bus, "CH3", "BRKGEN", target=1, timeout_s=brk_timeout_s):
            return True

    return False


# -------------------------
# Stability checks
# -------------------------

def _max_step(values):
    if len(values) < 2:
        return 0.0
    m = 0.0
    for i in range(1, len(values)):
        dv = abs(values[i] - values[i - 1])
        if dv > m:
            m = dv
    return m

def _stable_window_ok(samples: list[dict], *, limits: dict) -> bool:
    if len(samples) < 10:
        return False

    # Required signals presence check
    for name, cfg in limits.items():
        if cfg.get("required", False):
            vals = [s[name] for s in samples if s.get(name) is not None]
            if len(vals) < max(5, len(samples) // 2):
                return False

    ov = [s.get("OVERLOADED") for s in samples if s.get("OVERLOADED") is not None]
    if ov and max(ov) != 0:
        return False

    wd = [s.get("W_DETECTED") for s in samples if s.get("W_DETECTED") is not None]
    if wd and max(wd) != 0:
        return False

    for name, cfg in limits.items():
        vals = [s[name] for s in samples if s.get(name) is not None]
        if not vals:
            continue

        if isinstance(vals[0], int):
            if "step" in cfg:
                if _max_step([float(v) for v in vals]) > cfg["step"]:
                    return False
            continue

        span = max(vals) - min(vals)
        step = _max_step(vals)

        if "span" in cfg and span > cfg["span"]:
            return False
        if "step" in cfg and step > cfg["step"]:
            return False

    return True

def wait_until_stable(bus: DataBus, *,
                      dt: float = 0.05,
                      window_s: float = 2.0,
                      timeout_s: float = 30.0,
                      limits: dict) -> bool:
    n = max(10, int(window_s / dt))
    q = deque(maxlen=n)

    t0 = time.time()
    while time.time() - t0 < timeout_s:
        snap = {
            "PGRID": _as_float(_get(bus, "CH1", "PGRID", None)),
            "QGRID": _as_float(_get(bus, "CH1", "QGRID", None)),
            "N680RMSPU": _as_float(_get(bus, "CH4", "N680RMSPU", None)),
            "Pmeas": _as_float(_get(bus, "CH2", "Pmeas", None)),

            "PGEN": _as_float(_get(bus, "CH3", "PGEN", None)),
            "QGEN": _as_float(_get(bus, "CH3", "QGEN", None)),
            "PMACH": _as_float(_get(bus, "CH3", "PMACH", None)),
            "QMACH": _as_float(_get(bus, "CH3", "QMACH", None)),
            "SMACH": _as_float(_get(bus, "CH3", "SMACH", None)),
            "GENRMSPU": _as_float(_get(bus, "CH3", "GENRMSPU", None)),
            "WPU": _as_float(_get(bus, "CH3", "WPU", None)),

            "W_DETECTED": _as_int(_get(bus, "CH3", "W_DETECTED", None)),

            "BRKGEN": _as_int(_get(bus, "CH3", "BRKGEN", None)),
            "OVERLOADED": _as_int(_get(bus, "CH3", "OVERLOADED", None)),
        }

        q.append(snap)

        if _stable_window_ok(list(q), limits=limits):
            return True

        time.sleep(dt)

    return False


# -------------------------
# Optional hook: apply data degradation
# -------------------------

def degrade_dg_measurements(raw: Dict[str, Number]) -> Dict[str, Number]:
    return raw


# -------------------------
# Main test (CTRL)
# -------------------------

def main():
    bus = DataBus()

    #bus = DataBus(rx_log_channels={"CH3"})

    # bus = DataBus(
    #     rx_log_channels={"CH3"},
    #     rx_log_fields={
    #         "CH3": {"BRKGEN", "SMACH", "OVERLOADED", "WPU", "W_DETECTED"}
    #     }
    # )

    ch1 = GtnetChannel(CHANNEL_1, on_meas=lambda m: bus.update_meas("CH1", m))
    ch2 = GtnetChannel(CHANNEL_2, on_meas=lambda m: bus.update_meas("CH2", m))
    ch3 = GtnetChannel(CHANNEL_3, on_meas=lambda m: bus.update_meas("CH3", m))
    ch4 = GtnetChannel(CHANNEL_4, on_meas=lambda m: bus.update_meas("CH4", m))

    ch1.start(); ch2.start(); ch3.start(); ch4.start()
    print(f"[{ts_now()}] CH1–CH4 started.")

    COMMS_SETTLE_S = 5.0
    print(f"[{ts_now()}] Waiting {COMMS_SETTLE_S:.1f}s for communications to stabilize...")
    time.sleep(COMMS_SETTLE_S)

    STABILITY_LIMITS = {
        "PGRID": {"span": 0.02, "step": 0.01, "required": True},
        "QGRID": {"span": 0.02, "step": 0.01, "required": True},
        "N680RMSPU": {"span": 0.01, "step": 0.005, "required": True},
        "Pmeas": {"span": 0.03, "step": 0.015, "required": True},

        "PGEN": {"span": 0.03, "step": 0.015, "required": True},
        "QGEN": {"span": 0.03, "step": 0.015, "required": True},
        "PMACH": {"span": 0.03, "step": 0.015, "required": True},
        "QMACH": {"span": 0.03, "step": 0.015, "required": True},
        "SMACH": {"span": 0.03, "step": 0.015, "required": True},
        "GENRMSPU": {"span": 0.01, "step": 0.005, "required": True},
        "WPU": {"span": 0.002, "step": 0.001, "required": True},

        "W_DETECTED": {"step": 0.5, "required": False},
        "BRKGEN": {"step": 0.5, "required": False},
    }

    dt = 0.05

    P0 = 0.80
    Q0 = 0.003

    stable_window_s = 2.0
    stable_timeout_s = 40.0

    P_end = 2.0
    ramp_s = 20.0
    Q_hold = Q0

    BESS_BLOCK_ENABLE = 1
    BESS_BREAKER_CLOSED = 1

    DG_RESET_PULSE_S = 0.1
    DG_CLOSE_TRIES = 5
    DG_CLOSE_TIMEOUT_S = 3.0

    GRID_CLOSE_TIMEOUT_S = 5.0
    GRID_OPEN_TIMEOUT_S = 5.0

    dg_cfg = DGControllerConfig(
        SMACH_limit=1.50,
        SMACH_margin=0.05,
        Wref_base=1.0,
        Pref_base=0.0035,
        wref_rate_up=0.005,
        wref_rate_down=0.01,
        pref_rate_up=0.10,
        pref_rate_down=0.15,
    )
    dg_ctrl = DGController(dg_cfg)

    ramp_t0: Optional[float] = None

    try:
        if not wait_for_remote_mode(bus, timeout_s=10.0):
            raise RuntimeError("MODE != 1 (REMOTE). Put the plant in REMOTE mode and rerun.")

        print(f"[{ts_now()}] Sending defaults (Load, BESS, DG) and connecting grid...")

        bus.emit_cmd(ch4, "CH4", {"REM_PLOAD": float(P0), "REM_QLOAD": float(Q0)}, note="defaults: load")

        bus.emit_cmd(ch1, "CH1", {"REM_GRID": 1}, note="defaults: grid CONNECT")
        if not wait_for_grid_pos(bus, target=1, timeout_s=GRID_CLOSE_TIMEOUT_S):
            grid = _get(bus, "CH1", "GRID", None)
            raise RuntimeError(f"Grid breaker did not CLOSE (GRID != 1). GRID={grid}. Aborting.")

        bus.emit_cmd(
            ch2, "CH2",
            {"REM_BESSBRK": int(BESS_BREAKER_CLOSED),
             "REM_Preftest": 0.3, "REM_Qreftest": 0.0, "REM_CHKRESET": 0},
            note="defaults: BESS close breaker (no REM_BLOCK)"
        )
        pulse_cmd(bus, ch2, "CH2", "REM_CHKRESET", pulse_s=0.10, note="BESS CHKRESET (no block)")

        bus.emit_cmd(
            ch3, "CH3",
            {"REM_Wref": float(dg_cfg.Wref_base),
             "REM_PREF": float(dg_cfg.Pref_base), "REM_RESETGEN": 0},
            note="defaults: DG setpoints (no REM_BLOCKGEN)"
        )

        print(f"[{ts_now()}] Waiting for STABLE (grid-connected, before enabling DG/BESS blocks) ...")
        ok = wait_until_stable(
            bus, dt=dt, window_s=stable_window_s, timeout_s=stable_timeout_s, limits=STABILITY_LIMITS
        )
        if not ok:
            raise RuntimeError("Did not reach stable condition while grid-connected (system signals still moving).")

        print(f"[{ts_now()}] Stable grid-connected. Verifying W_DETECTED==0 before enabling DG...")

        if not wait_wdet_clear(bus, timeout_s=10.0, dt=dt):
            wdet = _get(bus, "CH3", "W_DETECTED", None)
            raise RuntimeError(f"W_DETECTED did not clear before enabling DG (W_DETECTED={wdet}). Aborting.")

        print(f"[{ts_now()}] W_DETECTED cleared. Now enabling DG, then BESS, then islanding...")

        bus.emit_cmd(
            ch3, "CH3",
            {"REM_BLOCKGEN": 1,
             "REM_Wref": float(dg_cfg.Wref_base),
             "REM_PREF": float(dg_cfg.Pref_base),
             "REM_RESETGEN": 0},
            note="enable DG (REM_BLOCKGEN)"
        )

        print(f"[{ts_now()}] Ensuring DG engages (waiting for BRKGEN==1)...")
        dg_ok = ensure_dg_closed(
            bus, ch3,
            max_tries=DG_CLOSE_TRIES,
            reset_pulse_s=DG_RESET_PULSE_S,
            brk_timeout_s=DG_CLOSE_TIMEOUT_S,
            wdet_timeout_s=5.0,
            dt=dt
        )
        brkgen = _get(bus, "CH3", "BRKGEN", None)
        print(f"[{ts_now()}] BRKGEN after ensure = {brkgen}")
        if not dg_ok:
            raise RuntimeError("DG did not engage (BRKGEN never became 1). Aborting: will NOT island or ramp load.")

        bus.emit_cmd(
            ch2, "CH2",
            {"REM_BLOCK": int(BESS_BLOCK_ENABLE),
             "REM_BESSBRK": int(BESS_BREAKER_CLOSED),
             "REM_Preftest": 0.3, "REM_Qreftest": 0.0, "REM_CHKRESET": 0},
            note="enable BESS (REM_BLOCK)"
        )
        pulse_cmd(bus, ch2, "CH2", "REM_CHKRESET", pulse_s=0.10, note="BESS CHKRESET (after block)")

        ok = wait_until_stable(
            bus, dt=dt, window_s=stable_window_s, timeout_s=stable_timeout_s, limits=STABILITY_LIMITS
        )
        if not ok:
            raise RuntimeError("Did not reach stable condition after enabling DG/BESS (still moving).")

        print(f"[{ts_now()}] Preconditions satisfied. Now ISLANDING (open grid breaker).")

        if not wait_for_breaker(bus, "CH3", "BRKGEN", target=1, timeout_s=1.0):
            raise RuntimeError("Safety block: BRKGEN != 1 right before islanding. Aborting.")

        bus.emit_cmd(ch1, "CH1", {"REM_GRID": 0}, note="ISLAND: open grid breaker")
        if not wait_for_grid_pos(bus, target=0, timeout_s=GRID_OPEN_TIMEOUT_S):
            grid = _get(bus, "CH1", "GRID", None)
            raise RuntimeError(f"Grid breaker did not OPEN (GRID != 0). GRID={grid}. Aborting.")

        print(f"[{ts_now()}] Waiting for STABLE (islanded) ...")
        ok = wait_until_stable(
            bus, dt=dt, window_s=stable_window_s, timeout_s=stable_timeout_s, limits=STABILITY_LIMITS
        )
        if not ok:
            raise RuntimeError("Did not reach stable condition after islanding (system still moving).")

        grid_pos = _get(bus, "CH1", "GRID", None)
        brkgen = _get(bus, "CH3", "BRKGEN", None)
        grid_ok = (grid_pos is not None and int(grid_pos) == 1)
        dg_ok = (brkgen is not None and int(brkgen) == 1)

        if not (dg_ok or grid_ok):
            raise RuntimeError(
                f"Safety block: load ramp forbidden (DG and grid both disconnected). GRID={grid_pos}, BRKGEN={brkgen}."
            )

        print(f"[{ts_now()}] Stable and interlocks satisfied. Starting load ramp (CTRL).")

        trip_cause = None
        OVL_SUSTAIN_COUNT = 3
        ovl_count = 0
        prev_brkgen = _as_int(_get(bus, "CH3", "BRKGEN", None))

        steps = max(1, int(ramp_s / dt))
        ramp_rate = (float(P_end) - float(P0)) / max(0.001, float(ramp_s))

        ramp_t0 = time.time()

        FREQ_PERTURB_ENABLE = 0
        P_PERTURB = 0.02
        F_PERTURB_HZ = 0.5

        WDET_SUSTAIN_COUNT = 3
        wdet_count = 0

        for k in range(steps + 1):
            grid_pos_i = _as_int(_get(bus, "CH1", "GRID", None))
            brkgen_i = _as_int(_get(bus, "CH3", "BRKGEN", None))
            overloaded_i = _as_int(_get(bus, "CH3", "OVERLOADED", None))

            if overloaded_i == 1:
                ovl_count += 1
            else:
                ovl_count = 0

            if prev_brkgen == 1 and brkgen_i == 0 and trip_cause is None:
                trip_cause = "OVERLOAD" if ovl_count >= OVL_SUSTAIN_COUNT else "UNKNOWN"
            prev_brkgen = brkgen_i

            grid_ok = (grid_pos_i == 1)
            dg_ok = (brkgen_i == 1)

            if not (dg_ok or grid_ok):
                endurance_s = (time.time() - ramp_t0) if ramp_t0 is not None else float("nan")
                if trip_cause == "OVERLOAD":
                    raise RuntimeError(
                        f"Protection trip during ramp: DG disconnected due to OVERLOAD. "
                        f"Endurance={endurance_s:.3f}s since ramp start."
                    )
                elif trip_cause == "UNKNOWN":
                    raise RuntimeError(
                        f"Safety stop during ramp: DG disconnected but OVERLOADED not sustained at trip. "
                        f"(GRID={grid_pos_i}, BRKGEN={brkgen_i}, OVERLOADED={overloaded_i}) "
                        f"Endurance={endurance_s:.3f}s since ramp start."
                    )
                else:
                    raise RuntimeError(
                        f"Safety stop during ramp: DG and grid both disconnected without detected DG trip edge. "
                        f"(GRID={grid_pos_i}, BRKGEN={brkgen_i}, OVERLOADED={overloaded_i}) "
                        f"Endurance={endurance_s:.3f}s since ramp start."
                    )

            wdet_i = _as_int(_get(bus, "CH3", "W_DETECTED", None))
            if wdet_i == 1:
                wdet_count += 1
            else:
                wdet_count = 0

            if wdet_count >= WDET_SUSTAIN_COUNT:
                endurance_s = (time.time() - ramp_t0) if ramp_t0 is not None else float("nan")
                raise RuntimeError(
                    f"Safety stop during ramp: W_DETECTED sustained {WDET_SUSTAIN_COUNT} samples. "
                    f"Endurance={endurance_s:.3f}s since ramp start."
                )

            p_cmd = float(P0) + float(k) * float(ramp_rate) * float(dt)
            if p_cmd > float(P_end):
                p_cmd = float(P_end)

            if FREQ_PERTURB_ENABLE and ramp_t0 is not None:
                t_rel = time.time() - ramp_t0
                p_cmd += float(P_PERTURB) * math.sin(2.0 * math.pi * float(F_PERTURB_HZ) * t_rel)
                p_cmd = max(0.0, p_cmd)

            bus.emit_cmd(ch4, "CH4", {"REM_PLOAD": float(p_cmd), "REM_QLOAD": float(Q_hold)}, note="ramp load")

            tf3 = bus.get_meas("CH3")
            if not tf3:
                time.sleep(dt)
                continue



            raw = dict(tf3.data)
            dg_in = degrade_dg_measurements(raw)

            meas = DGMeasurements(
                SMACH=_as_float(dg_in.get("SMACH")),
                GENRMSPU=_as_float(dg_in.get("GENRMSPU")),
                OVERLOADED=_as_int(dg_in.get("OVERLOADED")),
                WPU=_as_float(dg_in.get("WPU")),
            )

            cmds = dg_ctrl.update(meas, dt, now=time.time())

            bus.emit_cmd(
                ch3, "CH3",
                {"REM_BLOCKGEN": int(cmds.REM_BLOCKGEN),
                 "REM_Wref": float(cmds.REM_Wref),
                 "REM_PREF": float(cmds.REM_PREF),
                 "REM_RESETGEN": int(cmds.REM_RESETGEN)},
                note="DG controller"
            )

            print(
                f"[{ts_now()}] "
                f"PLOADcmd={p_cmd:.3f} QLOADcmd={Q_hold:.3f} | "
                f"GRID={_get(bus,'CH1','GRID',None)} BRKGEN={_get(bus,'CH3','BRKGEN',None)} | "
                f"WPU={meas.WPU if meas.WPU is not None else float('nan'):.4f} "
                f"Vpu={meas.GENRMSPU if meas.GENRMSPU is not None else float('nan'):.3f} "
                f"SMACH={meas.SMACH if meas.SMACH is not None else float('nan'):.3f} "
                f"OV={meas.OVERLOADED if meas.OVERLOADED is not None else -1} "
                f"WDET={wdet_i if wdet_i is not None else -1} || "
                f"TX: Wref={float(cmds.REM_Wref):.4f} Pref={float(cmds.REM_PREF):.3f} "
                f"Block={int(cmds.REM_BLOCKGEN)} Reset={int(cmds.REM_RESETGEN)}"
            )

            time.sleep(dt)

        endurance_s = (time.time() - ramp_t0) if ramp_t0 is not None else float("nan")
        print(f"[{ts_now()}] Ramp finished without collapse. Endurance={endurance_s:.3f}s since ramp start.")
        print(f"[{ts_now()}] Holding last load 5s...")
        time.sleep(5.0)

    except RuntimeError:
        if ramp_t0 is not None:
            endurance_s = time.time() - ramp_t0
            print(f"[{ts_now()}] TEST STOP. Endurance={endurance_s:.3f}s since ramp start.")

        # FINAL RX SNAPSHOT (CH3) AT STOP
        tf3 = bus.get_meas("CH3")
        if tf3:
            rx = tf3.data
            rx_keys = ("BRKGEN", "SMACH", "OVERLOADED", "WPU", "W_DETECTED")
            rx_snap = {k: rx.get(k, None) for k in rx_keys}
            print(f"[{tf3.t_str}][RX-ARMED][CH3][FINAL] {rx_snap}")

        raise
    finally:
        ch1.stop(); ch2.stop(); ch3.stop(); ch4.stop()
        print(f"[{ts_now()}] Stopped CH1–CH4.")


if __name__ == "__main__":
    main()


