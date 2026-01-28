import time
from typing import Dict, Union, Optional

from Comms.data_bus import DataBus, ts_now
from Comms.channel_specs import CHANNEL_1, CHANNEL_2, CHANNEL_3, CHANNEL_4
from Comms.gtnet_channel import GtnetChannel

from Comms.comms_disruptions import (
    ChannelDisruptor,
    DataDegradationConfig,
    AutonomyDegradationConfig,
)

Number = Union[int, float]


# -----------------------------
# Local “helper” implementations
# Route ALL TX through tx.emit_cmd(...)
# where tx is ChannelDisruptor (normal pass-through unless enabled).
# -----------------------------
def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def set_selector_tx(tx, channel: GtnetChannel, ch_name: str, sig: str, val: int, *, note: str = "") -> None:
    _require(val in (0, 1), f"{sig}: selector must be 0 or 1 (got {val})")
    tx.emit_cmd(channel, ch_name, {sig: int(val)}, note=note)


def set_dial_tx(tx, channel: GtnetChannel, ch_name: str, sig: str, val: int, *, lo: int, hi: int, note: str = "") -> int:
    _require(isinstance(val, int), f"{sig}: dial must be int (got {type(val)})")
    _require(lo <= val <= hi, f"{sig}: dial out of range [{lo},{hi}] (got {val})")
    tx.emit_cmd(channel, ch_name, {sig: int(val)}, note=note)
    return val


def set_slider_tx(tx, channel: GtnetChannel, ch_name: str, sig: str, val: float, *, lo: float, hi: float, note: str = "") -> float:
    _require(lo <= hi, f"{sig}: invalid range lo>hi")
    tx_val = float(val)
    if tx_val < lo:
        tx_val = lo
    elif tx_val > hi:
        tx_val = hi
    tx.emit_cmd(channel, ch_name, {sig: tx_val}, note=note)
    return tx_val


def pb_pulse_tx(tx, channel: GtnetChannel, ch_name: str, sig: str, *, pulse_s: float = 0.25, note: str = "") -> None:
    _require(pulse_s > 0, "pulse_s must be > 0")
    tx.emit_cmd(channel, ch_name, {sig: 1}, note=(note or f"{sig} pulse start"))
    time.sleep(pulse_s)
    tx.emit_cmd(channel, ch_name, {sig: 0}, note=(note or f"{sig} pulse end"))


# -----------------------------
# Gate + status
# -----------------------------
def mode_is_remote_from_bus(bus: DataBus) -> bool:
    tf = bus.get_meas("CH1")
    if not tf:
        return False
    try:
        return int(tf.data.get("MODE", 0)) == 1
    except Exception:
        return False


def print_status(bus: DataBus) -> None:
    snap = bus.snapshot_all()

    def _line(ch: str, s: str) -> None:
        print(f"[{ts_now()}][{ch}] {s}")

    tf = snap.get("CH1")
    if tf:
        m = tf.data
        print(
            f"[{tf.t_str}][CH1] Seq={m.get('NewDataSeq_1_')} Ready={m.get('ReadyToSend_1_')} MODE={m.get('MODE')} "
            f"PGRID={float(m.get('PGRID', 0.0)):.3f} QGRID={float(m.get('QGRID', 0.0)):.3f}"
        )
    else:
        _line("CH1", "No telemetry yet.")

    tf = snap.get("CH2")
    if tf:
        m = tf.data
        print(
            f"[{tf.t_str}][CH2] Seq={m.get('NewDataSeq_2_')} Ready={m.get('ReadyToSend_2_')} "
            f"SOC={float(m.get('SOC1', 0.0)):.3f} pu VLOAD={float(m.get('VLOADRMS', 0.0)):.3f} rms"
        )
    else:
        _line("CH2", "No telemetry yet.")

    tf = snap.get("CH3")
    if tf:
        m = tf.data
        print(
            f"[{tf.t_str}][CH3] Seq={m.get('NewDataSeq_3_')} Ready={m.get('ReadyToSend_3_')} "
            f"PGEN={float(m.get('PGEN', 0.0)):.3f} QGEN={float(m.get('QGEN', 0.0)):.3f} BRKGEN={m.get('BRKGEN')}"
        )
    else:
        _line("CH3", "No telemetry yet.")

    tf = snap.get("CH4")
    if tf:
        m = tf.data
        print(
            f"[{tf.t_str}][CH4] Seq={m.get('NewDataSeq_4_')} Ready={m.get('ReadyToSend_4_')} "
            f"PLOAD={float(m.get('PLOAD680', 0.0)):.3f} MW QLOAD={float(m.get('QLOAD680', 0.0)):.3f} MVAr "
            f"N680={float(m.get('N680RMSPU', 0.0)):.3f} pu"
        )
    else:
        _line("CH4", "No telemetry yet.")


