import time

from Comms.channel_specs import CHANNEL_1, CHANNEL_2, CHANNEL_3, CHANNEL_4
from Comms.gtnet_channel import GtnetChannel
from Comms.command_helpers import pb_pulse, set_dial, set_selector, set_slider


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


def print_status(ch1: GtnetChannel, ch2: GtnetChannel, ch3: GtnetChannel, ch4: GtnetChannel) -> None:
    m1 = ch1.get_latest_meas()
    m2 = ch2.get_latest_meas()
    m3 = ch3.get_latest_meas()
    m4 = ch4.get_latest_meas()

    if m1:
        print(
            f"[CH1] Seq={m1.get('NewDataSeq_1_')} "
            f"Ready={m1.get('ReadyToSend_1_')} "
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
            f"SOC={float(m2.get('SOC1', 0.0)):.3f} pu "
            f"VLOAD={float(m2.get('VLOADRMS', 0.0)):.3f} rms"
        )
    else:
        print("[CH2] No telemetry yet.")

    if m3:
        print(
            f"[CH3] Seq={m3.get('NewDataSeq_3_')} "
            f"Ready={m3.get('ReadyToSend_3_')} "
            f"PGEN={float(m3.get('PGEN', 0.0)):.3f} "
            f"QGEN={float(m3.get('QGEN', 0.0)):.3f} "
            f"BRKGEN={m3.get('BRKGEN')}"
        )
    else:
        print("[CH3] No telemetry yet.")

    if m4:
        print(
            f"[CH4] Seq={m4.get('NewDataSeq_4_')} "
            f"Ready={m4.get('ReadyToSend_4_')} "
            f"PLOAD={float(m4.get('PLOAD680', 0.0)):.3f} MW "
            f"QLOAD={float(m4.get('QLOAD680', 0.0)):.3f} MVAr "
            f"N680={float(m4.get('N680RMSPU', 0.0)):.3f} pu"
        )
    else:
        print("[CH4] No telemetry yet.")

def send_default_commands(ch2: GtnetChannel, ch3: GtnetChannel, ch4: GtnetChannel) -> None:
    """
    Sends a known-good set of default commands:
      bess pref 0.3
      bess brk 1
      gen wref 1
      gen pref 0.1
      load pq 0.8 0.003
    """
    # CH2: BESS
    tx_bess_pref = set_slider(ch2, "REM_Preftest", 0.3, lo=-2.0, hi=2.0)
    set_selector(ch2, "REM_BESSBRK", 1)

    # CH3: Generator
    tx_gen_wref = set_slider(ch3, "REM_Wref", 1.0, lo=0.0, hi=100.0)
    tx_gen_pref = set_slider(ch3, "REM_PREF", 0.1, lo=-100.0, hi=100.0)

    # CH4: Load
    tx_p = set_slider(ch4, "REM_PLOAD", 0.8, lo=0.0, hi=50.0)
    tx_q = set_slider(ch4, "REM_QLOAD", 0.003, lo=0.001, hi=50.0)

    print(
        "[TX-ARMED][DEFAULTS] "
        f"REM_Preftest={tx_bess_pref}  REM_BESSBRK=1  "
        f"REM_Wref={tx_gen_wref}  REM_PREF={tx_gen_pref}  "
        f"REM_PLOAD={tx_p}  REM_QLOAD={tx_q}"
    )

