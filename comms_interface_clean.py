import time
from typing import Dict, Union, List

from Comms.data_bus import DataBus, ts_now
from Comms.channel_specs import CHANNEL_1, CHANNEL_2, CHANNEL_3, CHANNEL_4
from Comms.gtnet_channel import GtnetChannel

Number = Union[int, float]


# -----------------------------
# Local “helper” implementations
# (so we can route ALL TX through DataBus.emit_cmd)
# -----------------------------
def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ValueError(msg)


def set_selector_bus(bus: DataBus, channel: GtnetChannel, ch_name: str, sig: str, val: int, *, note: str = "") -> None:
    _require(val in (0, 1), f"{sig}: selector must be 0 or 1 (got {val})")
    bus.emit_cmd(channel, ch_name, {sig: int(val)}, note=note)


def set_dial_bus(
    bus: DataBus,
    channel: GtnetChannel,
    ch_name: str,
    sig: str,
    val: int,
    *,
    lo: int,
    hi: int,
    note: str = "",
) -> int:
    _require(isinstance(val, int), f"{sig}: dial must be int (got {type(val)})")
    _require(lo <= val <= hi, f"{sig}: dial out of range [{lo},{hi}] (got {val})")
    bus.emit_cmd(channel, ch_name, {sig: int(val)}, note=note)
    return val


def set_slider_bus(
    bus: DataBus,
    channel: GtnetChannel,
    ch_name: str,
    sig: str,
    val: float,
    *,
    lo: float,
    hi: float,
    note: str = "",
) -> float:
    _require(lo <= hi, f"{sig}: invalid range lo>hi")
    # clamp (matches your previous behavior)
    tx = float(val)
    if tx < lo:
        tx = lo
    elif tx > hi:
        tx = hi
    bus.emit_cmd(channel, ch_name, {sig: tx}, note=note)
    return tx