def dump_cmd_log(bus: DataBus, n: int = 20) -> None:
    log = bus.get_cmd_log()
    if not log:
        print(f"[{ts_now()}][LOG] No command events recorded yet.")
        return
    tail = log[-n:]
    print(f"[{ts_now()}][LOG] Showing last {len(tail)} of {len(log)} command events:")
    for evt in tail:
        note = f" ({evt.note})" if evt.note else ""
        print(f"  [{evt.t_str}][{evt.channel}] {evt.updates}{note}")


# -----------------------------
# Disruption CLI
# -----------------------------
VALID_CH = {"CH1", "CH2", "CH3", "CH4"}


def _parse_on_off(tok: str) -> bool:
    t = tok.lower()
    if t in ("on", "1", "true", "enable", "enabled"):
        return True
    if t in ("off", "0", "false", "disable", "disabled"):
        return False
    raise ValueError("Expected on/off (or 1/0)")


def _show_disruptions(data_cfg: Dict[str, DataDegradationConfig], auto_cfg: Dict[str, AutonomyDegradationConfig]) -> None:
    print(f"[{ts_now()}][DISRUPT] Current settings:")
    for ch in ("CH1", "CH2", "CH3", "CH4"):
        dc = data_cfg.get(ch, DataDegradationConfig(enabled=False))
        ac = auto_cfg.get(ch, AutonomyDegradationConfig(enabled=False))
        print(f"  {ch}  DATA: {dc}")
        print(f"       AUTO: {ac}")


def _apply_data(disruptor: ChannelDisruptor, data_cfg: Dict[str, DataDegradationConfig], ch: str, cfg: DataDegradationConfig) -> None:
    data_cfg[ch] = cfg
    disruptor.set_data_degraded(ch, cfg)


def _apply_auto(disruptor: ChannelDisruptor, auto_cfg: Dict[str, AutonomyDegradationConfig], ch: str, cfg: AutonomyDegradationConfig) -> None:
    auto_cfg[ch] = cfg
    disruptor.set_autonomy_degraded(ch, cfg)