def main():
    # Start all channels. RX always on.
    # TX exists, but will only transmit after you call set_cmd(...),
    # assuming you changed self._dirty = False in GtnetChannel.
    ch1 = GtnetChannel(CHANNEL_1)
    ch2 = GtnetChannel(CHANNEL_2)
    ch3 = GtnetChannel(CHANNEL_3)
    ch4 = GtnetChannel(CHANNEL_4)

    ch1.start()
    ch2.start()
    ch3.start()
    ch4.start()

    print("Connected. Type commands (status / defaults / grid / fault / load / bess / gen / quit).")
    print("Commands:")
    print("  status")
    print("  defaults                        (send: bess pref 0.3, bess brk 1, gen wref 1, gen pref 0.1, "
                                             "load pq 0.8 0.003)")

    print("  pcc grid <0|1>                  (CH1: REM_GRID)")
    print("  pcc fault press                 (CH1: REM_LGFLTx pulse 1 then 0)")
    print("  pcc faultcfg <cycles> <type0-7> (CH1: REM_LGFTIMEx, REM_LGFLTxType)")

    print("  bess pref <MW>                  (CH2: REM_Preftest, range -2..2)")
    print("  bess qref <MVAr>                (CH2: REM_Qreftest, range -2..2)")
    print("  bess block <0|1>                (CH2: REM_BLOCK)")
    print("  bess chkreset press             (CH2: REM_CHKRESET pulse 1 then 0)")
    print("  bess brk <0|1>                  (CH2: REM_BESSBRK)")

    print("  gen block <0|1>                 (CH3: REM_BLOCKGEN)")
    print("  gen wref <pu>                   (CH3: REM_Wref, range 0..100)")
    print("  gen pref <pu>                   (CH3: REM_PREF, range -100..100)")
    print("  gen reset press                 (CH3: REM_RESETGEN pulse 1 then 0)")

    print("  load p <MW>                     (CH4: REM_PLOAD)")
    print("  load q <MVAr>                   (CH4: REM_QLOAD)")
    print("  load pq <MW> <MVAr>             (CH4: both)")

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
                print_status(ch1, ch2, ch3, ch4)
                continue

            m1 = ch1.get_latest_meas()
            if not m1:
                print("[WARN] No CH1 measurements yet; cannot validate MODE/ReadyToSend.")
                continue

            if not mode_is_remote(m1):
                print("[BLOCKED] MODE indicates MANUAL. Switch to REMOTE before sending commands.")
                continue

            try:

                # one-shot defaults
                if cmd == "defaults" and len(parts) == 1:
                    send_default_commands(ch2, ch3, ch4)
                    continue

                # CH1: PCC / Mode / Fault - --
                # Syntax:
                #   pcc grid <0|1>                 (REM_GRID, int selector 0/1)
                #   pcc fault press                (REM_LGFLTx, int pushbutton pulse 1->0)
                #   pcc faultcfg <cycles> <type>   (REM_LGFTIMEx float cycles 0..50, REM_LGFLTxType int 0..7)

                if cmd == "pcc" and len(parts) >= 2:
                    sub = parts[1].lower()

                    if sub == "grid" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector(ch1, "REM_GRID", val)
                        print(f"[TX-ARMED] REM_GRID={val}")
                        continue

                    if sub == "fault" and (len(parts) == 2 or (len(parts) == 3 and parts[2].lower() == "press")):
                        pb_pulse(ch1, "REM_LGFLTx")
                        print("[TX-ARMED] REM_LGFLTx pulse")
                        continue

                    if sub == "faultcfg" and len(parts) == 4:
                        cycles = float(parts[2])
                        ftype = int(parts[3])
                        tx_cycles = set_slider(ch1, "REM_LGFTIMEx", cycles, lo=0.0, hi=50.0)
                        set_dial(ch1, "REM_LGFLTxType", ftype, lo=0, hi=7)
                        print(f"[TX-ARMED] REM_LGFTIMEx={tx_cycles} cycles  REM_LGFLTxType={ftype}")
                        continue

                # --- CH2: Battery/BESS supervisory commands ---
                # Syntax:
                #   bess pref <mw>         (REM_Preftest, float, -2..2)
                #   bess qref <mvar>       (REM_Qreftest, float, -2..2)
                #   bess block 0|1         (REM_BLOCK, int)
                #   bess chkreset press    (REM_CHKRESET, pushbutton pulse)
                #   bess brk 0|1           (REM_BESSBRK, int)

                if cmd == "bess" and len(parts) >= 2:
                    sub = parts[1].lower()

                    if sub == "pref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider(ch2, "REM_Preftest", val, lo=-2.0, hi=2.0)
                        print(f"[TX-ARMED] REM_Preftest={tx_val}")
                        continue

                    if sub == "qref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider(ch2, "REM_Qreftest", val, lo=-2.0, hi=2.0)
                        print(f"[TX-ARMED] REM_Qreftest={tx_val}")
                        continue

                    if sub == "block" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector(ch2, "REM_BLOCK", val)
                        print(f"[TX-ARMED] REM_BLOCK={val}")
                        continue

                    if sub == "chkreset" and len(parts) == 3 and parts[2].lower() == "press":
                        pb_pulse(ch2, "REM_CHKRESET")
                        print("[TX-ARMED] REM_CHKRESET pulse")
                        continue

                    if sub == "brk" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector(ch2, "REM_BESSBRK", val)
                        print(f"[TX-ARMED] REM_BESSBRK={val}")
                        continue

                # --- CH3: Diesel generator / governor commands ---
                # Syntax:
                #   gen block 0|1          (REM_BLOCKGEN, int)
                #   gen wref <pu>          (REM_Wref, float, 0..100)
                #   gen pref <pu>          (REM_PREF, float, -100..100)
                #   gen reset press        (REM_RESETGEN, pushbutton pulse)
                if cmd == "gen" and len(parts) >= 2:
                    sub = parts[1].lower()

                    if sub == "block" and len(parts) == 3:
                        val = int(parts[2])
                        set_selector(ch3, "REM_BLOCKGEN", val)
                        print(f"[TX-ARMED] REM_BLOCKGEN={val}")
                        continue

                    if sub == "wref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider(ch3, "REM_Wref", val, lo=0.0, hi=100.0)
                        print(f"[TX-ARMED] REM_Wref={tx_val}")
                        continue

                    if sub == "pref" and len(parts) == 3:
                        val = float(parts[2])
                        tx_val = set_slider(ch3, "REM_PREF", val, lo=-100.0, hi=100.0)
                        print(f"[TX-ARMED] REM_PREF={tx_val}")
                        continue

                    if sub == "reset" and len(parts) == 3 and parts[2].lower() == "press":
                        pb_pulse(ch3, "REM_RESETGEN")
                        print("[TX-ARMED] REM_RESETGEN pulse")
                        continue


                # --- CH4: Load commands ---
                # Syntax:
                #   load p <MW>                     (REM_PLOAD, float, 0..50)
                #   load q <MVAr>                   (REM_QLOAD, float, 0.001..50)
                #   load pq <MW> <MVAr>             (REM_PLOAD, REM_QLOAD)

                if cmd == "load" and len(parts) >= 3:
                    sub = parts[1].lower()

                    if sub == "p" and len(parts) == 3:
                        p = float(parts[2])
                        tx_p = set_slider(ch4, "REM_PLOAD", p, lo=0.0, hi=50.0)
                        print(f"[TX-ARMED] REM_PLOAD={tx_p}")
                        continue

                    if sub == "q" and len(parts) == 3:
                        q = float(parts[2])
                        tx_q = set_slider(ch4, "REM_QLOAD", q, lo=0.001, hi=50.0)
                        print(f"[TX-ARMED] REM_QLOAD={tx_q}")
                        continue

                    if sub == "pq" and len(parts) == 4:
                        p = float(parts[2])
                        q = float(parts[3])
                        tx_p = set_slider(ch4, "REM_PLOAD", p, lo=0.0, hi=50.0)
                        tx_q = set_slider(ch4, "REM_QLOAD", q, lo=0.001, hi=50.0)
                        print(f"[TX-ARMED] REM_PLOAD={tx_p}  REM_QLOAD={tx_q}")
                        continue

                print("[ERROR] Unknown command or wrong syntax. Type 'status' to confirm comms.")

            except ValueError as e:
                print(f"[INPUT ERROR] {e}")
                continue
            except Exception as e:
                print(f"[UNEXPECTED ERROR] {e}")
                continue

    except ValueError as e:
        print(f"[INPUT ERROR] {e}")
    except Exception as e:
        print(f"[UNEXPECTED ERROR] {e}")

    finally:
        ch1.stop()
        ch2.stop()
        ch3.stop()
        ch4.stop()
        print("Stopped.")


if __name__ == "__main__":
    main()

