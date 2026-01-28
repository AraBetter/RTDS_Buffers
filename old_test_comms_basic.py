import time

from Comms.channel_specs import CHANNEL_1, CHANNEL_2, CHANNEL_3, CHANNEL_4
from Comms.gtnet_channel import GtnetChannel




def mode_is_remote(ch1_meas: dict) -> bool:
    """
    Your convention has been:
      MODE=0 -> MANUAL (block commands)
      MODE=1 -> REMOTE (allow commands)
    """
    try:
        return int(ch1_meas.get("MODE", 0)) == 1
    except Exception:
        return False


def print_status(ch1: GtnetChannel, ch4: GtnetChannel) -> None:
    m1 = ch1.get_latest_meas()
    m2 = ch2.get_latest_meas()
    m3 = ch3.get_latest_meas()
    m4 = ch4.get_latest_meas()

    if m1:
        print(
            f"[CH1] Seq={m1.get('NewDataSeq_1')} "
            f"Ready={m1.get('ReadyToSend_1')} "
            f"MODE={m1.get('MODE')} "
            f"PGRID={float(m1.get('PGRID', 0.0)):.3f} "
            f"QGRID={float(m1.get('QGRID', 0.0)):.3f}"
        )
    else:
        print("[CH1] No telemetry yet.")

    if m2:
        print(
            f"[CH2] Seq={m2.get('NewDataSeq_2_')} "
            f"Ready={m2.get('ReadyToSend_2_')} "
            f"BRK1island={m2.get('BRK1island')} "
            f"SOC1={float(m2.get('SOC1', 0.0)):.3f} "
            f"VLOADRMS={float(m2.get('VLOADRMS', 0.0)):.3f}"
        )
    else:
        print("[CH2] No telemetry yet.")

    if m3:
        print(
            f"[CH3] Seq={m3.get('NewDataSeq_3_')} "
            f"Ready={m3.get('ReadyToSend_3_')} "
            f"BRKGEN={m3.get('BRKGEN')} "
            f"PGEN={float(m3.get('PGEN', 0.0)):.3f} "
            f"PMACH={float(m3.get('PMACH', 0.0)):.3f}"
        )
    else:
        print("[CH3] No telemetry yet.")

    if m4:
        print(
            f"[CH4] Seq={m4.get('NewDataSeq_4')} "
            f"Ready={m4.get('ReadyToSend_4')} "
            f"PLOAD={float(m4.get('PLOAD680', 0.0)):.3f} MW "
            f"QLOAD={float(m4.get('QLOAD680', 0.0)):.3f} MVAr "
            f"N680={float(m4.get('N680RMSPU', 0.0)):.3f} pu"
        )
    else:
        print("[CH4] No telemetry yet.")


def main():
    # Start channels. RX always on.
    # TX exists, but will only transmit after you call set_cmd(...),

    ch1 = GtnetChannel(CHANNEL_1)
    ch2 = GtnetChannel(CHANNEL_2)
    ch3 = GtnetChannel(CHANNEL_3)
    ch4 = GtnetChannel(CHANNEL_4)

    ch1.start()
    ch2.start()
    ch3.start()
    ch4.start()

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
                print_status(ch1, ch2, ch3, ch4)
                continue


            m1 = ch1.get_latest_meas()
            if not m1:
                print("[WARN] No CH1 measurements yet; cannot validate MODE/ReadyToSend.")
                continue

            if not mode_is_remote(m1):
                print("[BLOCKED] MODE indicates MANUAL. Switch to REMOTE before sending commands.")
                continue

            if cmd == "armdefaults":
                sec = fault_cycles / 60
                ch1.set_cmd({"REM_LGFTIMEx": sec, "REM_LGFLItype": fault_type})
                ch4.set_cmd({"REM_PLOAD": load_p, "REM_QLOAD": load_q})
                print(f"[TX-ARMED] Defaults sent: fault {fault_cycles} cycles (sec={sec:.6f}), type={fault_type}, "
                      f"PLOAD={load_p} MW, QLOAD={load_q} MVAr")
                continue


            # --- Grid command ---
            if cmd == "grid" and len(parts) == 2:
                val = int(parts[1])
                ch1.set_cmd({"REM_GRID": val})
                print(f"[TX-ARMED] REM_GRID={val}")
                continue

            # --- Fault commands ---
            if cmd == "fault" and len(parts) == 2:
                sub = parts[1].lower()

                if sub == "press":
                    # pulse 1 then 0 (like pushbutton)
                    ch1.set_cmd({"REM_LGFLTx": 1})
                    print("[TX-ARMED] REM_LGFLTx=1 (pulse)")
                    time.sleep(0.1)
                    ch1.set_cmd({"REM_LGFLTx": 0})
                    print("[TX-ARMED] REM_LGFLTx=0 (pulse end)")
                    continue

                if sub == "on":
                    ch1.set_cmd({"REM_LGFLTx": 1})
                    print("[TX-ARMED] REM_LGFLTx=1 (latched)")
                    continue

                if sub == "off":
                    ch1.set_cmd({"REM_LGFLTx": 0})
                    print("[TX-ARMED] REM_LGFLTx=0 (cleared)")
                    continue

            if cmd == "faultcfg" and len(parts) == 3:
                fault_cycles = int(parts[1])
                fault_type = int(parts[2])

                sec = fault_cycles / 60  # cycles -> seconds
                ch1.set_cmd({"REM_LGFTIMEx": sec, "REM_LGFLItype": fault_type})

                print(f"[TX-ARMED] REM_LGFTIMEx={sec:.6f} s ({fault_cycles} cycles)  "
                      f"REM_LGFLItype={fault_type}")
                continue

            # --- CH4: Load commands ---
            if cmd == "load" and len(parts) >= 3:
                sub = parts[1].lower()

                if sub == "p" and len(parts) == 3:
                    p = float(parts[2])
                    load_p = p
                    ch4.set_cmd({"REM_PLOAD": p})
                    print(f"[TX-ARMED] REM_PLOAD={p}")
                    continue

                if sub == "q" and len(parts) == 3:
                    q = float(parts[2])
                    load_q = q
                    ch4.set_cmd({"REM_QLOAD": q})
                    print(f"[TX-ARMED] REM_QLOAD={q}")
                    continue

                if sub == "pq" and len(parts) == 4:
                    p = float(parts[2])
                    q = float(parts[3])
                    load_p = p
                    load_q = q
                    ch4.set_cmd({"REM_PLOAD": p, "REM_QLOAD": q})
                    print(f"[TX-ARMED] REM_PLOAD={p:.3f}  REM_QLOAD={q:.3f}")

                    continue

            print("[ERROR] Unknown command or wrong syntax. Type 'status' to confirm comms.")

    finally:
        ch1.stop()
        ch2.stop()
        ch3.stop()
        ch4.stop()
        print("Stopped.")


if __name__ == "__main__":
    main()