def handle_disrupt_cmd(
    parts: list[str],
    disruptor: ChannelDisruptor,
    data_cfg: Dict[str, DataDegradationConfig],
    auto_cfg: Dict[str, AutonomyDegradationConfig],
) -> bool:
    """
    Returns True if command was handled (even if it errors).
    Syntax:
      disrupt show
      disrupt clear all|CHx
      disrupt data CHx freeze on|off
      disrupt data CHx drop <p>
      disrupt data CHx delay <fixed_s> [jitter_s]
      disrupt auto CHx block on|off
      disrupt auto CHx drop <p>
      disrupt auto CHx delay <fixed_s> [jitter_s]
    """
    if len(parts) < 2:
        print(f"[{ts_now()}][DISRUPT] Usage: disrupt show | disrupt clear ... | disrupt data ... | disrupt auto ...")
        return True

    sub = parts[1].lower()

    if sub == "show":
        _show_disruptions(data_cfg, auto_cfg)
        return True

    if sub == "clear":
        if len(parts) != 3:
            print(f"[{ts_now()}][DISRUPT] Usage: disrupt clear all | disrupt clear CH4")
            return True
        target = parts[2].upper()
        if target == "ALL":
            disruptor.clear_all()
            data_cfg.clear()
            auto_cfg.clear()
            print(f"[{ts_now()}][DISRUPT] cleared all")
            return True
        if target not in VALID_CH:
            print(f"[{ts_now()}][DISRUPT] Invalid channel {target}. Use CH1..CH4 or ALL.")
            return True
        disruptor.clear_channel(target)
        data_cfg.pop(target, None)
        auto_cfg.pop(target, None)
        print(f"[{ts_now()}][DISRUPT] cleared {target}")
        return True

    if sub == "data":
        if len(parts) < 5:
            print(f"[{ts_now()}][DISRUPT] Usage: disrupt data CH4 freeze on|off | drop p | delay fixed [jitter]")
            return True
        ch = parts[2].upper()
        if ch not in VALID_CH:
            print(f"[{ts_now()}][DISRUPT] Invalid channel {ch}. Use CH1..CH4.")
            return True

        action = parts[3].lower()
        cfg = data_cfg.get(ch, DataDegradationConfig(enabled=False))

        try:
            if action == "freeze":
                on = _parse_on_off(parts[4])
                cfg.enabled = on
                cfg.freeze = on
                if not on:
                    cfg.freeze = False
                _apply_data(disruptor, data_cfg, ch, cfg)
                print(f"[{ts_now()}][DISRUPT][DATA][{ch}] freeze {'ON' if on else 'OFF'}")
                return True

            if action == "drop":
                p = float(parts[4])
                cfg.enabled = True
                cfg.drop_prob = p
                _apply_data(disruptor, data_cfg, ch, cfg)
                print(f"[{ts_now()}][DISRUPT][DATA][{ch}] drop_prob={cfg.drop_prob}")
                return True

            if action == "delay":
                fixed = float(parts[4])
                jitter = float(parts[5]) if len(parts) >= 6 else 0.0
                cfg.enabled = True
                cfg.fixed_delay_s = fixed
                cfg.jitter_s = jitter
                _apply_data(disruptor, data_cfg, ch, cfg)
                print(f"[{ts_now()}][DISRUPT][DATA][{ch}] delay={cfg.fixed_delay_s} jitter={cfg.jitter_s}")
                return True

            print(f"[{ts_now()}][DISRUPT] Unknown data action: {action}")
            return True

        except ValueError as e:
            print(f"[{ts_now()}][DISRUPT][INPUT ERROR] {e}")
            return True

    if sub == "auto":
        if len(parts) < 5:
            print(f"[{ts_now()}][DISRUPT] Usage: disrupt auto CH2 block on|off | drop p | delay fixed [jitter]")
            return True
        ch = parts[2].upper()
        if ch not in VALID_CH:
            print(f"[{ts_now()}][DISRUPT] Invalid channel {ch}. Use CH1..CH4.")
            return True

        action = parts[3].lower()
        cfg = auto_cfg.get(ch, AutonomyDegradationConfig(enabled=False))

        try:
            if action == "block":
                on = _parse_on_off(parts[4])
                cfg.enabled = on
                cfg.block_all = on
                if not on:
                    cfg.block_all = False
                _apply_auto(disruptor, auto_cfg, ch, cfg)
                print(f"[{ts_now()}][DISRUPT][AUTO][{ch}] block {'ON' if on else 'OFF'}")
                return True

            if action == "drop":
                p = float(parts[4])
                cfg.enabled = True
                cfg.drop_prob = p
                _apply_auto(disruptor, auto_cfg, ch, cfg)
                print(f"[{ts_now()}][DISRUPT][AUTO][{ch}] drop_prob={cfg.drop_prob}")
                return True

            if action == "delay":
                fixed = float(parts[4])
                jitter = float(parts[5]) if len(parts) >= 6 else 0.0
                cfg.enabled = True
                cfg.fixed_delay_s = fixed
                cfg.jitter_s = jitter
                _apply_auto(disruptor, auto_cfg, ch, cfg)
                print(f"[{ts_now()}][DISRUPT][AUTO][{ch}] delay={cfg.fixed_delay_s} jitter={cfg.jitter_s}")
                return True

            print(f"[{ts_now()}][DISRUPT] Unknown auto action: {action}")
            return True

        except ValueError as e:
            print(f"[{ts_now()}][DISRUPT][INPUT ERROR] {e}")
            return True

    print(f"[{ts_now()}][DISRUPT] Unknown subcommand: {sub}")
    return True


# -----------------------------
# Defaults
# -----------------------------
def send_default_commands(tx, ch2: GtnetChannel, ch3: GtnetChannel, ch4: GtnetChannel) -> None:
    tx_bess_pref = set_slider_tx(tx, ch2, "CH2", "REM_Preftest", 0.3, lo=-2.0, hi=2.0, note="defaults: bess pref")
    set_selector_tx(tx, ch2, "CH2", "REM_BESSBRK", 1, note="defaults: bess brk")

    tx_gen_wref = set_slider_tx(tx, ch3, "CH3", "REM_Wref", 1.0, lo=0.0, hi=100.0, note="defaults: gen wref")
    tx_gen_pref = set_slider_tx(tx, ch3, "CH3", "REM_PREF", 0.1, lo=-100.0, hi=100.0, note="defaults: gen pref")

    tx_p = set_slider_tx(tx, ch4, "CH4", "REM_PLOAD", 0.8, lo=0.0, hi=50.0, note="defaults: load p")
    tx_q = set_slider_tx(tx, ch4, "CH4", "REM_QLOAD", 0.003, lo=0.001, hi=50.0, note="defaults: load q")

    print(
        f"[{ts_now()}][TX-ARMED][DEFAULTS] "
        f"REM_Preftest={tx_bess_pref}  REM_BESSBRK=1  "
        f"REM_Wref={tx_gen_wref}  REM_PREF={tx_gen_pref}  "
        f"REM_PLOAD={tx_p}  REM_QLOAD={tx_q}"
    )