def pb_pulse_bus(
    bus: DataBus,
    channel: GtnetChannel,
    ch_name: str,
    sig: str,
    *,
    pulse_s: float = 0.25,
    note: str = "",
) -> None:
    _require(pulse_s > 0, "pulse_s must be > 0")
    bus.emit_cmd(channel, ch_name, {sig: 1}, note=(note or f"{sig} pulse start"))
    time.sleep(pulse_s)
    bus.emit_cmd(channel, ch_name, {sig: 0}, note=(note or f"{sig} pulse end"))


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

    # CH1
    tf = snap.get("CH1")
    if tf:
        m = tf.data
        print(
            f"[{tf.t_str}][CH1] "
            f"Seq={m.get('NewDataSeq_1_')} Ready={m.get('ReadyToSend_1_')} MODE={m.get('MODE')} "
            f"PGRID={float(m.get('PGRID', 0.0)):.3f} QGRID={float(m.get('QGRID', 0.0)):.3f} "
            f"GRID={m.get('GRID')}"
        )
    else:
        print(f"[{ts_now()}][CH1] No telemetry yet.")

    # CH2
    tf = snap.get("CH2")
    if tf:
        m = tf.data
        print(
            f"[{tf.t_str}][CH2] "
            f"Seq={m.get('NewDataSeq_2_')} Ready={m.get('ReadyToSend_2_')} "
            f"SOC={float(m.get('SOC1', 0.0)):.3f} pu "
            f"VLOAD={float(m.get('VLOADRMS', 0.0)):.3f} rms"
        )
    else:
        print(f"[{ts_now()}][CH2] No telemetry yet.")

    # CH3
    tf = snap.get("CH3")
    if tf:
        m = tf.data
        print(
            f"[{tf.t_str}][CH3] "
            f"Seq={m.get('NewDataSeq_3_')} Ready={m.get('ReadyToSend_3_')} "
            f"PGEN={float(m.get('PGEN', 0.0)):.3f} QGEN={float(m.get('QGEN', 0.0)):.3f} "
            f"BRKGEN={m.get('BRKGEN')} "
            f"WPU={float(m.get('WPU', 0.0)):.4f} "
            f"W_DETECTED={m.get('W_DETECTED')} "
        )
    else:
        print(f"[{ts_now()}][CH3] No telemetry yet.")

    # CH4
    tf = snap.get("CH4")
    if tf:
        m = tf.data
        print(
            f"[{tf.t_str}][CH4] "
            f"Seq={m.get('NewDataSeq_4_')} Ready={m.get('ReadyToSend_4_')} "
            f"PLOAD={float(m.get('PLOAD680', 0.0)):.3f} MW "
            f"QLOAD={float(m.get('QLOAD680', 0.0)):.3f} MVAr "
            f"N680={float(m.get('N680RMSPU', 0.0)):.3f} pu"
        )
    else:
        print(f"[{ts_now()}][CH4] No telemetry yet.")


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
# Defaults
# -----------------------------
def send_default_commands(bus: DataBus, ch2: GtnetChannel, ch3: GtnetChannel, ch4: GtnetChannel) -> None:
    """
    Sends a known-good set of default commands:
      bess pref 0.3
      bess brk 1
      gen wref 1
      gen pref 0.1
      load pq 0.8 0.003
    """
    tx_bess_pref = set_slider_bus(bus, ch2, "CH2", "REM_Preftest", 0.3, lo=-2.0, hi=2.0, note="defaults: bess pref")
    set_selector_bus(bus, ch2, "CH2", "REM_BESSBRK", 1, note="defaults: bess brk")

    tx_gen_wref = set_slider_bus(bus, ch3, "CH3", "REM_Wref", 1.0, lo=0.0, hi=100.0, note="defaults: gen wref")
    tx_gen_pref = set_slider_bus(bus, ch3, "CH3", "REM_PREF", 0.1, lo=-100.0, hi=100.0, note="defaults: gen pref")

    tx_p = set_slider_bus(bus, ch4, "CH4", "REM_PLOAD", 0.8, lo=0.0, hi=50.0, note="defaults: load p")
    tx_q = set_slider_bus(bus, ch4, "CH4", "REM_QLOAD", 0.003, lo=0.001, hi=50.0, note="defaults: load q")

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

    # Wire RX -> DataBus with timestamps.
    ch1 = GtnetChannel(CHANNEL_1, on_meas=lambda m: bus.update_meas("CH1", m))
    ch2 = GtnetChannel(CHANNEL_2, on_meas=lambda m: bus.update_meas("CH2", m))
    ch3 = GtnetChannel(CHANNEL_3, on_meas=lambda m: bus.update_meas("CH3", m))
    ch4 = GtnetChannel(CHANNEL_4, on_meas=lambda m: bus.update_meas("CH4", m))

    ch1.start()
    ch2.start()
    ch3.start()
    ch4.start()

    print("Connected. Type commands (status / defaults / log / pcc / load / bess / gen / quit).")
    print("Commands:")
    print("  status")
    print("  defaults                        (bess pref 0.3, bess brk 1, gen wref 1, gen pref 0.1, load pq 0.8 0.003)")
    print("  log [N]                         (dump last N command events, default 20)")
    print("")
    print("  pcc grid <0|1>                  (CH1: REM_GRID)")
    print("  pcc fault [press]               (CH1: REM_LGFLTx pushbutton pulse)")
    print("  pcc faultcfg <cycles> <type0-7> (CH1: REM_LGFTIMEx, REM_LGFLTxType)")
    print("")
    print("  bess pref <MW>                  (CH2: REM_Preftest, -2..2)")
    print("  bess qref <MVAr>                (CH2: REM_Qreftest, -2..2)")
    print("  bess block <0|1>                (CH2: REM_BLOCK)")
    print("  bess chkreset press             (CH2: REM_CHKRESET pushbutton pulse)")
    print("  bess brk <0|1>                  (CH2: REM_BESSBRK)")
    print("")
    print("  gen block <0|1>                 (CH3: REM_BLOCKGEN)")
    print("  gen wref <pu>                   (CH3: REM_Wref, 0..100)")
    print("  gen pref <pu>                   (CH3: REM_PREF, -100..100)")
    print("  gen reset press                 (CH3: REM_RESETGEN pushbutton pulse)")
    print("")
    print("  load p <MW>                     (CH4: REM_PLOAD, 0..50)")
    print("  load q <MVAr>                   (CH4: REM_QLOAD, 0.001..50)")
    print("  load pq <MW> <MVAr>             (CH4: REM_PLOAD + REM_QLOAD)")
    print("")
    print("  quit\n")

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

            # Gate: require CH1 telemetry + MODE=REMOTE.
            if bus.get_meas("CH1") is None:
                print(f"[{ts_now()}][WARN] No CH1 measurements yet; cannot validate MODE.")
                continue

            if not mode_is_remote_from_bus(bus):
                print(f"[{ts_now()}][BLOCKED] MODE indicates MANUAL. Switch to REMOTE before sending commands.")
                continue

            try:
                # defaults
                if cmd == "defaults" and len(parts) == 1:
                    send_default_commands(bus, ch2, ch3, ch4)
                    continue

                # ---------------- CH1: PCC ----------------
                if cmd == "pcc" and len(parts) >= 2:
                    sub = parts[1].lower()

                    if sub == "grid" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector_bus(bus, ch1, "CH1", "REM_GRID", val, note="pcc grid")
                        continue

                    if sub == "fault" and (len(parts) == 2 or (len(parts) == 3 and parts[2].lower() == "press")):
                        # pulse width tuned for lab robustness
                        pb_pulse_bus(bus, ch1, "CH1", "REM_LGFLTx", pulse_s=0.25, note="pcc fault press")
                        continue

                    if sub == "faultcfg" and len(parts) == 4:
                        cycles = float(parts[2])
                        ftype = int(parts[3])

                        tx_cycles = set_slider_bus(
                            bus, ch1, "CH1", "REM_LGFTIMEx", cycles, lo=0.0, hi=50.0, note="pcc faultcfg cycles"
                        )
                        set_dial_bus(bus, ch1, "CH1", "REM_LGFLTxType", ftype, lo=0, hi=7, note="pcc faultcfg type")

                        print(f"[{ts_now()}][TX-ARMED][CH1] REM_LGFTIMEx={tx_cycles} cycles  REM_LGFLTxType={ftype}")
                        continue

                # ---------------- CH2: BESS ----------------
                if cmd == "bess" and len(parts) >= 2:
                    sub = parts[1].lower()

                    if sub == "pref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider_bus(bus, ch2, "CH2", "REM_Preftest", val, lo=-2.0, hi=2.0, note="bess pref")
                        print(f"[{ts_now()}][TX-ARMED][CH2] REM_Preftest={tx_val}")
                        continue

                    if sub == "qref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider_bus(bus, ch2, "CH2", "REM_Qreftest", val, lo=-2.0, hi=2.0, note="bess qref")
                        print(f"[{ts_now()}][TX-ARMED][CH2] REM_Qreftest={tx_val}")
                        continue

                    if sub == "block" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector_bus(bus, ch2, "CH2", "REM_BLOCK", val, note="bess block")
                        continue

                    if sub == "chkreset" and len(parts) == 3 and parts[2].lower() == "press":
                        pb_pulse_bus(bus, ch2, "CH2", "REM_CHKRESET", pulse_s=0.25, note="bess chkreset press")
                        continue

                    if sub == "brk" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector_bus(bus, ch2, "CH2", "REM_BESSBRK", val, note="bess brk")
                        continue

                # ---------------- CH3: GEN ----------------
                if cmd == "gen" and len(parts) >= 2:
                    sub = parts[1].lower()

                    if sub == "block" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector_bus(bus, ch3, "CH3", "REM_BLOCKGEN", val, note="gen block")
                        continue

                    if sub == "wref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider_bus(bus, ch3, "CH3", "REM_Wref", val, lo=0.0, hi=100.0, note="gen wref")
                        print(f"[{ts_now()}][TX-ARMED][CH3] REM_Wref={tx_val}")
                        continue

                    if sub == "pref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider_bus(bus, ch3, "CH3", "REM_PREF", val, lo=-100.0, hi=100.0, note="gen pref")
                        print(f"[{ts_now()}][TX-ARMED][CH3] REM_PREF={tx_val}")
                        continue

                    if sub == "reset" and len(parts) == 3 and parts[2].lower() == "press":
                        pb_pulse_bus(bus, ch3, "CH3", "REM_RESETGEN", pulse_s=0.25, note="gen reset press")
                        continue

                # ---------------- CH4: LOAD ----------------
                if cmd == "load" and len(parts) >= 3:
                    sub = parts[1].lower()

                    if sub == "p" and len(parts) == 3:
                        p = float(parts[2])
                        tx_p = set_slider_bus(bus, ch4, "CH4", "REM_PLOAD", p, lo=0.0, hi=50.0, note="load p")
                        print(f"[{ts_now()}][TX-ARMED][CH4] REM_PLOAD={tx_p}")
                        continue

                    if sub == "q" and len(parts) == 3:
                        q = float(parts[2])
                        tx_q = set_slider_bus(bus, ch4, "CH4", "REM_QLOAD", q, lo=0.001, hi=50.0, note="load q")
                        print(f"[{ts_now()}][TX-ARMED][CH4] REM_QLOAD={tx_q}")
                        continue

                    if sub == "pq" and len(parts) == 4:
                        p = float(parts[2])
                        q = float(parts[3])
                        tx_p = set_slider_bus(bus, ch4, "CH4", "REM_PLOAD", p, lo=0.0, hi=50.0, note="load pq p")
                        tx_q = set_slider_bus(bus, ch4, "CH4", "REM_QLOAD", q, lo=0.001, hi=50.0, note="load pq q")
                        print(f"[{ts_now()}][TX-ARMED][CH4] REM_PLOAD={tx_p}  REM_QLOAD={tx_q}")
                        continue

                print(f"[{ts_now()}][ERROR] Unknown command or wrong syntax. Type 'status' to confirm comms.")

            except ValueError as e:
                print(f"[{ts_now()}][INPUT ERROR] {e}")
                continue
            except Exception as e:
                print(f"[{ts_now()}][UNEXPECTED ERROR] {e}")
                continue

    finally:
        ch1.stop()
        ch2.stop()
        ch3.stop()
        ch4.stop()
        print(f"[{ts_now()}] Stopped.")


if __name__ == "__main__":
    main()