# -----------------------------
# Main CLI
# -----------------------------
def main():
    bus = DataBus()

    disruptor = ChannelDisruptor(
        meas_sink=lambda ch, m: bus.update_meas(ch, m),
        cmd_sink=lambda ch_obj, ch, upd, note: bus.emit_cmd(ch_obj, ch, upd, note=note),
    )

    # keep local copies so "disrupt show" can print current settings
    data_cfg: Dict[str, DataDegradationConfig] = {}
    auto_cfg: Dict[str, AutonomyDegradationConfig] = {}

    ch1 = GtnetChannel(CHANNEL_1, on_meas=lambda m: disruptor.on_meas("CH1", m))
    ch2 = GtnetChannel(CHANNEL_2, on_meas=lambda m: disruptor.on_meas("CH2", m))
    ch3 = GtnetChannel(CHANNEL_3, on_meas=lambda m: disruptor.on_meas("CH3", m))
    ch4 = GtnetChannel(CHANNEL_4, on_meas=lambda m: disruptor.on_meas("CH4", m))

    ch1.start()
    ch2.start()
    ch3.start()
    ch4.start()

    print("Connected. Type commands (status / defaults / log / disrupt / pcc / load / bess / gen / quit).")
    print("Add disruptions:")
    print("  disrupt show")
    print("  disrupt clear all | disrupt clear CH4")
    print("  disrupt data CH4 freeze on|off")
    print("  disrupt data CH4 drop <p>")
    print("  disrupt data CH4 delay <fixed_s> [jitter_s]")
    print("  disrupt auto CH2 block on|off")
    print("  disrupt auto CH2 drop <p>")
    print("  disrupt auto CH2 delay <fixed_s> [jitter_s]")
    print("")

    try:
        while True:
            line = input(">> ").strip()
            if not line:
                continue

            parts = line.split()
            cmd = parts[0].lower()

            if cmd == "quit":
                break

            if cmd == "status":
                print_status(bus)
                continue

            if cmd == "log":
                n = 20
                if len(parts) == 2:
                    n = int(parts[1])
                    _require(n > 0, "log N must be > 0")
                dump_cmd_log(bus, n=n)
                continue

            # NEW: disruptions handled BEFORE MODE gate
            if cmd == "disrupt":
                handle_disrupt_cmd(parts, disruptor, data_cfg, auto_cfg)
                continue

            # Gate: require CH1 telemetry + MODE=REMOTE.
            if bus.get_meas("CH1") is None:
                print(f"[{ts_now()}][WARN] No CH1 measurements yet; cannot validate MODE.")
                continue

            if not mode_is_remote_from_bus(bus):
                print(f"[{ts_now()}][BLOCKED] MODE indicates MANUAL. Switch to REMOTE before sending commands.")
                continue

            tx = disruptor  # route commands through disruptor (pass-through unless degraded)

            try:
                if cmd == "defaults" and len(parts) == 1:
                    send_default_commands(tx, ch2, ch3, ch4)
                    continue

                # ---------------- CH1: PCC ----------------
                if cmd == "pcc" and len(parts) >= 2:
                    sub = parts[1].lower()

                    if sub == "grid" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector_tx(tx, ch1, "CH1", "REM_GRID", val, note="pcc grid")
                        continue

                    if sub == "fault" and (len(parts) == 2 or (len(parts) == 3 and parts[2].lower() == "press")):
                        pb_pulse_tx(tx, ch1, "CH1", "REM_LGFLTx", pulse_s=0.25, note="pcc fault press")
                        continue

                    if sub == "faultcfg" and len(parts) == 4:
                        cycles = float(parts[2])
                        ftype = int(parts[3])

                        tx_cycles = set_slider_tx(tx, ch1, "CH1", "REM_LGFTIMEx", cycles, lo=0.0, hi=50.0, note="pcc faultcfg cycles")
                        set_dial_tx(tx, ch1, "CH1", "REM_LGFLTxType", ftype, lo=0, hi=7, note="pcc faultcfg type")

                        print(f"[{ts_now()}][TX-ARMED][CH1] REM_LGFTIMEx={tx_cycles} cycles  REM_LGFLTxType={ftype}")
                        continue

                # ---------------- CH2: BESS ----------------
                if cmd == "bess" and len(parts) >= 2:
                    sub = parts[1].lower()

                    if sub == "pref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider_tx(tx, ch2, "CH2", "REM_Preftest", val, lo=-2.0, hi=2.0, note="bess pref")
                        print(f"[{ts_now()}][TX-ARMED][CH2] REM_Preftest={tx_val}")
                        continue

                    if sub == "qref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider_tx(tx, ch2, "CH2", "REM_Qreftest", val, lo=-2.0, hi=2.0, note="bess qref")
                        print(f"[{ts_now()}][TX-ARMED][CH2] REM_Qreftest={tx_val}")
                        continue

                    if sub == "block" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector_tx(tx, ch2, "CH2", "REM_BLOCK", val, note="bess block")
                        continue

                    if sub == "chkreset" and len(parts) == 3 and parts[2].lower() == "press":
                        pb_pulse_tx(tx, ch2, "CH2", "REM_CHKRESET", pulse_s=0.25, note="bess chkreset press")
                        continue

                    if sub == "brk" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector_tx(tx, ch2, "CH2", "REM_BESSBRK", val, note="bess brk")
                        continue

                # ---------------- CH3: GEN ----------------
                if cmd == "gen" and len(parts) >= 2:
                    sub = parts[1].lower()

                    if sub == "block" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector_tx(tx, ch3, "CH3", "REM_BLOCKGEN", val, note="gen block")
                        continue

                    if sub == "wref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider_tx(tx, ch3, "CH3", "REM_Wref", val, lo=0.0, hi=100.0, note="gen wref")
                        print(f"[{ts_now()}][TX-ARMED][CH3] REM_Wref={tx_val}")
                        continue

                    if sub == "pref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider_tx(tx, ch3, "CH3", "REM_PREF", val, lo=-100.0, hi=100.0, note="gen pref")
                        print(f"[{ts_now()}][TX-ARMED][CH3] REM_PREF={tx_val}")
                        continue

                    if sub == "reset" and len(parts) == 3 and parts[2].lower() == "press":
                        pb_pulse_tx(tx, ch3, "CH3", "REM_RESETGEN", pulse_s=0.25, note="gen reset press")
                        continue

                # ---------------- CH4: LOAD ----------------
                if cmd == "load" and len(parts) >= 3:
                    sub = parts[1].lower()

                    if sub == "p" and len(parts) == 3:
                        p = float(parts[2])
                        tx_p = set_slider_tx(tx, ch4, "CH4", "REM_PLOAD", p, lo=0.0, hi=50.0, note="load p")
                        print(f"[{ts_now()}][TX-ARMED][CH4] REM_PLOAD={tx_p}")
                        continue

                    if sub == "q" and len(parts) == 3:
                        q = float(parts[2])
                        tx_q = set_slider_tx(tx, ch4, "CH4", "REM_QLOAD", q, lo=0.001, hi=50.0, note="load q")
                        print(f"[{ts_now()}][TX-ARMED][CH4] REM_QLOAD={tx_q}")
                        continue

                    if sub == "pq" and len(parts) == 4:
                        p = float(parts[2])
                        q = float(parts[3])
                        tx_p = set_slider_tx(tx, ch4, "CH4", "REM_PLOAD", p, lo=0.0, hi=50.0, note="load pq p")
                        tx_q = set_slider_tx(tx, ch4, "CH4", "REM_QLOAD", q, lo=0.001, hi=50.0, note="load pq q")
                        print(f"[{ts_now()}][TX-ARMED][CH4] REM_PLOAD={tx_p}  REM_QLOAD={tx_q}")
                        continue

                print(f"[{ts_now()}][ERROR] Unknown command or wrong syntax.")

            except ValueError as e:
                print(f"[{ts_now()}][INPUT ERROR] {e}")
            except Exception as e:
                print(f"[{ts_now()}][UNEXPECTED ERROR] {e}")

    finally:
        ch1.stop()
        ch2.stop()
        ch3.stop()
        ch4.stop()
        print(f"[{ts_now()}] Stopped.")


if __name__ == "__main__":
    main()

